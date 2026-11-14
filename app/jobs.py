from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from app import omni_client, prompt_enhancer
from app.audio_direction import AudioPreferences, compose_media_prompt
from app.workspace import workspace_id, ConflictError
from app.config import OUTPUT_DIR, gcs_prefix_for_inputs, settings
from app.model_catalog import ModelProfile, profile_for, upscale_targets
from app.gcs_util import (
    mvhd_duration_sec,
    build_input_object_uri,
    ensure_omni_source_mp4,
    guess_ext,
    probe_video_duration,
    probe_video_resolution,
    save_bytes_locally,
    stage_images_to_gcs,
    upload_local_file_to_gcs,
)
from app.prompt_utils import (
    download_image_bytes,
    extract_reference_urls,
    frame_lock_prefix,
    reference_lock_prefix,
    rewrite_image_refs,
    rewrite_video_refs,
)

GENERATE_KINDS = {
    "text_to_video": "text_to_video",
    "image_to_video": "image_to_video",
    "frames_to_video": "image_to_video",
    "reference_to_video": "reference_to_video",
}


# 1.1 rejects edits above 10s ("Editing duration N exceeds maximum duration 10").
# 1.2's model card raises that ceiling to 15s. Callers should use the job's model.
EDIT_MAX_INPUT_SEC = 10.0
# 1.1 extend input cap. 1.2 accepts any length and keeps up to the last 30s.
EXTEND_MAX_INPUT_SEC = 30.0


class JobStatus(str, Enum):
    queued = "queued"
    downloading = "downloading"
    submitting = "submitting"
    generating = "generating"
    saving = "saving"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"


@dataclass
class Job:
    id: str
    owner: str = "local"
    library: dict[str, Any] = field(default_factory=dict)
    request: dict[str, Any] = field(default_factory=dict)
    request_hash: str = ""
    events: list[dict[str, str]] = field(default_factory=list)
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
        if status is not None or message is not None:
            self.events.append({"at": self.updated_at, "status": self.status.value, "message": self.message})
            self.events = self.events[-100:]

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
            "library": self.library,
            "request": self.request,
            "events": self.events,
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
            # A task that has only just been queued is described by its request.
            "model": self.meta.get("model") or self.request.get("model"),
            "edit_max_sec": _edit_limit(self),
            "upscale_max_sec": _upscale_limit(self),
            "output_resolution": self.meta.get("output_resolution"),
            "editable": bool(
                self.video_uri
                and self.duration_sec is not None
                and self.duration_sec <= _edit_limit(self) + 0.05
            ),
            "upscalable": _job_upscalable(self),
            "generate_task": self.meta.get("generate_task")
            or self.meta.get("task")
            or (self.request.get("generate_task") if self.request.get("mode") == "generate" else None),
            "reuse_ready": _reuse_ready(self),
            "shot_list": self.meta.get("shot_list"),
        }


