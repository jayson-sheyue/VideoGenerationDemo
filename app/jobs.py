from __future__ import annotations

import asyncio
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from app import omni_client, prompt_enhancer
from app.config import OUTPUT_DIR, gcs_prefix_for_inputs, settings
from app.gcs_util import (
    mvhd_duration_sec,
    build_input_object_uri,
    guess_ext,
    probe_video_duration,
    save_bytes_locally,
    stage_images_to_gcs,
    upload_local_file_to_gcs,
)
from app.prompt_utils import (
    build_control_prefix,
    dialogue_lock,
    download_image_bytes,
    extract_reference_urls,
    extract_spoken_lines,
    frame_lock_prefix,
    reference_lock_prefix,
    rewrite_image_refs,
)

GENERATE_KINDS = {
    "text_to_video": "text_to_video",
    "image_to_video": "image_to_video",
    "frames_to_video": "image_to_video",
    "reference_to_video": "reference_to_video",
}


# Verified against the API: editing a 16s clip returns
# "Editing duration 16 exceeds maximum duration 10."
EDIT_MAX_INPUT_SEC = 10.0
# Documented extend envelope: 1-30s in, up to 40s out.
EXTEND_MAX_INPUT_SEC = 30.0


class JobStatus(str, Enum):
    queued = "queued"
    downloading = "downloading"
    submitting = "submitting"
    generating = "generating"
    saving = "saving"
    completed = "completed"
    failed = "failed"


@dataclass
class Job:
    id: str
    status: JobStatus = JobStatus.queued
    message: str = "Queued"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    interaction_id: str | None = None
    video_path: str | None = None
    video_uri: str | None = None
    thoughts: str | None = None
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    # Lineage, so the UI can line a clip up against what it was derived from.
    mode: str = "generate"
    source_job_id: str | None = None
    source_video_uri: str | None = None
    duration_sec: float | None = None

    def touch(self, status: JobStatus | None = None, message: str | None = None) -> None:
        if status is not None:
            self.status = status
        if message is not None:
            self.message = message
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        video_url = None
        if self.video_path:
            video_url = f"/api/jobs/{self.id}/video"
        elif self.video_uri and self.video_uri.startswith("gs://"):
            video_url = f"/api/jobs/{self.id}/video"
        elif self.video_uri and self.video_uri.startswith(("http://", "https://")):
            video_url = self.video_uri

        return {
            "id": self.id,
            "status": self.status.value,
            "message": self.message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "interaction_id": self.interaction_id,
            "video_url": video_url,
            "video_uri": self.video_uri,
            "thoughts": self.thoughts,
            "error": self.error,
            "meta": self.meta,
            "mode": self.mode,
            "source_job_id": self.source_job_id,
            "source_video_uri": self.source_video_uri,
            "duration_sec": self.duration_sec,
            "editable": bool(
                self.video_uri
                and self.duration_sec is not None
                and self.duration_sec <= EDIT_MAX_INPUT_SEC
            ),
            "generate_task": self.meta.get("generate_task") or self.meta.get("task"),
            "reuse_ready": _reuse_ready(self),
            "shot_list": self.meta.get("shot_list"),
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()

    async def create(self) -> Job:
        job = Job(id=uuid.uuid4().hex[:12])
        async with self._lock:
            self._jobs[job.id] = job
        return job

    async def get(self, job_id: str) -> Job | None:
        async with self._lock:
            return self._jobs.get(job_id)

    async def list(self, limit: int = 20) -> list[Job]:
        async with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda j: j.created_at,
                reverse=True,
            )
            return jobs[:limit]


job_store = JobStore()


def _reuse_ready(job: Job) -> bool:
    if job.meta.get("gcs_ref_items") or job.meta.get("gcs_refs"):
        return True
    kind = job.meta.get("generate_task") or job.meta.get("task")
    return job.mode == "generate" and kind == "text_to_video" and bool(
        job.meta.get("shot_list")
    )


