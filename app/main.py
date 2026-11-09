from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl, model_validator

from app.config import (
    ACTIVE_PROXY,
    IS_CLOUD_RUN,
    OUTPUT_DIR,
    ROOT_DIR,
    gcs_prefix_for_inputs,
    settings,
)
from app.gcs_util import sign_gcs_read_url, stage_images_to_gcs
from app import prompt_enhancer
from app.jobs import (
    JobStatus,
    job_store,
    reuse_images_from_job,
    reuse_videos_from_job,
    followup_chain_id,
    run_generation_job,
)
from app.model_catalog import (
    CATALOG,
    SEED_MAX,
    duration_bounds,
    normalize_labels,
    profile_for,
    resolve_model,
    upscale_targets,
)
from app.omni_client import OmniAPIError
from app import omni_client
from app.prompt_utils import (
    EXAMPLE_IMAGES,
    EXAMPLE_PROMPT,
    download_image_as_base64,
    examples_payload,
    extract_reference_urls,
)

app = FastAPI(
    title="Omni Flash Video Lab",
    version="0.2.0",
    docs_url="/api/openapi",
    redoc_url=None,
)

STATIC_DIR = ROOT_DIR / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ReferenceImage(BaseModel):
    """A still the browser already read, or an http(s) URL the server downloads.

    Public image hosts often block browser fetches. Edit and generate send the
    URL in this JSON body instead of reading the file in the page.
    """

    mime_type: str = "image/png"
    data: str | None = None
    url: str | None = None

    @model_validator(mode="after")
    def one_source(self):
        has_data = bool(self.data and len(self.data) >= 8)
        has_url = bool(self.url and self.url.startswith(("http://", "https://")))
        if has_data == has_url:
            raise ValueError("reference image needs either base64 data or an http(s) url")
        return self


class GcsUploadTestRequest(BaseModel):
    reference_images: list[ReferenceImage] = Field(..., min_length=1)


class MediaRef(BaseModel):
    mime_type: str = "video/mp4"
    data: str | None = None
    uri: str | None = None


class GenerateRequest(BaseModel):
    prompt: str = Field(default="")
    image_urls: list[HttpUrl] = Field(default_factory=list)
    # Browser-fetched images — preferred when server cannot reach OSS DNS.
    reference_images: list[ReferenceImage] = Field(default_factory=list)
    # Reference clips for reference_to_video. data is base64, or uri is gs://.
    reference_videos: list[MediaRef] = Field(default_factory=list)
    duration: int = Field(default=8, ge=3, le=15)
    resolution: str = Field(default="720p")
    aspect_ratio: str = Field(default="16:9")
    generate_audio: bool = True
    no_subtitles: bool = True
    no_bgm: bool = True
    # generate | continue | edit | extend | upscale
    mode: str = Field(default="generate")
    # Omni generate task: text_to_video | image_to_video | frames_to_video | reference_to_video
    generate_task: str | None = None
    enhance_prompt: bool = False
    # Empty uses OMNI_MODEL. A known id or any custom model id is accepted.
    model: str | None = None
    seed: int | None = Field(default=None, ge=0, le=SEED_MAX)
    labels: dict[str, str] = Field(default_factory=dict)
    # Omni 1.2: continue from the stored interaction instead of re-uploading.
    stateful: bool = True
    # For continue/edit/extend/upscale: gs://…mp4 from a prior job (or any GCS video)
    source_video_uri: str | None = None
    source_job_id: str | None = None
    # Generate again using GCS refs already uploaded for this job.
    reuse_job_id: str | None = None


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/docs")
async def docs_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "docs.html")


@app.get("/api/health")
async def health() -> dict:
    default_id, _profile, _assumed = resolve_model(None)
    return {
        "ok": True,
        "project_configured": bool(settings.google_cloud_project),
        "model": default_id,
        "models": [profile.to_public() for profile in CATALOG.values()],
        "input_gcs_uri": settings.input_gcs_uri or None,
        "output_gcs_uri": settings.output_gcs_uri or None,
        "image_delivery": "gcs" if (settings.input_gcs_uri or settings.output_gcs_uri) else "base64",
        "active_proxy": ACTIVE_PROXY,
        "google_api_trust_env": settings.google_api_trust_env,
        "video_playback": settings.video_playback,
        "runtime": "cloud_run" if IS_CLOUD_RUN else "local",
        "prompt_enhance": settings.prompt_enhance,
        "prompt_enhance_model": settings.prompt_enhance_model,
        "docs": "/docs",
    }