class JobStore:
    def __init__(self, repository=None) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()
        self.repository = repository
        self.loaded: set[str] = set()
        self.running: dict[str, asyncio.Task] = {}
        self.saved: dict[str, str] = {}

    async def load(self) -> None:
        owner = workspace_id.get()
        if not self.repository or owner in self.loaded:
            return
        async with self._lock:
            if owner in self.loaded:
                return
            records = await asyncio.to_thread(self.repository.list, f"{owner}/jobs/")
            libraries = await asyncio.to_thread(self.repository.list, f"{owner}/library/")
            by_id = {item["id"]: item for item in libraries}
            for record in records:
                record["status"] = JobStatus(record["status"])
                record["library"] = by_id.get(record["id"], {})
                job = Job(**record)
                self._jobs[job.id] = job
                # What was just read is already stored; only later changes are written.
                self.saved[job.id] = self.fingerprint(job)
            self.loaded.add(owner)
        # Recovery never repeats create_interaction. Only poll a known ID.
        for job in list(self._jobs.values()):
            if job.owner == owner and job.status not in TERMINAL:
                if job.interaction_id:
                    self.track(job, resume_job(job))
                else:
                    job.touch(JobStatus.interrupted, "Execution was interrupted before an interaction ID was saved. Check before retrying; submission may have reached the API.")
                    await self.save(job)

    async def create(self, *, job_id=None, request_hash="", request=None) -> Job:
        await self.load()
        job = Job(id=job_id or uuid.uuid4().hex, owner=workspace_id.get(), request=request or {}, request_hash=request_hash)
        async with self._lock:
            if job.id in self._jobs:
                existing = self._jobs[job.id]
                if existing.owner != job.owner or existing.request_hash != request_hash:
                    raise ConflictError("Request identifier already used with different inputs")
                return existing
            if sum(j.status not in TERMINAL for j in self._jobs.values()) >= settings.max_active_jobs:
                from fastapi import HTTPException
                raise HTTPException(429, "Generation capacity is busy. Wait for a running task to finish.")
            if self.repository:
                try:
                    await asyncio.to_thread(self.repository.write, f"{job.owner}/jobs/{job.id}.json", self.record(job), "0")
                except ConflictError:
                    raw, _ = await asyncio.to_thread(self.repository.read, f"{job.owner}/jobs/{job.id}.json")
                    raw["status"] = JobStatus(raw["status"])
                    existing = Job(**raw)
                    if existing.request_hash != request_hash:
                        raise ConflictError("Request identifier already used with different inputs")
                    self._jobs[job.id] = existing
                    return existing
            self._jobs[job.id] = job
            job._created_here = True
            if self.repository:
                self.saved[job.id] = self.fingerprint(job)
        return job

    def fingerprint(self, job) -> str:
        import json
        return json.dumps(self.record(job), sort_keys=True)

    def record(self, job):
        raw = asdict(job)
        raw["status"] = job.status.value
        raw["library"] = {}  # User edits live in a separately versioned record.
        return raw

    async def save(self, job):
        if not self.repository:
            return
        async with self._lock:
            record = self.record(job)
            fingerprint = self.fingerprint(job)
            if self.saved.get(job.id) == fingerprint:
                return
            await asyncio.to_thread(self.repository.write, f"{job.owner}/jobs/{job.id}.json", record)
            self.saved[job.id] = fingerprint

    def track(self, job, coroutine):
        async def run():
            try:
                await coroutine
            finally:
                try:
                    await self.save(job)
                finally:
                    self.running.pop(job.id, None)
        task = asyncio.create_task(run())
        self.running[job.id] = task
        return task

    async def get(self, job_id: str) -> Job | None:
        await self.load()
        async with self._lock:
            job = self._jobs.get(job_id)
            return job if job and job.owner == workspace_id.get() else None

    async def list(self, limit: int = 500) -> list[Job]:
        await self.load()
        async with self._lock:
            return sorted((j for j in self._jobs.values() if j.owner == workspace_id.get()), key=lambda j: j.created_at, reverse=True)[:limit]

    async def update_library(self, job, changes, expected):
        async with self._lock:
            key = f"{job.owner}/library/{job.id}.json"
            if self.repository:
                current, generation = await asyncio.to_thread(self.repository.read, key)
            else:
                current, generation = job.library, "0"
            current = current or {}
            version = int(current.get("version", 0))
            if expected != version:
                job.library = current
                raise ConflictError("This work changed in another window. Refresh and try again.")
            updated = {**current, **changes, "id": job.id, "version": version + 1}
            if self.repository:
                await asyncio.to_thread(self.repository.write, key, updated, generation)
            job.library = updated
        return job


TERMINAL = {JobStatus.completed, JobStatus.failed, JobStatus.interrupted}


job_store = JobStore()


def _edit_limit(job: Job) -> float:
    return float(profile_for(job.meta.get("model")).edit_max)


def _upscale_limit(job: Job) -> float | None:
    return profile_for(job.meta.get("model")).upscale_max


def _job_upscalable(job: Job) -> bool:
    limit = _upscale_limit(job)
    if not job.video_uri or limit is None:
        return False
    if job.duration_sec is not None and job.duration_sec > limit + 0.05:
        return False
    targets = upscale_targets(job.meta.get("output_resolution"))
    if targets is not None and not targets:
        return False
    return True


def reuse_videos_from_job(job: Job) -> list[dict[str, str]]:
    return [
        {
            "uri": item["uri"],
            "mime_type": item.get("mime_type") or _mime_from_uri(item["uri"]),
        }
        for item in (job.meta.get("gcs_video_items") or [])
        if str(item.get("uri", "")).startswith("gs://")
    ]