def resolve_generate_kind(generate_task: str | None, image_count: int) -> tuple[str, str]:
    """Map UI kind → Omni task. Empty kind infers from whether images were sent."""
    kind = (generate_task or "").strip().lower()
    if kind in GENERATE_KINDS:
        return kind, GENERATE_KINDS[kind]
    if image_count <= 0:
        return "text_to_video", "text_to_video"
    return "reference_to_video", "reference_to_video"


async def _submit_and_finalize(job: Job, *, payload: dict[str, Any]) -> None:
    job.touch(JobStatus.submitting, "Submitting to Omni Interactions API…")
    created = await omni_client.create_interaction(payload)
    interaction_id = created.get("id")
    if not interaction_id:
        raise omni_client.OmniAPIError(
            f"No interaction id in response: {created}",
            body=created,
        )
    job.interaction_id = interaction_id
    job.meta["task"] = (
        (payload.get("generation_config") or {}).get("video_config", {}).get("task")
    )

    status = (created.get("status") or "").lower()
    if status == "completed":
        interaction = created
    else:
        job.touch(
            JobStatus.generating,
            f"Generating… (interaction {interaction_id})",
        )
        interaction = await omni_client.poll_until_done(interaction_id)

    if (interaction.get("status") or "").lower() == "failed":
        raise omni_client.OmniAPIError(
            "Omni interaction failed",
            body=interaction,
        )

    job.thoughts = omni_client.extract_thoughts(interaction) or None
    video_bytes, video_uri, _mime = omni_client.extract_video(interaction)
    job.video_uri = video_uri

    use_gcs_playback = (
        settings.video_playback == "gcs_signed"
        and bool(video_uri)
        and video_uri.startswith("gs://")
    )

    if use_gcs_playback:
        job.touch(JobStatus.saving, "Preparing signed playback URL…")
        from app.gcs_util import sign_gcs_read_url

        signed = await asyncio.to_thread(sign_gcs_read_url, video_uri)
        job.meta["playback"] = "gcs_signed"
        job.meta["signed_url_preview"] = signed[:96] + "…"
        job.duration_sec = await asyncio.to_thread(probe_video_duration, video_uri)
        job.touch(JobStatus.completed, _done_message(job))
        return

    job.touch(JobStatus.saving, "Saving video locally…")
    out_path: Path = OUTPUT_DIR / f"{job.id}.mp4"
    if video_bytes is None and video_uri:
        from app.gcs_util import download_gcs_bytes

        if video_uri.startswith("gs://"):
            video_bytes = await asyncio.to_thread(
                download_gcs_bytes,
                video_uri,
                dest=out_path,
            )
        else:
            video_bytes = await omni_client.download_gcs_or_http(video_uri)
            out_path.write_bytes(video_bytes)
    elif video_bytes is not None:
        out_path.write_bytes(video_bytes)
    else:
        raise omni_client.OmniAPIError(
            "Completed but no video bytes/uri found in response",
            body=interaction,
        )

    if not out_path.is_file() or out_path.stat().st_size < 1000:
        raise omni_client.OmniAPIError(
            f"Downloaded video looks empty: {out_path}",
            body={"video_uri": video_uri},
        )

    job.video_path = str(out_path)
    job.meta["playback"] = "local"
    job.duration_sec = mvhd_duration_sec(out_path.read_bytes()[:65536])
    job.touch(JobStatus.completed, _done_message(job))


def _done_message(job: Job) -> str:
    length = f"{job.duration_sec:g}s clip" if job.duration_sec else "Done"
    if job.duration_sec and job.duration_sec > EDIT_MAX_INPUT_SEC:
        return f"Done — {length}; too long to Edit (max {EDIT_MAX_INPUT_SEC:g}s), Extend still works"
    return f"Done — {length}"