@app.get("/api/example")
async def example() -> dict:
    return {
        "prompt": EXAMPLE_PROMPT,
        "image_urls": EXAMPLE_IMAGES,
        "duration": 8,
        "resolution": "720p",
        "aspect_ratio": "16:9",
        "generate_audio": True,
        "no_subtitles": True,
        "no_bgm": True,
        "generate_task": "reference_to_video",
    }


@app.get("/api/examples")
async def examples() -> dict:
    return examples_payload()


@app.get("/api/fetch-image")
async def fetch_image(url: str = Query(..., min_length=8)) -> dict:
    """Same-origin helper: server downloads the image (proxy DNS ok) → base64."""
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must be http(s)")
    try:
        data_b64, mime = await download_image_as_base64(url)
    except Exception as exc:
        raise HTTPException(502, f"download failed: {exc}") from exc
    return {"mime_type": mime, "data": data_b64, "url": url}


@app.post("/api/test-gcs-upload")
async def test_gcs_upload(body: GcsUploadTestRequest) -> dict:
    """Step-1 only: browser reference_images → local disk → GCS (no Omni)."""
    if not gcs_prefix_for_inputs():
        raise HTTPException(400, "INPUT_GCS_URI / OUTPUT_GCS_URI not configured")

    job = await job_store.create()
    items = []
    for img in body.reference_images:
        if img.url:
            data_b64, mime = await download_image_as_base64(img.url)
            items.append({"mime_type": mime, "data": data_b64})
        else:
            items.append({"mime_type": img.mime_type, "data": img.data})
    try:
        staged = await asyncio.to_thread(
            stage_images_to_gcs,
            job_id=job.id,
            items=items,
        )
    except OmniAPIError as exc:
        job.touch(JobStatus.failed, str(exc))
        job.error = str(exc)
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:
        job.touch(JobStatus.failed, str(exc))
        job.error = str(exc)
        raise HTTPException(502, f"stage failed: {exc}") from exc

    job.meta = {
        "image_delivery": "browser_local_gcs",
        "local_refs": [s["local_path"] for s in staged],
        "gcs_refs": [s["uri"] for s in staged],
        "client_images": len(items),
        "test_only": True,
    }
    job.touch(
        JobStatus.completed,
        f"Staged {len(staged)} image(s) to local disk and GCS (no Omni call)",
    )
    return job.to_dict()