def interaction_can_chain(meta: dict | None) -> bool:
    """Upscale interactions are terminal. Later edit/extend must resend the mp4."""
    meta = meta or {}
    mode = str(meta.get("mode") or "").lower()
    task = str(meta.get("api_task") or meta.get("task") or "").lower()
    return mode != "upscale" and task != "upscale"


def followup_chain_id(
    *,
    mode: str,
    stateful: bool,
    supports_multiturn: bool,
    source_interaction_id: str | None,
    source_meta: dict | None,
    source_model: str | None,
    requested_model: str,
    requested_video_uri: str | None,
    source_video_uri: str | None,
) -> str | None:
    """Only Continue / Edit may continue a stored interaction.

    Extend must send task=extend plus the clip. Upscale must send task=upscale
    plus the clip. The live API rejects previous_interaction_id together with
    either task. A pasted gs:// that is not this job's clip is a new edit.
    """
    if mode not in {"continue", "edit"}:
        return None
    if not stateful or not supports_multiturn or not source_interaction_id:
        return None
    if not interaction_can_chain(source_meta):
        return None
    if source_model and source_model != requested_model:
        return None
    requested = (requested_video_uri or "").strip()
    stored = (source_video_uri or "").strip()
    if requested and stored and requested != stored:
        return None
    return source_interaction_id


def _reuse_ready(job: Job) -> bool:
    """Regenerate only replays a generate job. Edit refs are not a new generation."""
    if job.mode != "generate":
        return False
    if (
        job.meta.get("gcs_ref_items")
        or job.meta.get("gcs_refs")
        or job.meta.get("gcs_video_items")
    ):
        return True
    kind = job.meta.get("generate_task") or job.meta.get("task")
    return kind == "text_to_video" and bool(job.meta.get("shot_list"))


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
    job.meta["request_payload"] = {**payload, "input": [{k: v for k, v in item.items() if k != "data"} for item in payload.get("input", [])]}
    await job_store.save(job)
    created = await omni_client.create_interaction(payload)
    interaction_id = created.get("id")
    if not interaction_id:
        raise omni_client.OmniAPIError(
            f"No interaction id in response: {created}",
            body=created,
        )
    job.interaction_id = interaction_id
    sent_task = (
        (payload.get("generation_config") or {}).get("video_config") or {}
    ).get("task")
    job.meta["task_sent"] = sent_task
    # Keep the operation name when a follow-up edit omits video_config.task.
    # Clearing it made the next regenerate inherit task=null / "edit".
    if sent_task:
        job.meta["task"] = sent_task
        job.meta.pop("task_omitted", None)
    else:
        job.meta["task"] = job.meta.get("api_task")
        job.meta["task_omitted"] = bool(payload.get("previous_interaction_id"))

    await job_store.save(job)
    await _finalize_interaction(job, created)


async def resume_job(job):
    try:
        job.touch(JobStatus.generating, "Restoring status from the original interaction…")
        await _finalize_interaction(job, await omni_client.get_interaction(job.interaction_id))
    except Exception as exc:
        job.error = str(exc)
        job.touch(JobStatus.interrupted, "Could not recover interaction status. Refresh status to retry without generating again.")


async def _finalize_interaction(job, created):
    status = (created.get("status") or "").lower()
    if status == "completed":
        interaction = created
    else:
        job.touch(
            JobStatus.generating,
            f"Generating… (interaction {job.interaction_id})",
        )
        interaction = await omni_client.poll_until_done(job.interaction_id)

    if (interaction.get("status") or "").lower() in {"failed", "cancelled", "canceled"}:
        raise omni_client.OmniAPIError(
            omni_client.format_interaction_failure(interaction),
            body=interaction,
        )

    job.thoughts = omni_client.extract_thoughts(interaction) or None
    job.meta["usage"] = interaction.get("usage")
    video_bytes, video_uri, _mime = omni_client.extract_video(interaction)
    job.video_uri = video_uri

    use_gcs_playback = (
        settings.video_playback == "gcs_signed"
        and bool(video_uri)
        and video_uri.startswith("gs://")
    )

    if use_gcs_playback:
        job.touch(JobStatus.saving, "Preparing signed playback URL…")
        job.meta["playback"] = "gcs_signed"
        # Playback signing is recoverable independently of paid generation.
        # A transient signing failure must not turn a generated work into a failure.
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
    limit = _edit_limit(job)
    if job.duration_sec and job.duration_sec > limit + 0.05:
        return (
            f"Done — {length}; too long to Edit (max {limit:g}s for this model), "
            "Extend still works"
        )
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
    lower = uri.lower().split("?", 1)[0]
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".mp4"):
        return "video/mp4"
    if lower.endswith(".webm"):
        return "video/webm"
    if lower.endswith(".mov"):
        return "video/quicktime"
    return "image/png"


MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = 40 * 1024 * 1024


def _strip_data_url(data_b64: str) -> str:
    data_b64 = (data_b64 or "").strip()
    if "," in data_b64 and data_b64.startswith("data:"):
        return data_b64.split(",", 1)[1]
    return data_b64


def _check_media_size(raw: bytes, *, kind: str, index: int) -> None:
    limit = MAX_VIDEO_BYTES if kind == "video" else MAX_IMAGE_BYTES
    if len(raw) > limit:
        raise omni_client.OmniAPIError(
            f"{kind} {index} is {len(raw) / (1024 * 1024):.1f} MB; "
            f"this demo accepts up to {limit // (1024 * 1024)} MB"
        )


async def _materialize_client_images(items: list[dict[str, str]]) -> list[dict[str, str]]:
    """Download http(s) stills on the server. The browser often cannot read them."""
    import base64

    ready: list[dict[str, str]] = []
    for index, item in enumerate(items, start=1):
        url = (item.get("url") or "").strip()
        if url.startswith(("http://", "https://")):
            raw, mime = await download_image_bytes(url)
            _check_media_size(raw, kind="image", index=index)
            ready.append(
                {
                    "mime_type": mime or item.get("mime_type") or "image/jpeg",
                    "data": base64.b64encode(raw).decode("ascii"),
                }
            )
            continue
        ready.append(item)
    return ready


async def _stage_uploads(
    job: Job,
    items: list[dict[str, str]],
    *,
    kind: str,
    stem: str,
) -> list[dict[str, str]]:
    """Turn browser base64 or existing gs:// URIs into Omni media entries."""
    import base64

    use_gcs = bool(gcs_prefix_for_inputs())
    staged: list[dict[str, str]] = []
    default_mime = "video/mp4" if kind == "video" else "image/png"
    for idx, item in enumerate(items, start=1):
        uri = (item.get("uri") or "").strip()
        if uri.startswith("gs://"):
            staged.append(
                {"mime_type": item.get("mime_type") or _mime_from_uri(uri), "uri": uri}
            )
            continue
        if not item.get("data"):
            raise omni_client.OmniAPIError(f"{kind} {idx} needs base64 data or a gs:// uri")
        data_b64 = _strip_data_url(item["data"])
        raw = base64.b64decode(data_b64)
        _check_media_size(raw, kind=kind, index=idx)
        mime = item.get("mime_type") or default_mime
        if use_gcs:
            local_path = await asyncio.to_thread(
                save_bytes_locally,
                raw,
                job_id=job.id,
                index=idx,
                mime_type=mime,
            )
            gcs_uri = build_input_object_uri(
                job.id, idx, guess_ext(uri or local_path.name, mime), stem=stem
            )
            uploaded = await asyncio.to_thread(
                upload_local_file_to_gcs,
                local_path,
                gcs_uri=gcs_uri,
                mime_type=mime,
            )
            staged.append({"mime_type": mime, "uri": uploaded})
        else:
            staged.append({"mime_type": mime, "data": data_b64})
    return staged


