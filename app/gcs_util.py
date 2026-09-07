from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests

from app.config import OUTPUT_DIR, gcs_prefix_for_inputs
from app.omni_client import OmniAPIError, _get_access_token, _requests_session


def parse_gcs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Not a GCS URI: {uri}")
    without = uri[len("gs://") :]
    bucket, _, blob = without.partition("/")
    if not bucket:
        raise ValueError(f"Invalid GCS URI: {uri}")
    return bucket, blob


def ext_for_mime(mime: str) -> str:
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    return mapping.get((mime or "").split(";")[0].strip().lower(), ".png")


def decode_data_url_or_b64(raw: str) -> bytes:
    data_b64 = raw.strip()
    if "," in data_b64 and data_b64.startswith("data:"):
        data_b64 = data_b64.split(",", 1)[1]
    return base64.b64decode(data_b64)


def local_ref_dir(job_id: str) -> Path:
    path = OUTPUT_DIR / "refs" / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_bytes_locally(
    data: bytes,
    *,
    job_id: str,
    index: int,
    mime_type: str,
) -> Path:
    """Persist browser/server image bytes before GCS upload (debuggable)."""
    ext = ext_for_mime(mime_type)
    path = local_ref_dir(job_id) / f"ref_{index:02d}{ext}"
    path.write_bytes(data)
    return path


def upload_bytes_to_gcs(
    data: bytes,
    *,
    gcs_uri: str,
    mime_type: str = "application/octet-stream",
) -> str:
    """Upload bytes to gs://bucket/object using JSON API."""
    bucket, blob = parse_gcs_uri(gcs_uri)
    if not blob:
        raise ValueError(f"GCS URI must include an object path: {gcs_uri}")

    token = _get_access_token()
    url = (
        f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
        f"?uploadType=media&name={quote(blob, safe='')}"
    )
    session = _requests_session()
    response = session.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": mime_type,
        },
        data=data,
        timeout=180,
    )
    if response.status_code >= 400:
        raise OmniAPIError(
            f"GCS upload failed ({response.status_code}) for {gcs_uri}: "
            f"{response.text[:1000]}",
            status_code=response.status_code,
            body=_safe(response),
        )
    return f"gs://{bucket}/{blob}"


def upload_local_file_to_gcs(
    path: Path,
    *,
    gcs_uri: str,
    mime_type: str,
) -> str:
    """Read a local file and upload to GCS (two-step staging)."""
    if not path.is_file():
        raise OmniAPIError(f"Local file missing for GCS upload: {path}")
    return upload_bytes_to_gcs(path.read_bytes(), gcs_uri=gcs_uri, mime_type=mime_type)