@app.post("/api/generate")
async def generate(body: GenerateRequest) -> dict:
    if body.resolution not in {"360p", "720p", "1080p", "4k"}:
        raise HTTPException(400, "resolution must be one of 360p, 720p, 1080p, 4k")
    if body.aspect_ratio not in {"16:9", "9:16"}:
        raise HTTPException(400, "aspect_ratio must be 16:9 or 9:16")

    mode = (body.mode or "generate").strip().lower()
    if mode not in {"generate", "continue", "edit", "extend", "upscale"}:
        raise HTTPException(400, "mode must be generate|continue|edit|extend|upscale")

    model_id, profile, assumed = resolve_model(body.model)
    if body.seed is not None and not profile.supports_seed:
        raise HTTPException(400, f"{model_id} does not accept generation_config.seed")
    try:
        labels = normalize_labels(body.labels)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if labels and not profile.supports_labels:
        raise HTTPException(400, f"{model_id} does not accept labels")
    if mode == "upscale" and profile.upscale_max is None:
        raise HTTPException(
            400,
            f"{model_id} has no standalone upscale. Use gemini-omni-1.2-flash.",
        )

    generate_task = (body.generate_task or "").strip().lower() or None
    if generate_task and generate_task not in {
        "text_to_video",
        "image_to_video",
        "frames_to_video",
        "reference_to_video",
    }:
        raise HTTPException(
            400,
            "generate_task must be text_to_video|image_to_video|"
            "frames_to_video|reference_to_video",
        )

    if mode not in {"continue", "edit", "upscale"}:
        low, high = duration_bounds(profile, mode=mode, generate_task=generate_task)
        if not low <= body.duration <= high:
            raise HTTPException(
                400,
                f"duration must be {low}–{high}s for {model_id}"
                + (" first+last frames" if generate_task == "frames_to_video" else ""),
            )

    urls = [str(u) for u in body.image_urls]
    if mode == "generate" and generate_task != "text_to_video" and not urls:
        _, urls = extract_reference_urls(body.prompt)

    client_images = []
    for img in body.reference_images:
        entry = {"mime_type": img.mime_type}
        if img.data:
            entry["data"] = img.data
        if img.url:
            entry["url"] = img.url
        client_images.append(entry)
    client_videos = [
        {
            "mime_type": item.mime_type,
            "data": item.data,
            "uri": (item.uri or "").strip() or None,
        }
        for item in body.reference_videos
        if item.data or (item.uri or "").strip()
    ]
    if len(client_videos) > profile.reference_videos_max:
        raise HTTPException(
            400, f"at most {profile.reference_videos_max} reference videos"
        )
    if mode in {"continue", "edit", "extend"} and len(client_images) > profile.edit_reference_images_max:
        raise HTTPException(
            400,
            f"{model_id} accepts at most {profile.edit_reference_images_max} "
            f"reference images on {mode}",
        )

    reuse_images: list[dict[str, str]] = []
    reuse_videos: list[dict[str, str]] = []
    prompt_text = body.prompt.strip()
    generate_source_job_id = body.source_job_id

    reuse_id = (body.reuse_job_id or "").strip() or None
    if mode == "generate" and reuse_id:
        src = await job_store.get(reuse_id)
        if not src:
            raise HTTPException(404, f"reuse_job_id not found: {reuse_id}")
        inherited_kind = src.meta.get("generate_task") or src.meta.get("task")
        if inherited_kind in {
            "text_to_video",
            "image_to_video",
            "frames_to_video",
            "reference_to_video",
        }:
            generate_task = generate_task or inherited_kind
        if generate_task == "text_to_video":
            reuse_images = []
            reuse_videos = []
        else:
            reuse_images = reuse_images_from_job(src)
            reuse_videos = reuse_videos_from_job(src)
            if not reuse_images and not reuse_videos and generate_task != "text_to_video":
                raise HTTPException(400, "that job has no GCS images or videos to reuse")
        if not prompt_text:
            prompt_text = (src.meta.get("shot_list") or "").strip()
        if not prompt_text:
            raise HTTPException(400, "reuse needs a prompt or a stored shot_list")
        client_images = []
        client_videos = []
        urls = []
        generate_source_job_id = reuse_id

    if mode == "generate" and not prompt_text:
        raise HTTPException(400, "prompt is required")
    if mode == "upscale":
        # The live API rejects any text part on task=upscale.
        prompt_text = ""
    if mode in {"continue", "edit", "extend"} and not prompt_text:
        raise HTTPException(400, "prompt is required")

    if mode == "generate" and generate_task == "text_to_video":
        client_images = []
        client_videos = []
        urls = []
    if mode == "generate" and generate_task != "reference_to_video":
        client_videos = []

    if (
        mode == "generate"
        and generate_task in {"image_to_video", "frames_to_video", "reference_to_video"}
        and not reuse_id
        and not client_images
        and not urls
        and not client_videos
    ):
        if generate_task == "frames_to_video":
            need = "exactly two images"
        elif generate_task == "reference_to_video":
            need = "at least one image or reference video"
        else:
            need = "at least one image"
        raise HTTPException(
            400,
            f"{generate_task} needs {need} (upload, paste a URL, or Load example)",
        )

    requested_source = (body.source_video_uri or "").strip() or None
    source_video = requested_source
    previous_interaction_id = None
    source_modes = {"continue", "edit", "extend", "upscale"}

    if body.source_job_id and mode in source_modes:
        src = await job_store.get(body.source_job_id)
        if not src:
            raise HTTPException(404, f"source_job_id not found: {body.source_job_id}")
        if not source_video:
            source_video = src.video_uri
        previous_interaction_id = followup_chain_id(
            mode=mode,
            stateful=body.stateful,
            supports_multiturn=profile.supports_multiturn,
            source_interaction_id=src.interaction_id,
            source_meta=src.meta,
            source_model=src.meta.get("model"),
            requested_model=model_id,
            requested_video_uri=requested_source,
            source_video_uri=src.video_uri,
        )
        if mode == "upscale":
            source_res = src.meta.get("output_resolution")
            allowed = upscale_targets(source_res)
            if allowed is not None and body.resolution not in allowed:
                legal = ", ".join(allowed) if allowed else "none"
                raise HTTPException(
                    400,
                    f"Cannot upscale a {source_res} clip to {body.resolution}. "
                    f"Official targets from {source_res}: {legal}.",
                )

    if mode in source_modes and not previous_interaction_id and not (
        source_video and source_video.startswith("gs://")
    ):
        raise HTTPException(
            400,
            f"{mode} needs source_video_uri (gs://…) or a completed source_job_id",
        )

    edit_images = client_images if mode in {"continue", "edit", "extend"} else []
    if mode == "upscale":
        edit_images = []
        client_videos = []

    job = await job_store.create()
    asyncio.create_task(
        run_generation_job(
            job,
            prompt=prompt_text,
            image_urls=urls if mode == "generate" else [],
            client_images=client_images if mode == "generate" else edit_images,
            client_videos=client_videos if mode == "generate" else None,
            duration=body.duration,
            resolution=body.resolution,
            aspect_ratio=body.aspect_ratio,
            generate_audio=body.generate_audio,
            no_subtitles=body.no_subtitles,
            no_bgm=body.no_bgm,
            mode=mode,
            source_video_uri=source_video,
            source_job_id=generate_source_job_id if mode == "generate" else body.source_job_id,
            enhance_prompt=body.enhance_prompt,
            reuse_images=reuse_images if mode == "generate" else None,
            reuse_videos=reuse_videos if mode == "generate" else None,
            generate_task=generate_task if mode == "generate" else None,
            model=model_id,
            seed=body.seed,
            labels=labels or None,
            stateful=body.stateful,
            previous_interaction_id=previous_interaction_id,
            profile_assumed=assumed,
        )
    )
    return job.to_dict()