def _state_flags(profile: ModelProfile) -> dict[str, bool]:
    if not profile.supports_multiturn:
        return {}
    # store keeps the interaction for 7 days so the next turn can pass
    # previous_interaction_id. background returns an id immediately; we poll.
    return {"store": True, "background": True}


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
    audio_preferences: AudioPreferences | None = None,
    mode: str = "generate",
    source_video_uri: str | None = None,
    source_job_id: str | None = None,
    enhance_prompt: bool = False,
    reuse_images: list[dict[str, str]] | None = None,
    reuse_videos: list[dict[str, str]] | None = None,
    client_videos: list[dict[str, str]] | None = None,
    generate_task: str | None = None,
    model: str | None = None,
    seed: int | None = None,
    labels: dict[str, str] | None = None,
    stateful: bool = False,
    previous_interaction_id: str | None = None,
    profile_assumed: bool = False,
) -> None:
    try:
        model_id, profile, assumed = (
            model or settings.omni_model,
            profile_for(model or settings.omni_model),
            profile_assumed,
        )
        preferences = audio_preferences or AudioPreferences(
            speech=generate_audio, music=generate_audio and not no_bgm, effects=generate_audio
        )
        duration_str = f"{duration}s"
        mode = (mode or "generate").strip().lower()
        job.mode = mode
        job.source_job_id = source_job_id
        job.source_video_uri = source_video_uri
        state = _state_flags(profile)
        job.meta = {
            "mode": mode,
            "duration": duration_str,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "audio_preferences": preferences.model_dump() if mode in {"generate", "extend"} else None,
            "audio_policy": "original-prompt-first-v1" if mode in {"generate", "extend"} else None,
            "generate_audio": any(preferences.model_dump().values()),
            "no_subtitles": no_subtitles,
            "no_bgm": not preferences.music,
            "enhance_prompt": enhance_prompt,
            "prompt_original": prompt,
            "model": model_id,
            "profile_assumed": assumed,
            "stateful": bool(state),
            "seed": seed,
            "labels": labels or None,
            "generate_task": (generate_task or "").strip().lower() or None,
            # Extend keeps the source resolution; the form value is not sent.
            "output_resolution": None if mode == "extend" else resolution,
        }

        if mode in {"continue", "edit", "extend", "upscale"}:
            api_task = {"extend": "extend", "upscale": "upscale"}.get(mode, "edit")
            # Extend/upscale require their own task, which cannot share
            # previous_interaction_id. Only an edit follow-up chains.
            chain_id = (
                previous_interaction_id
                if api_task == "edit" and stateful and profile.supports_multiturn
                else None
            )
            source_job = await job_store.get(source_job_id) if source_job_id else None
            if source_job and source_video_uri and source_video_uri != source_job.video_uri:
                source_job = None
            if api_task == "extend" and source_job:
                inherited = source_job.meta.get("output_resolution")
                if inherited:
                    job.meta["output_resolution"] = inherited
            if api_task == "upscale":
                chain_id = None
                if profile.upscale_max is None:
                    raise omni_client.OmniAPIError(
                        f"{model_id} has no standalone upscale in this demo. "
                        "Switch to gemini-omni-1.2-flash."
                    )
                source_res = (
                    source_job.meta.get("output_resolution") if source_job else None
                )
                if not source_res and source_video_uri and source_video_uri.startswith("gs://"):
                    source_res = await asyncio.to_thread(
                        probe_video_resolution, source_video_uri
                    )
                if source_res:
                    job.meta["source_resolution"] = source_res
                    allowed = upscale_targets(source_res)
                    if allowed is not None and resolution not in allowed:
                        legal = ", ".join(allowed) if allowed else "none"
                        raise omni_client.OmniAPIError(
                            f"Cannot upscale a {source_res} clip to {resolution}. "
                            f"Official targets from {source_res}: {legal}. "
                            "360p → 720p or 1080p; 720p → 1080p or 4k; "
                            "1080p → 4k is not supported."
                        )
            if not chain_id and (
                not source_video_uri or not source_video_uri.startswith("gs://")
            ):
                raise omni_client.OmniAPIError(
                    f"{mode} needs a gs:// source video, or a stored Omni 1.2 "
                    "interaction to continue (多轮编辑)"
                )

            source_seconds = None
            # Remux fragmented stock MP4s only (container hygiene). Do not trim
            # extend inputs — official 1.2 contract is any source duration.
            if (
                source_video_uri
                and source_video_uri.startswith("gs://")
                and not chain_id
            ):
                job.touch(
                    JobStatus.submitting,
                    "Checking source clip (remux fragmented MP4 if needed)…",
                )
                source_video_uri, source_seconds, source_prep_meta = (
                    await asyncio.to_thread(
                        ensure_omni_source_mp4,
                        source_video_uri,
                        job_id=job.id,
                    )
                )
                job.source_video_uri = source_video_uri
                job.meta.update(source_prep_meta)
                job.meta["source_duration_sec"] = source_seconds
            elif source_video_uri and source_video_uri.startswith("gs://"):
                job.touch(JobStatus.submitting, "Checking source clip length…")
                source_seconds = await asyncio.to_thread(
                    probe_video_duration, source_video_uri
                )
                job.meta["source_duration_sec"] = source_seconds

            if api_task == "edit":
                limit = profile.edit_max
            elif api_task == "upscale":
                limit = profile.upscale_max or 0
            else:
                limit = profile.extend_input_max
            if (
                limit is not None
                and source_seconds is not None
                and source_seconds > limit + 0.05
            ):
                raise omni_client.OmniAPIError(
                    f"Source clip is {source_seconds:g}s but {api_task} on {model_id} "
                    f"accepts at most {limit:g}s."
                )
            if (
                api_task == "extend"
                and source_seconds is not None
                and source_seconds > 30
            ):
                job.meta["extend_note"] = (
                    "Official contract: any input length. Model uses the last ~10s "
                    "as context and keeps up to the last ~30s in the output "
                    "(about ≤40s out)."
                )

            edit_images: list[dict[str, str]] = []
            if client_images and api_task in {"edit", "extend"}:
                cap = profile.edit_reference_images_max
                if cap <= 0:
                    raise omni_client.OmniAPIError(
                        f"{model_id} does not take reference images on {api_task}"
                    )
                if len(client_images) > cap:
                    raise omni_client.OmniAPIError(
                        f"{api_task} accepts at most {cap} reference images"
                    )
                job.touch(
                    JobStatus.downloading,
                    f"Staging {len(client_images)} edit reference image(s)…",
                )
                client_images = await _materialize_client_images(client_images)
                edit_images = await _stage_uploads(
                    job, client_images, kind="image", stem="editref"
                )
                _persist_gcs_refs(job, edit_images)

            edit_prompt = "" if api_task == "upscale" else prompt.strip()
            if api_task == "extend" and enhance_prompt:
                job.touch(JobStatus.submitting, "Polishing instruction…")
                polished = await prompt_enhancer.enhance(edit_prompt, kind="extend")
                job.meta["prompt_enhanced"] = polished["enhanced"]
                job.meta["prompt_enhance_reason"] = polished["reason"]
                if polished["enhanced"]:
                    job.meta["prompt_original"] = edit_prompt
                    edit_prompt = polished["prompt"]
            if api_task == "edit" and enhance_prompt and "keep everything else" not in edit_prompt.lower():
                edit_prompt = (
                    f"{edit_prompt.rstrip('.')}. Keep everything else the same."
                )
            if api_task == "extend":
                edit_prompt = compose_media_prompt(
                    prompt.strip(), preferences=preferences, no_subtitles=no_subtitles,
                    visual_guidance=edit_prompt if edit_prompt != prompt.strip() else "", extend=True,
                )
            if edit_images:
                edit_prompt = rewrite_image_refs(edit_prompt, len(edit_images))
            job.meta["source_video_uri"] = source_video_uri
            job.meta["api_task"] = api_task
            job.meta["previous_interaction_id"] = chain_id
            job.meta["final_prompt_preview"] = edit_prompt
            if api_task == "upscale":
                job.meta["upscale_note"] = (
                    "No text was sent. Upscale takes the source video and a higher resolution only."
                )
            job.meta["image_count"] = len(edit_images)
            payload = omni_client.build_payload(
                prompt=edit_prompt,
                images=edit_images,
                duration=duration_str,
                resolution=resolution,
                aspect_ratio=aspect_ratio,
                task=api_task,
                video=(
                    {"mime_type": "video/mp4", "uri": source_video_uri}
                    if source_video_uri and not chain_id
                    else None
                ),
                model=model_id,
                seed=seed,
                labels=labels,
                previous_interaction_id=chain_id,
                **state,
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
            client_images = await _materialize_client_images(client_images)
            job.touch(
                JobStatus.downloading,
                f"Staging {len(client_images)} browser image(s) "
                f"({'local→GCS' if use_gcs else 'base64'})…",
            )
            images = await _stage_uploads(job, client_images, kind="image", stem="ref")
            job.meta["image_delivery"] = "gcs" if use_gcs else "base64"
            _persist_gcs_refs(job, images)
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

        videos: list[dict[str, str]] = []
        if requested_kind == "reference_to_video":
            incoming_videos = list(client_videos or [])
            if not incoming_videos and reuse_videos:
                incoming_videos = list(reuse_videos)
            if incoming_videos:
                job.touch(
                    JobStatus.downloading,
                    f"Staging {len(incoming_videos)} reference video(s)…",
                )
                videos = await _stage_uploads(
                    job, incoming_videos, kind="video", stem="vid"
                )
                persisted = [
                    {"uri": item["uri"], "mime_type": item.get("mime_type") or "video/mp4"}
                    for item in videos
                    if item.get("uri")
                ]
                if persisted:
                    job.meta["gcs_video_items"] = persisted

        kind, api_task = resolve_generate_kind(
            requested_kind, len(images) + len(videos)
        )
        job.meta["generate_task"] = kind
        job.meta["api_task"] = api_task
        job.meta["image_count"] = len(images)
        job.meta["video_count"] = len(videos)

        if api_task == "text_to_video" and images:
            images = []
            videos = []
            job.meta["image_count"] = 0
            job.meta["video_count"] = 0
            job.meta["images_ignored"] = "text_to_video does not send stills"
        elif kind == "frames_to_video" and len(images) != 2:
            raise omni_client.OmniAPIError(
                "First + last frame needs exactly two images (first, then last)"
            )
        elif api_task == "image_to_video" and not (1 <= len(images) <= 2):
            raise omni_client.OmniAPIError(
                "image_to_video needs 1 still (first frame) or 2 stills (first + last)"
            )
        elif api_task == "reference_to_video" and not images and not videos:
            raise omni_client.OmniAPIError(
                "reference_to_video needs at least one reference image or video"
            )
        if len(images) > profile.reference_images_max:
            raise omni_client.OmniAPIError(
                f"at most {profile.reference_images_max} images per prompt"
            )
        if len(videos) > profile.reference_videos_max:
            raise omni_client.OmniAPIError(
                f"at most {profile.reference_videos_max} reference videos per prompt"
            )

        rewritten = rewrite_video_refs(rewrite_image_refs(prompt_body, len(images)))
        job.meta["shot_list"] = prompt_body
        visual_guidance = ""
        if not enhance_prompt:
            job.meta["prompt_enhanced"] = False
            job.meta["prompt_enhance_reason"] = "disabled"
        elif api_task == "text_to_video":
            job.touch(JobStatus.submitting, "Polishing shot list…")
            polished = await prompt_enhancer.enhance(rewritten, kind="generate")
            job.meta["prompt_enhanced"] = polished["enhanced"]
            job.meta["prompt_enhance_reason"] = polished["reason"]
            if polished["enhanced"]:
                visual_guidance = polished["prompt"]
        else:
            job.touch(JobStatus.submitting, "Writing motion notes…")
            mot = await prompt_enhancer.motion_addendum(rewritten)
            job.meta["prompt_enhanced"] = mot["enhanced"]
            job.meta["prompt_enhance_reason"] = mot["reason"]
            lock = (frame_lock_prefix(len(images)) if api_task == "image_to_video"
                    else reference_lock_prefix(len(images), len(videos)))
            visual_guidance = "\n\n".join(p for p in (lock, mot["prompt"]) if p)

        final_prompt = compose_media_prompt(
            rewritten, preferences=preferences, no_subtitles=no_subtitles,
            visual_guidance=visual_guidance,
        )

        job.meta["final_prompt_preview"] = final_prompt

        payload = omni_client.build_payload(
            prompt=final_prompt,
            images=images,
            videos=videos,
            duration=duration_str,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            task=api_task,
            model=model_id,
            seed=seed,
            labels=labels,
            **state,
        )
        await _submit_and_finalize(job, payload=payload)
    except Exception as exc:
        job.error = str(exc)
        job.meta["error_kind"] = "api" if isinstance(exc, omni_client.OmniAPIError) else "application"
        unknown = bool(job.interaction_id) and not (isinstance(exc, omni_client.OmniAPIError) and isinstance(exc.body, dict) and exc.body.get("status") in {"failed", "canceled", "cancelled"})
        job.touch(JobStatus.interrupted if unknown else JobStatus.failed, str(exc))
    finally:
        await job_store.save(job)
