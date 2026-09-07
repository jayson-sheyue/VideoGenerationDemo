from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, HttpUrl

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
    EDIT_MAX_INPUT_SEC,
    JobStatus,
    job_store,
    reuse_images_from_job,
    run_generation_job,
)
from app.omni_client import OmniAPIError
from app import omni_client
from app.prompt_utils import (
    EXAMPLE_IMAGES,
    EXAMPLE_PROMPT,
    download_image_as_base64,
    extract_reference_urls,
)

app = FastAPI(title="Omni 1.1 Flash Video Demo", version="0.1.0")

STATIC_DIR = ROOT_DIR / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ReferenceImage(BaseModel):
    mime_type: str = "image/png"
    data: str = Field(..., min_length=8, description="Base64 image bytes")


class GcsUploadTestRequest(BaseModel):
    reference_images: list[ReferenceImage] = Field(..., min_length=1)


class GenerateRequest(BaseModel):
    prompt: str = Field(default="")
    image_urls: list[HttpUrl] = Field(default_factory=list)
    # Browser-fetched images — preferred when server cannot reach OSS DNS.
    reference_images: list[ReferenceImage] = Field(default_factory=list)
    duration: int = Field(default=8, ge=3, le=10)
    resolution: str = Field(default="720p")
    aspect_ratio: str = Field(default="16:9")
    generate_audio: bool = True
    no_subtitles: bool = True
    no_bgm: bool = True
    # generate | continue | edit | extend
    mode: str = Field(default="generate")
    enhance_prompt: bool = True
    # For continue/edit/extend: gs://…mp4 from a prior job (or any GCS video)
    source_video_uri: str | None = None
    source_job_id: str | None = None
    # Generate again using GCS refs already uploaded for this job.
    reuse_job_id: str | None = None


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "project_configured": bool(settings.google_cloud_project),
        "model": settings.omni_model,
        "input_gcs_uri": settings.input_gcs_uri or None,
        "output_gcs_uri": settings.output_gcs_uri or None,
        "image_delivery": "gcs" if (settings.input_gcs_uri or settings.output_gcs_uri) else "base64",
        "active_proxy": ACTIVE_PROXY,
        "google_api_trust_env": settings.google_api_trust_env,
        "video_playback": settings.video_playback,
        "runtime": "cloud_run" if IS_CLOUD_RUN else "local",
        "prompt_enhance": settings.prompt_enhance,
        "prompt_enhance_model": settings.prompt_enhance_model,
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
    }


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
    items = [
        {"mime_type": img.mime_type, "data": img.data} for img in body.reference_images
    ]
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
    if mode not in {"generate", "continue", "edit", "extend"}:
        raise HTTPException(400, "mode must be generate|continue|edit|extend")

    urls = [str(u) for u in body.image_urls]
    if mode == "generate" and not urls:
        _, urls = extract_reference_urls(body.prompt)

    client_images = [
        {"mime_type": img.mime_type, "data": img.data} for img in body.reference_images
    ]
    reuse_images: list[dict[str, str]] = []
    prompt_text = body.prompt.strip()
    generate_source_job_id = body.source_job_id

    reuse_id = (body.reuse_job_id or "").strip() or None
    if mode == "generate" and reuse_id:
        src = await job_store.get(reuse_id)
        if not src:
            raise HTTPException(404, f"reuse_job_id not found: {reuse_id}")
        reuse_images = reuse_images_from_job(src)
        if not reuse_images:
            raise HTTPException(400, "that job has no GCS reference images to reuse")
        if not prompt_text:
            prompt_text = (src.meta.get("shot_list") or "").strip()
        if not prompt_text:
            raise HTTPException(400, "reuse needs a prompt or a stored shot_list")
        client_images = []
        urls = []
        generate_source_job_id = reuse_id

    if mode == "generate" and not prompt_text:
        raise HTTPException(400, "prompt is required")

    source_video = (body.source_video_uri or "").strip() or None

    if body.source_job_id and mode in {"continue", "edit", "extend"}:
        src = await job_store.get(body.source_job_id)
        if not src:
            raise HTTPException(404, f"source_job_id not found: {body.source_job_id}")
        if not source_video:
            source_video = src.video_uri

    if mode in {"continue", "edit", "extend"} and not (
        source_video and source_video.startswith("gs://")
    ):
        raise HTTPException(
            400,
            f"{mode} mode needs source_video_uri (gs://…) or a completed source_job_id",
        )

    job = await job_store.create()
    asyncio.create_task(
        run_generation_job(
            job,
            prompt=prompt_text,
            image_urls=urls if mode == "generate" else [],
            client_images=client_images if mode == "generate" else [],
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
                "task": j.meta.get("task"),
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

    allow_edit = job.duration_sec is None or job.duration_sec <= EDIT_MAX_INPUT_SEC
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