@app.get("/api/jobs")
async def list_jobs() -> dict:
    jobs = await job_store.list()
    return {"jobs": [j.to_dict() for j in jobs]}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict:
    job = await job_store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


@app.post("/api/jobs/{job_id}/fetch-video")
async def fetch_job_video(job_id: str) -> dict:
    """Re-download video from job.video_uri (useful after IncompleteRead)."""
    from app.gcs_util import download_gcs_bytes

    job = await job_store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not job.video_uri:
        raise HTTPException(400, "Job has no video_uri to fetch")

    out_path = Path(job.video_path) if job.video_path else (OUTPUT_DIR / f"{job_id}.mp4")
    job.touch(JobStatus.saving, f"Re-fetching {job.video_uri}…")
    try:
        if job.video_uri.startswith("gs://"):
            await asyncio.to_thread(
                download_gcs_bytes, job.video_uri, dest=out_path
            )
        else:
            data = await omni_client.download_gcs_or_http(job.video_uri)
            out_path.write_bytes(data)
    except Exception as exc:
        job.error = str(exc)
        job.touch(JobStatus.failed, str(exc))
        raise HTTPException(502, str(exc)) from exc

    job.video_path = str(out_path)
    job.error = None
    job.touch(JobStatus.completed, "Done (re-fetched)")
    return job.to_dict()


def _allowed_gcs_buckets() -> set[str]:
    buckets = set()
    for raw in (settings.input_gcs_uri, settings.output_gcs_uri):
        value = (raw or "").strip()
        if value.startswith("gs://"):
            buckets.add(value[5:].split("/", 1)[0])
    return buckets


@app.get("/api/video", response_model=None)
async def get_video_by_uri(uri: str = Query(..., description="gs:// object to play")):
    """Signed playback for a clip that is not a job output (e.g. a pasted source)."""
    if not uri.startswith("gs://"):
        raise HTTPException(400, "uri must be a gs:// object")
    bucket = uri[5:].split("/", 1)[0]
    # Signing is done with the app's own credentials, so restrict it to the
    # buckets this demo owns rather than anything the caller names.
    if bucket not in _allowed_gcs_buckets():
        raise HTTPException(403, f"bucket not allowed: {bucket}")
    try:
        signed = await asyncio.to_thread(sign_gcs_read_url, uri)
    except Exception as exc:
        raise HTTPException(502, f"Failed to sign GCS URL: {exc}") from exc
    return RedirectResponse(url=signed, status_code=302)