def reuse_images_from_job(job: Job) -> list[dict[str, str]]:
    items = job.meta.get("gcs_ref_items") or []
    if items:
        return [
            {
                "uri": item["uri"],
                "mime_type": item.get("mime_type") or _mime_from_uri(item["uri"]),
            }
            for item in items
            if str(item.get("uri", "")).startswith("gs://")
        ]
    return [
        {"uri": uri, "mime_type": _mime_from_uri(uri)}
        for uri in (job.meta.get("gcs_refs") or [])
        if str(uri).startswith("gs://")
    ]


def _persist_gcs_refs(job: Job, images: list[dict[str, str]]) -> None:
    items = [
        {"uri": img["uri"], "mime_type": img.get("mime_type") or "image/png"}
        for img in images
        if img.get("uri")
    ]
    if items:
        job.meta["gcs_ref_items"] = items
        job.meta["gcs_refs"] = [item["uri"] for item in items]


def _mime_from_uri(uri: str) -> str:
    lower = uri.lower()
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    return "image/png"


async def run_generation_job(
    job: Job,
    *,
    prompt: str,
    image_urls: list[str],
    client_images: list[dict[str, str]] | None = None,
    duration: int,
    resolution: str,
    aspect_ratio: str,
    generate_audio: bool,
    no_subtitles: bool,
    no_bgm: bool,
    mode: str = "generate",
    source_video_uri: str | None = None,
    source_job_id: str | None = None,
    enhance_prompt: bool = True,
    reuse_images: list[dict[str, str]] | None = None,
    generate_task: str | None = None,
) -> None:
    try:
        duration_str = f"{duration}s"
        mode = (mode or "generate").strip().lower()
        job.mode = mode
        job.source_job_id = source_job_id
        job.source_video_uri = source_video_uri
        job.meta = {
            "mode": mode,
            "duration": duration_str,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "generate_audio": generate_audio,
            "model": settings.omni_model,
            "generate_task": (generate_task or "").strip().lower() or None,
        }

        if mode in {"continue", "edit", "extend"}:
            if not source_video_uri or not source_video_uri.startswith("gs://"):
                raise omni_client.OmniAPIError(
                    f"{mode} mode needs a gs:// source_video_uri "
                    "(pick a completed job or paste a GCS video URI)"
                )
            # "continue" is an edit chained onto the previous job's output. The
            # Agent Platform path rejects previous_interaction_id for this model,
            # so each turn re-attaches the source video instead.
            api_task = "extend" if mode == "extend" else "edit"

            job.touch(JobStatus.submitting, "Checking source clip length…")
            source_seconds = await asyncio.to_thread(
                probe_video_duration, source_video_uri
            )
            job.meta["source_duration_sec"] = source_seconds
            limit = EDIT_MAX_INPUT_SEC if api_task == "edit" else EXTEND_MAX_INPUT_SEC
            if source_seconds is not None and source_seconds > limit:
                raise omni_client.OmniAPIError(
                    f"Source clip is {source_seconds:g}s but {api_task} accepts at most "
                    f"{limit:g}s. Extended clips grow past the edit limit — "
                    "pick an earlier, shorter clip in the chain."
                )

            edit_prompt = prompt.strip()
            # Edits must stay terse — the docs warn that over-describing an edit
            # changes parts of the frame you never mentioned. Only extend, which
            # is generative, gets the cinematography pass.
            if api_task == "extend" and enhance_prompt:
                job.touch(JobStatus.submitting, "Polishing instruction…")
                polished = await prompt_enhancer.enhance(edit_prompt, kind="extend")
                job.meta["prompt_enhanced"] = polished["enhanced"]
                job.meta["prompt_enhance_reason"] = polished["reason"]
                if polished["enhanced"]:
                    job.meta["prompt_original"] = edit_prompt
                    edit_prompt = polished["prompt"]
            if api_task == "edit" and "keep everything else" not in edit_prompt.lower():
                edit_prompt = (
                    f"{edit_prompt.rstrip('.')}. Keep everything else the same."
                )
            job.meta["source_video_uri"] = source_video_uri
            job.meta["api_task"] = api_task
            job.meta["final_prompt_preview"] = edit_prompt[:4000]
            job.meta["image_count"] = 0
            payload = omni_client.build_payload(
                prompt=edit_prompt,
                images=[],
                duration=duration_str,
                resolution=resolution,
                aspect_ratio=aspect_ratio,
                task=api_task,
                video={"mime_type": "video/mp4", "uri": source_video_uri},
            )
            await _submit_and_finalize(job, payload=payload)
            return

        requested_kind = (generate_task or "").strip().lower() or None
        skip_images = requested_kind == "text_to_video"

        cleaned_prompt, parsed_urls = extract_reference_urls(prompt)
        resolved_urls = [] if skip_images else (image_urls or parsed_urls)
        prompt_body = prompt if skip_images else (cleaned_prompt if parsed_urls else prompt)
        client_images = [] if skip_images else (client_images or [])
        reuse_images = [] if skip_images else (reuse_images or [])

        job.meta.update(
            {
                "image_count": max(len(resolved_urls), len(client_images), len(reuse_images)),
                "parsed_from_prompt": bool(parsed_urls) and not image_urls and not skip_images,
                "client_images": len(client_images),
            }
        )

        use_gcs = bool(gcs_prefix_for_inputs())
        images: list[dict[str, str]] = []

        if reuse_images:
            job.touch(
                JobStatus.submitting,
                f"Reusing {len(reuse_images)} image(s) already on GCS…",
            )
            for item in reuse_images:
                uri = (item.get("uri") or "").strip()
                if not uri.startswith("gs://"):
                    continue
                images.append(
                    {
                        "mime_type": item.get("mime_type") or _mime_from_uri(uri),
                        "uri": uri,
                    }
                )
            if not images:
                raise omni_client.OmniAPIError(
                    "reuse_job_id has no gs:// images to reuse"
                )
            job.meta["image_count"] = len(images)
            job.meta["image_delivery"] = "gcs_reuse"
            _persist_gcs_refs(job, images)
        elif client_images:
            job.touch(
                JobStatus.downloading,
                f"Staging {len(client_images)} browser image(s) "
                f"({'local→GCS' if use_gcs else 'base64'})…",
            )
            if use_gcs:
                staged = await asyncio.to_thread(
                    stage_images_to_gcs,
                    job_id=job.id,
                    items=client_images,
                )
                for item in staged:
                    images.append(
                        {"mime_type": item["mime_type"], "uri": item["uri"]}
                    )
                job.meta["image_delivery"] = "browser_local_gcs"
                job.meta["local_refs"] = [s["local_path"] for s in staged]
                _persist_gcs_refs(job, images)
            else:
                for item in client_images:
                    mime = item.get("mime_type") or "image/png"
                    data_b64 = item["data"]
                    if "," in data_b64 and data_b64.strip().startswith("data:"):
                        data_b64 = data_b64.split(",", 1)[1]
                    images.append({"mime_type": mime, "data": data_b64})
                job.meta["image_delivery"] = "browser_base64"
        elif resolved_urls:
            job.touch(
                JobStatus.downloading,
                f"Fetching {len(resolved_urls)} image(s)…",
            )
            local_refs: list[str] = []
            for idx, url in enumerate(resolved_urls, start=1):
                job.touch(
                    JobStatus.downloading,
                    f"Fetching image {idx}/{len(resolved_urls)}…",
                )
                raw, mime = await download_image_bytes(url)
                if use_gcs:
                    ext = guess_ext(url, mime)
                    local_path = await asyncio.to_thread(
                        save_bytes_locally,
                        raw,
                        job_id=job.id,
                        index=idx,
                        mime_type=mime,
                    )
                    local_refs.append(str(local_path))
                    gcs_uri = build_input_object_uri(job.id, idx, ext)
                    job.touch(
                        JobStatus.downloading,
                        f"Uploading image {idx}/{len(resolved_urls)} to GCS…",
                    )
                    uploaded = await asyncio.to_thread(
                        upload_local_file_to_gcs,
                        local_path,
                        gcs_uri=gcs_uri,
                        mime_type=mime,
                    )
                    images.append({"mime_type": mime, "uri": uploaded})
                else:
                    import base64

                    images.append(
                        {
                            "mime_type": mime,
                            "data": base64.b64encode(raw).decode("utf-8"),
                        }
                    )

            job.meta["image_delivery"] = "gcs" if use_gcs else "base64"
            if use_gcs:
                job.meta["local_refs"] = local_refs
                _persist_gcs_refs(job, images)
        else:
            job.touch(JobStatus.submitting, "No images — text_to_video…")

        kind, api_task = resolve_generate_kind(requested_kind, len(images))
        job.meta["generate_task"] = kind
        job.meta["api_task"] = api_task
        job.meta["image_count"] = len(images)

        if api_task == "text_to_video" and images:
            images = []
            job.meta["image_count"] = 0
            job.meta["images_ignored"] = "text_to_video does not send stills"
        elif kind == "frames_to_video" and len(images) != 2:
            raise omni_client.OmniAPIError(
                "First + last frame needs exactly two images (first, then last)"
            )
        elif api_task == "image_to_video" and not (1 <= len(images) <= 2):
            raise omni_client.OmniAPIError(
                "image_to_video needs 1 still (first frame) or 2 stills (first + last)"
            )
        elif api_task == "reference_to_video" and not images:
            raise omni_client.OmniAPIError(
                "reference_to_video needs at least one reference image "
                "(upload a file, paste a URL, or pick Load example)"
            )

        rewritten = rewrite_image_refs(prompt_body, len(images))
        spoken = extract_spoken_lines(prompt_body)
        job.meta["shot_list"] = prompt_body
        speech = dialogue_lock(prompt_body)
        control = build_control_prefix(
            duration=duration_str,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            generate_audio=generate_audio,
            no_subtitles=no_subtitles,
            no_bgm=no_bgm,
            spoken_lines=spoken,
        )

        motion_note = ""
        lock = ""
        extra = ""
        if api_task == "text_to_video":
            body_text = rewritten
            if enhance_prompt:
                job.touch(JobStatus.submitting, "Polishing shot list…")
                polished = await prompt_enhancer.enhance(rewritten, kind="generate")
                job.meta["prompt_enhanced"] = polished["enhanced"]
                job.meta["prompt_enhance_reason"] = polished["reason"]
                if polished["enhanced"]:
                    job.meta["prompt_original"] = rewritten
                    body_text = polished["prompt"]
            else:
                job.meta["prompt_enhanced"] = False
                job.meta["prompt_enhance_reason"] = "disabled"
            parts = [control, speech, body_text]
        else:
            if enhance_prompt:
                job.touch(JobStatus.submitting, "Writing motion notes…")
                mot = await prompt_enhancer.motion_addendum(rewritten)
                job.meta["prompt_enhanced"] = mot["enhanced"]
                job.meta["prompt_enhance_reason"] = mot["reason"]
                motion_note = mot["prompt"]
            else:
                job.meta["prompt_enhanced"] = False
                job.meta["prompt_enhance_reason"] = "static motion note"
                motion_note = prompt_enhancer.DEFAULT_MOTION_NOTE
            if api_task == "image_to_video":
                lock = frame_lock_prefix(len(images))
            else:
                lock = reference_lock_prefix(len(images))
                extra = (
                    "Use the given image(s) as references for video generation. "
                    "The images should not be used as literal initial frames."
                )
            parts = [control]
            if lock:
                parts.append(lock)
            parts.extend([speech, rewritten, motion_note])
            if extra:
                parts.append(extra)

        final_prompt = "\n\n".join(p for p in parts if p)
        job.meta["final_prompt_preview"] = final_prompt[:4000]

        payload = omni_client.build_payload(
            prompt=final_prompt,
            images=images,
            duration=duration_str,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            task=api_task,
        )
        await _submit_and_finalize(job, payload=payload)
    except Exception as exc:
        job.error = f"{exc}\n{traceback.format_exc()}"
        job.touch(JobStatus.failed, str(exc))