def stage_images_to_gcs(
    *,
    job_id: str,
    items: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Decode/save each image locally, then upload to GCS.

    Each item: {"mime_type": "...", "data": "<base64>"}.
    Returns dicts with mime_type, uri, local_path.
    """
    results: list[dict[str, str]] = []
    for idx, item in enumerate(items, start=1):
        mime = item.get("mime_type") or "image/png"
        if "data" not in item:
            raise OmniAPIError(f"Image {idx} missing base64 data")
        raw = decode_data_url_or_b64(item["data"])

        local_path = save_bytes_locally(
            raw, job_id=job_id, index=idx, mime_type=mime
        )
        gcs_uri = build_input_object_uri(job_id, idx, ext_for_mime(mime))
        uploaded = upload_local_file_to_gcs(
            local_path, gcs_uri=gcs_uri, mime_type=mime
        )
        results.append(
            {
                "mime_type": mime,
                "uri": uploaded,
                "local_path": str(local_path),
            }
        )
    return results


def mvhd_duration_sec(head: bytes) -> float | None:
    """Read the movie header's duration. Omni writes moov up front, so the
    first few KB of the object are enough."""
    import struct

    idx = head.find(b"mvhd")
    if idx < 0:
        return None
    try:
        version = head[idx + 4]
        if version == 0:
            timescale, duration = struct.unpack(">II", head[idx + 16 : idx + 24])
        else:
            timescale, duration = struct.unpack(">IQ", head[idx + 24 : idx + 36])
    except (struct.error, IndexError):
        return None
    if not timescale:
        return None
    return duration / timescale


def probe_video_duration(gcs_uri: str, *, head_bytes: int = 65536) -> float | None:
    """Duration of a GCS MP4 in seconds, or None if it cannot be determined."""
    try:
        bucket, blob = parse_gcs_uri(gcs_uri)
    except ValueError:
        return None
    url = (
        f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/"
        f"{quote(blob, safe='')}?alt=media"
    )
    try:
        session = _requests_session()
        resp = session.get(
            url,
            headers={
                "Authorization": f"Bearer {_get_access_token()}",
                "Range": f"bytes=0-{head_bytes - 1}",
            },
            timeout=(30, 90),
        )
        if resp.status_code >= 400:
            return None
        return mvhd_duration_sec(resp.content)
    except Exception:
        return None


def download_gcs_bytes(
    gcs_uri: str,
    *,
    dest: Path | None = None,
    max_attempts: int = 8,
) -> bytes:
    """Download a GCS object with streaming + Range resume (proxy-friendly)."""
    bucket, blob = parse_gcs_uri(gcs_uri)
    url = (
        f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/"
        f"{quote(blob, safe='')}?alt=media"
    )

    tmp = dest
    if tmp is None:
        tmp = OUTPUT_DIR / "_tmp" / f"gcs_{abs(hash(gcs_uri)) & 0xFFFFFFFF:08x}.bin"
        tmp.parent.mkdir(parents=True, exist_ok=True)
    else:
        tmp.parent.mkdir(parents=True, exist_ok=True)

    # Resume from partial local bytes if present.
    offset = tmp.stat().st_size if tmp.is_file() else 0
    last_err: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            token = _get_access_token()
            headers = {"Authorization": f"Bearer {token}"}
            if offset > 0:
                headers["Range"] = f"bytes={offset}-"

            session = _requests_session()
            with session.get(
                url,
                headers=headers,
                timeout=(30, 300),
                stream=True,
            ) as response:
                # 200 full body, 206 partial — both ok when resuming.
                if response.status_code == 416:
                    # Already complete according to server.
                    break
                if response.status_code >= 400:
                    raise OmniAPIError(
                        f"GCS download failed ({response.status_code}) for {gcs_uri}: "
                        f"{response.text[:1000]}",
                        status_code=response.status_code,
                        body=_safe(response),
                    )

                mode = "ab" if offset > 0 and response.status_code == 206 else "wb"
                if mode == "wb":
                    offset = 0
                with tmp.open(mode) as fh:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            fh.write(chunk)
                            offset += len(chunk)

            data = tmp.read_bytes()
            if dest is None:
                tmp.unlink(missing_ok=True)
            return data
        except OmniAPIError:
            raise
        except Exception as exc:
            last_err = exc
            offset = tmp.stat().st_size if tmp.is_file() else 0
            # Brief backoff; Clash/proxy often drops mid-stream.
            import time

            time.sleep(min(2 * attempt, 12))

    # If loop exited via 416 / already complete
    if tmp.is_file() and tmp.stat().st_size > 0 and last_err is None:
        data = tmp.read_bytes()
        if dest is None:
            tmp.unlink(missing_ok=True)
        return data

    # Last resort: curl with resume (often more reliable under Clash).
    try:
        return _download_gcs_via_curl(gcs_uri, tmp, dest is not None)
    except Exception as curl_exc:
        raise OmniAPIError(
            f"GCS download incomplete for {gcs_uri} after {max_attempts} attempts. "
            f"Last error: {last_err}; curl fallback: {curl_exc}"
        ) from curl_exc


def _download_gcs_via_curl(gcs_uri: str, tmp: Path, keep_file: bool) -> bytes:
    import subprocess

    from app.config import ACTIVE_PROXY

    bucket, blob = parse_gcs_uri(gcs_uri)
    url = (
        f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/"
        f"{quote(blob, safe='')}?alt=media"
    )
    token = _get_access_token()
    cmd = ["curl", "-fsSL", "-C", "-"]
    if ACTIVE_PROXY:
        cmd.extend(["-x", ACTIVE_PROXY])
    cmd.extend(
        [
            "-H",
            f"Authorization: Bearer {token}",
            "--connect-timeout",
            "30",
            "--max-time",
            "600",
            "-o",
            str(tmp),
            url,
        ]
    )
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise OmniAPIError(
            f"curl GCS download failed ({proc.returncode}): {proc.stderr[:800]}"
        )
    data = tmp.read_bytes()
    if not keep_file:
        tmp.unlink(missing_ok=True)
    return data


def guess_ext(url: str, mime: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ext_for_mime(mime)


def build_input_object_uri(job_id: str, index: int, ext: str) -> str:
    prefix = gcs_prefix_for_inputs()
    if not prefix:
        raise OmniAPIError(
            "No GCS input prefix configured. Set INPUT_GCS_URI or OUTPUT_GCS_URI "
            "in .env, e.g. INPUT_GCS_URI=gs://your-bucket/omni-input/"
        )
    return f"{prefix.rstrip('/')}/{job_id}/ref_{index:02d}{ext}"


def _metadata_service_account() -> str:
    """Runtime SA email of the current Cloud Run instance, or '' if unavailable."""
    try:
        resp = requests.get(
            "http://metadata.google.internal/computeMetadata/v1"
            "/instance/service-accounts/default/email",
            headers={"Metadata-Flavor": "Google"},
            timeout=3,
        )
        resp.raise_for_status()
        return resp.text.strip()
    except Exception:
        return ""


def signing_service_account() -> str:
    from app.config import IS_CLOUD_RUN, settings

    if settings.gcs_signing_service_account.strip():
        return settings.gcs_signing_service_account.strip()
    if IS_CLOUD_RUN:
        runtime_sa = _metadata_service_account()
        if runtime_sa:
            return runtime_sa
    project = settings.google_cloud_project.strip()
    if not project:
        raise OmniAPIError(
            "Set GOOGLE_CLOUD_PROJECT or GCS_SIGNING_SERVICE_ACCOUNT for signed URLs"
        )
    return f"{project}@appspot.gserviceaccount.com"


def sign_gcs_read_url(gcs_uri: str, *, minutes: int | None = None) -> str:
    """V4 signed HTTPS URL so the browser can play directly from the bucket."""
    from datetime import timedelta

    import google.auth
    from google.cloud import storage

    from app.config import settings
    from app.omni_client import _get_access_token

    if not gcs_uri.startswith("gs://"):
        raise OmniAPIError(f"Expected gs:// URI, got {gcs_uri}")

    minutes = minutes if minutes is not None else settings.signed_url_ttl_minutes
    credentials, project = google.auth.default()
    token = _get_access_token()
    credentials.token = token

    bucket_name, blob_name = parse_gcs_uri(gcs_uri)
    client = storage.Client(
        credentials=credentials,
        project=project or settings.google_cloud_project or None,
    )
    blob = client.bucket(bucket_name).blob(blob_name)
    sa = signing_service_account()

    kwargs: dict[str, Any] = {
        "version": "v4",
        "expiration": timedelta(minutes=max(5, minutes)),
        "method": "GET",
    }
    # User ADC has no private key — IAM signBlob via a service account.
    if not getattr(credentials, "signer", None) and not hasattr(credentials, "sign_bytes"):
        kwargs["service_account_email"] = sa
        kwargs["access_token"] = token

    try:
        return blob.generate_signed_url(**kwargs)
    except Exception as exc:
        raise OmniAPIError(
            f"Failed to sign GCS URL for {gcs_uri} (SA={sa}): {exc}. "
            "Grant roles/iam.serviceAccountTokenCreator on that SA to your user, "
            "or set GCS_SIGNING_SERVICE_ACCOUNT in .env."
        ) from exc


def _safe(response: requests.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text