@app.get("/api/jobs/{job_id}/lineage")
async def get_job_lineage(job_id: str) -> dict:
    """Every clip derived from the same original generate, oldest first."""
    job = await job_store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    jobs = await job_store.list(limit=200)
    by_id = {j.id: j for j in jobs}
    by_uri = {j.video_uri: j for j in jobs if j.video_uri}

    def parent_of(j):
        if j.source_job_id and j.source_job_id in by_id:
            return by_id[j.source_job_id]
        if j.source_video_uri:
            return by_uri.get(j.source_video_uri)
        return None

    def root_of(j):
        seen = {j.id}
        while True:
            p = parent_of(j)
            if p is None or p.id in seen:
                return j
            seen.add(p.id)
            j = p

    root = root_of(job)
    family = [j for j in jobs if j.video_uri and root_of(j).id == root.id]
    family.sort(key=lambda j: j.created_at)

    clips = []
    for j in family:
        p = parent_of(j)
        clips.append(
            {
                "job_id": j.id,
                "mode": j.mode,
                "task": j.meta.get("api_task") or j.meta.get("task"),
                "generate_task": j.meta.get("generate_task"),
                "duration_sec": j.duration_sec,
                "video_url": f"/api/jobs/{j.id}/video",
                "video_uri": j.video_uri,
                "prompt": j.meta.get("final_prompt_preview"),
                "created_at": j.created_at,
                "parent_job_id": p.id if p else None,
                "is_current": j.id == job.id,
            }
        )

    # The original may predate this process (pasted gs:// URI with no job).
    orphan_source = None
    if not clips or (clips[0]["parent_job_id"] is None and root.source_video_uri):
        orphan_source = root.source_video_uri

    return {"root_job_id": root.id, "clips": clips, "external_source": orphan_source}


@app.get("/api/jobs/{job_id}/suggestions")
async def get_job_suggestions(job_id: str) -> dict:
    """Follow-up instructions grounded in the shot list that made this clip."""
    job = await job_store.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != JobStatus.completed or not job.video_uri:
        return {"suggestions": [], "reason": "job has no clip yet"}

    cached = job.meta.get("suggestions")
    if cached is not None:
        return {"suggestions": cached, "cached": True}

    # An edit instruction like "remove the cup" carries no scene description, so
    # walk back to the generate job whose prompt actually describes the footage.
    jobs = await job_store.list(limit=200)
    by_id = {j.id: j for j in jobs}
    scene_job, seen = job, {job.id}
    while scene_job.mode != "generate":
        parent = by_id.get(scene_job.source_job_id or "")
        if parent is None or parent.id in seen:
            break
        seen.add(parent.id)
        scene_job = parent

    scene_prompt = (
        scene_job.meta.get("prompt_original")
        or scene_job.meta.get("final_prompt_preview")
        or ""
    )
    if not scene_prompt:
        return {"suggestions": [], "reason": "no prompt to work from"}

    edit_limit = profile_for(job.meta.get("model")).edit_max
    allow_edit = job.duration_sec is None or job.duration_sec <= edit_limit
    suggestions = await prompt_enhancer.suggest_followups(
        scene_prompt, allow_edit=allow_edit
    )
    job.meta["suggestions"] = suggestions
    return {"suggestions": suggestions, "cached": False, "allow_edit": allow_edit}


@app.get("/api/jobs/{job_id}/video", response_model=None)
async def get_job_video(job_id: str):
    """Serve local file, or 302 redirect to a short-lived GCS signed URL."""
    job = await job_store.get(job_id)
    if not job or job.status != JobStatus.completed:
        raise HTTPException(404, "Video not ready")

    if job.video_path:
        path = Path(job.video_path)
        if path.exists():
            return FileResponse(
                path,
                media_type="video/mp4",
                filename=f"{job_id}.mp4",
            )

    if job.video_uri and job.video_uri.startswith("gs://"):
        try:
            signed = await asyncio.to_thread(sign_gcs_read_url, job.video_uri)
        except Exception as exc:
            raise HTTPException(502, f"Failed to sign GCS URL: {exc}") from exc
        return RedirectResponse(url=signed, status_code=302)

    if job.video_uri and job.video_uri.startswith(("http://", "https://")):
        return RedirectResponse(url=job.video_uri, status_code=302)

    raise HTTPException(404, "Video file missing")

