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


def _media_item(kind: str, entry: dict[str, str]) -> dict[str, Any]:
    item: dict[str, Any] = {
        "type": kind,
        "mime_type": entry.get("mime_type")
        or ("video/mp4" if kind == "video" else "image/png"),
    }
    if entry.get("uri"):
        item["uri"] = entry["uri"]
    elif entry.get("data"):
        item["data"] = entry["data"]
    else:
        raise OmniAPIError(f"{kind} entry needs either uri or data")
    return item


def build_payload(
    *,
    prompt: str,
    images: list[dict[str, str]] | None = None,
    videos: list[dict[str, str]] | None = None,
    duration: str,
    resolution: str,
    aspect_ratio: str,
    task: str | None = None,
    video: dict[str, str] | None = None,
    model: str | None = None,
    seed: int | None = None,
    labels: dict[str, str] | None = None,
    store: bool | None = None,
    background: bool | None = None,
    previous_interaction_id: str | None = None,
) -> dict[str, Any]:
    """Build an Interactions payload.

    images: {"mime_type", "uri"|"data"} — frames or references
    videos: extra reference clips (not the edit/extend/upscale source)
    video:  source clip for edit / extend / upscale

    response_format is trimmed per task. The live API rejects fields a task
    does not accept:
      edit / upscale — resolution only
      extend — duration only (aspect_ratio is rejected despite some docs)
    """
    images = images or []
    videos = videos or []
    input_items: list[dict[str, Any]] = []

    if not task:
        if video or previous_interaction_id:
            task = "edit"
        elif images or videos:
            task = "reference_to_video"
        else:
            task = "text_to_video"

    if video and not previous_interaction_id:
        input_items.append(_media_item("video", video))
    for clip in videos:
        input_items.append(_media_item("video", clip))
    for img in images:
        input_items.append(_media_item("image", img))
    # Live API: "Text input is not supported for upscale task."
    if task != "upscale":
        input_items.append({"type": "text", "text": prompt})

    response_format: dict[str, Any] = {"type": "video"}
    if task in {"edit", "upscale"}:
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

    generation_config: dict[str, Any] = {"video_config": {"task": task}}
    if seed is not None:
        generation_config["seed"] = seed

    payload: dict[str, Any] = {
        "model": model or settings.omni_model,
        "input": input_items,
        "response_format": [response_format],
        "generation_config": generation_config,
    }
    if store is not None:
        payload["store"] = store
    if background is not None:
        payload["background"] = background
    if previous_interaction_id:
        payload["previous_interaction_id"] = previous_interaction_id
    if labels:
        payload["labels"] = labels
    return payload


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


def _error_text(err: Any) -> str:
    if isinstance(err, str):
        return err.strip()
    if not isinstance(err, dict):
        return ""
    code = str(err.get("code") or "").strip()
    message = str(err.get("message") or err.get("detail") or "").strip()
    if code and message:
        return f"{code}: {message}"
    return message or code


def format_interaction_failure(interaction: dict[str, Any]) -> str:
    """Keep the API's own failure text. A bare status hides policy and limit errors."""
    messages: list[str] = []

    def add(err: Any) -> None:
        if isinstance(err, list):
            for item in err:
                add(item)
            return
        text = _error_text(err)
        if text and text not in messages:
            messages.append(text)

    add(interaction.get("errors"))
    add(interaction.get("error"))
    for step in interaction.get("steps") or []:
        if isinstance(step, dict):
            add(step.get("errors"))
            add(step.get("error"))
    if not messages:
        return "Omni interaction failed"
    return "Omni interaction failed: " + " | ".join(messages)


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
