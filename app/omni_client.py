from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

import google.auth
import google.auth.transport.requests
import requests

from app.config import ACTIVE_PROXY, IS_CLOUD_RUN, settings


class OmniAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _proxy_dict() -> dict[str, str] | None:
    """Explicit proxies for requests (Clash Mixed Port from .env / shell)."""
    if IS_CLOUD_RUN:
        return None
    proxy = (
        ACTIVE_PROXY
        or (settings.https_proxy or settings.http_proxy or "").strip()
        or None
    )
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _requests_session(*, trust_env: bool | None = None) -> requests.Session:
    session = requests.Session()
    session.trust_env = (
        settings.google_api_trust_env if trust_env is None else trust_env
    )
    proxies = _proxy_dict()
    if proxies and session.trust_env:
        session.proxies.update(proxies)
    return session


def _get_access_token() -> str:
    """Refresh ADC token. Retries through proxy/direct — Clash often flaps briefly."""
    credentials, _project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    # Prefer settings order, then the opposite — covers both Clash DNS and direct.
    # On Cloud Run the token comes from the metadata server; one mode is enough.
    primary = settings.google_api_trust_env
    modes = [primary] if IS_CLOUD_RUN else [primary, (not primary)]
    errors: list[str] = []

    for round_i in range(3):
        for trust_env in modes:
            session = _requests_session(trust_env=trust_env)
            request = google.auth.transport.requests.Request(session=session)
            try:
                credentials.refresh(request)
                if credentials.token:
                    return credentials.token
                errors.append(f"round{round_i}+trust_env={trust_env}: empty token")
            except Exception as exc:
                errors.append(f"round{round_i}+trust_env={trust_env}: {exc}")
        time.sleep(0.8 * (round_i + 1))

    if IS_CLOUD_RUN:
        hint = (
            "Credentials come from the instance metadata server — verify a runtime "
            "service account is attached and holds roles/aiplatform.user."
        )
    else:
        proxy_hint = ACTIVE_PROXY or settings.http_proxy or "(none)"
        hint = (
            f"Active proxy={proxy_hint}. Clash may be unstable — retry Generate, or "
            "check Mixed Port 7890 is up. Also: gcloud auth application-default login."
        )
    raise OmniAPIError(
        "Failed to refresh Google access token. " + hint + " Details: " + " | ".join(errors[-6:])
    )


def _project_id() -> str:
    if settings.google_cloud_project:
        return settings.google_cloud_project
    _, project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    if not project:
        raise OmniAPIError(
            "GOOGLE_CLOUD_PROJECT is not set and ADC has no default project."
        )
    return project


def interactions_url(project_id: str, interaction_id: str | None = None) -> str:
    base = (
        f"https://aiplatform.googleapis.com/v1beta1/projects/{project_id}"
        f"/locations/global/interactions"
    )
    if interaction_id:
        return f"{base}/{interaction_id}"
    return base


