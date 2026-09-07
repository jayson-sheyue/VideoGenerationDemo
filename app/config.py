import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).resolve().parent.parent

# Cloud Run always sets K_SERVICE. There the container filesystem is in-memory
# (scratch files count against the memory limit) and egress is native, so a
# proxy from .env would point at a loopback port that nothing is listening on.
IS_CLOUD_RUN = bool(os.environ.get("K_SERVICE"))

_output_dir_env = os.environ.get("OUTPUT_DIR", "").strip()
if _output_dir_env:
    OUTPUT_DIR = Path(_output_dir_env)
elif IS_CLOUD_RUN:
    OUTPUT_DIR = Path("/tmp/omni-outputs")
else:
    OUTPUT_DIR = ROOT_DIR / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    google_cloud_project: str = ""
    # e.g. gs://my-bucket/omni-input/  — reference images uploaded here
    input_gcs_uri: str = ""
    # e.g. gs://my-bucket/omni-output/ — generated videos stored here
    output_gcs_uri: str = ""
    # SA email used to IAM-sign GCS read URLs for browser playback (user ADC has no key).
    # Default: {project}@appspot.gserviceaccount.com
    gcs_signing_service_account: str = ""
    # Browser plays via signed GCS HTTPS URL (skip server-side full download).
    video_playback: str = "gcs_signed"  # gcs_signed | local
    # Use system HTTP(S)_PROXY for Google APIs (oauth / aiplatform / GCS).
    # On Clash/VPN machines DNS often only works via proxy — keep True.
    google_api_trust_env: bool = True
    # Clash Mixed Port (or Http Port). Applied at startup if shell has no proxy.
    # Example: http://127.0.0.1:7890
    http_proxy: str = ""
    https_proxy: str = ""
    omni_model: str = "gemini-omni-1.1-flash-preview"
    # Env: PROMPT_ENHANCE / PROMPT_ENHANCE_MODEL / PROMPT_ENHANCE_LOCATION
    prompt_enhance: bool = True
    prompt_enhance_model: str = "gemini-2.5-flash"
    prompt_enhance_location: str = "global"
    prompt_enhance_timeout_sec: float = 90.0
    poll_interval_sec: float = 8.0
    poll_timeout_sec: float = 900.0
    image_download_timeout_sec: float = 90.0
    signed_url_ttl_minutes: int = 120


settings = Settings()


PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


def apply_proxy_env() -> str | None:
    """Ensure process has HTTP(S)_PROXY for Google DNS via Clash.

    Terminal often does not inherit Clash system proxy. Prefer existing shell
    env; otherwise apply values from .env (HTTP_PROXY / HTTPS_PROXY).
    """
    if IS_CLOUD_RUN:
        for key in PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        return None

    existing = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or os.environ.get("ALL_PROXY")
        or os.environ.get("all_proxy")
    )
    configured = (settings.https_proxy or settings.http_proxy or "").strip()
    if existing:
        return existing
    if not configured:
        return None
    for key in PROXY_ENV_KEYS:
        os.environ[key] = configured
    return configured


ACTIVE_PROXY = apply_proxy_env()


def gcs_prefix_for_inputs() -> str:
    """Prefer input prefix; fall back to output bucket root /inputs/."""
    for raw in (settings.input_gcs_uri, settings.output_gcs_uri):
        value = (raw or "").strip()
        if not value or "YOUR_BUCKET" in value or "YOUR_REAL_BUCKET" in value:
            continue
        if not value.startswith("gs://"):
            continue
        if raw == settings.input_gcs_uri:
            return value.rstrip("/") + "/"
        return value.rstrip("/") + "/inputs/"
    return ""