def build_payload(
    *,
    prompt: str,
    images: list[dict[str, str]] | None = None,
    duration: str,
    resolution: str,
    aspect_ratio: str,
    task: str | None = None,
    video: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build an Interactions payload for text_to_video / image_to_video /
    reference_to_video / edit / extend.

    images: list of {"mime_type", "uri"|"data"} — first/last frames for
    image_to_video, or character/scene refs for reference_to_video
    video:  {"mime_type", "uri"} for edit / extend from a prior clip

    response_format is validated per task and the request is rejected outright
    on a field the task does not accept (verified against the live API):
      edit   — resolution only; geometry and length come from the source video
      extend — duration only; aspect_ratio is rejected despite the docs listing it
    """
    images = images or []
    text_item: dict[str, Any] = {"type": "text", "text": prompt}
    input_items: list[dict[str, Any]] = []

    if video:
        v: dict[str, Any] = {
            "type": "video",
            "mime_type": video.get("mime_type") or "video/mp4",
        }
        if video.get("uri"):
            v["uri"] = video["uri"]
        elif video.get("data"):
            v["data"] = video["data"]
        else:
            raise OmniAPIError("Video entry needs either uri or data")
        input_items.append(v)

    for img in images:
        item: dict[str, Any] = {
            "type": "image",
            "mime_type": img["mime_type"],
        }
        if img.get("uri"):
            item["uri"] = img["uri"]
        elif img.get("data"):
            item["data"] = img["data"]
        else:
            raise OmniAPIError("Image entry needs either uri or data")
        input_items.append(item)

    input_items.append(text_item)

    if not task:
        if video:
            task = "edit"
        elif images:
            task = "reference_to_video"
        else:
            task = "text_to_video"

    response_format: dict[str, Any] = {"type": "video"}
    if task == "edit":
        response_format["resolution"] = resolution
    elif task == "extend":
        response_format["duration"] = duration
    else:
        response_format["aspect_ratio"] = aspect_ratio
        response_format["resolution"] = resolution
        response_format["duration"] = duration

    if settings.output_gcs_uri:
        response_format["delivery"] = "uri"
        response_format["gcs_uri"] = settings.output_gcs_uri

    return {
        "model": settings.omni_model,
        "input": input_items,
        "response_format": [response_format],
        "generation_config": {"video_config": {"task": task}},
    }


def _create_interaction_sync(payload: dict[str, Any]) -> dict[str, Any]:
    project_id = _project_id()
    token = _get_access_token()
    url = interactions_url(project_id)
    session = _requests_session()
    response = session.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        data=json.dumps(payload),
        timeout=180,
    )
    if response.status_code >= 400:
        raise OmniAPIError(
            f"Omni create failed ({response.status_code}): {response.text[:2000]}",
            status_code=response.status_code,
            body=_safe_json(response),
        )
    return response.json()


def _get_interaction_sync(interaction_id: str) -> dict[str, Any]:
    project_id = _project_id()
    token = _get_access_token()
    url = interactions_url(project_id, interaction_id)
    session = _requests_session()
    response = session.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    if response.status_code == 405:
        response = session.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            data="{}",
            timeout=60,
        )
    if response.status_code >= 400:
        raise OmniAPIError(
            f"Omni poll failed ({response.status_code}): {response.text[:2000]}",
            status_code=response.status_code,
            body=_safe_json(response),
        )
    return response.json()


async def create_interaction(payload: dict[str, Any]) -> dict[str, Any]:
    return await asyncio.to_thread(_create_interaction_sync, payload)


async def get_interaction(interaction_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(_get_interaction_sync, interaction_id)


async def poll_until_done(interaction_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + settings.poll_timeout_sec
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = await get_interaction(interaction_id)
        status = (last.get("status") or "").lower()
        if status in {"completed", "failed", "cancelled", "canceled"}:
            return last
        await asyncio.sleep(settings.poll_interval_sec)
    raise OmniAPIError(
        f"Timed out waiting for interaction {interaction_id}",
        body=last,
    )


def extract_video(interaction: dict[str, Any]) -> tuple[bytes | None, str | None, str | None]:
    steps = interaction.get("steps") or []
    for step in steps:
        if step.get("type") != "model_output":
            continue
        for item in step.get("content") or []:
            if item.get("type") != "video":
                continue
            mime = item.get("mime_type") or "video/mp4"
            uri = item.get("uri")
            data = item.get("data")
            if data:
                return base64.b64decode(data), uri, mime
            if uri:
                return None, uri, mime

    output_video = interaction.get("output_video") or {}
    if output_video.get("data"):
        return (
            base64.b64decode(output_video["data"]),
            output_video.get("uri"),
            output_video.get("mime_type") or "video/mp4",
        )
    if output_video.get("uri"):
        return None, output_video["uri"], output_video.get("mime_type") or "video/mp4"

    return None, None, None


def extract_thoughts(interaction: dict[str, Any]) -> str:
    chunks: list[str] = []
    for step in interaction.get("steps") or []:
        if step.get("type") != "thought":
            continue
        for item in step.get("summary") or []:
            if item.get("type") == "text" and item.get("text"):
                chunks.append(item["text"])
    return "\n".join(chunks)


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text


async def download_gcs_or_http(uri: str) -> bytes:
    if uri.startswith("gs://"):
        from app.gcs_util import download_gcs_bytes

        return await asyncio.to_thread(download_gcs_bytes, uri)

    def _get() -> bytes:
        session = _requests_session()
        response = session.get(uri, timeout=180)
        if response.status_code >= 400:
            raise OmniAPIError(f"Failed to download video URI ({response.status_code})")
        return response.content

    return await asyncio.to_thread(_get)
