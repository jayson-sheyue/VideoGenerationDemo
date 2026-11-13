"""Small durable workspace store: GCS in Cloud Run, atomic JSON files locally.

Execution records and user-managed library metadata are separate so a late worker
cannot overwrite a rename/archive. Library writes use object-generation CAS.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import quote

from app.config import OUTPUT_DIR, settings

workspace_id: ContextVar[str] = ContextVar("workspace_id", default="local")
TOKEN_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def owner_for(token: str) -> str:
    if not TOKEN_PATTERN.fullmatch(token):
        raise ValueError("Invalid workspace recovery key")
    return hashlib.sha256(token.encode()).hexdigest()


class ConflictError(Exception):
    pass


# Record reads and writes come in bursts (opening a workspace, the 2s flush).
# One token serves the burst; the metadata server keeps a token valid for at
# least five minutes, so two minutes of reuse stays well inside its lifetime.
TOKEN_REUSE_SEC = 120.0
_token_lock = threading.Lock()
_token: dict = {"value": "", "until": 0.0}


def _bearer() -> str:
    from app.omni_client import _get_access_token

    with _token_lock:
        if not _token["value"] or time.monotonic() >= _token["until"]:
            _token["value"] = _get_access_token()
            _token["until"] = time.monotonic() + TOKEN_REUSE_SEC
        return _token["value"]


class RecordStore:
    def __init__(self, location: str):
        self.location = location.rstrip("/")
        self.lock = threading.RLock()
        self.http = None

    def _url(self, key: str) -> str:
        bucket, _, prefix = self.location[5:].partition("/")
        name = f"{prefix}/{key}".lstrip("/")
        return f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{quote(name, safe='')}"

    def _session(self):
        """One pooled session per store, so record calls reuse open connections."""
        from app.omni_client import _requests_session

        with self.lock:
            if self.http is None:
                from requests.adapters import HTTPAdapter
                from urllib3.util.retry import Retry

                session = _requests_session()
                # A connection that failed to open is tried again; a read is repeated for GET only,
                # so a conditional write is sent once.
                retry = Retry(total=2, connect=2, read=2, status=0, allowed_methods={"GET"}, backoff_factor=0.2)
                session.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=16, max_retries=retry))
                self.http = session
            return self.http

    @staticmethod
    def _auth() -> dict:
        return {"Authorization": "Bearer " + _bearer()}

    def read(self, key: str) -> tuple[dict | None, str]:
        if self.location.startswith("gs://"):
            r = self._session().get(self._url(key), params={"alt": "media"}, headers=self._auth(), timeout=30)
            if r.status_code == 404:
                return None, "0"
            r.raise_for_status()
            return r.json(), r.headers.get("x-goog-generation", "0")
        with self.lock:
            path = Path(self.location) / key
            if not path.exists():
                return None, "0"
            record = json.loads(path.read_text())
            return record["data"], record["generation"]

    def write(self, key: str, data: dict, expected: str | None = None) -> str:
        if self.location.startswith("gs://"):
            bucket, _, prefix = self.location[5:].partition("/")
            params = {"uploadType": "media", "name": f"{prefix}/{key}".lstrip("/")}
            if expected is not None:
                params["ifGenerationMatch"] = expected
            r = self._session().post(f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o",
                                     params=params, json=data, headers=self._auth(), timeout=30)
            if r.status_code == 412:
                raise ConflictError("This item changed in another window. Refresh and try again.")
            r.raise_for_status()
            return str(r.json()["generation"])
        with self.lock:
            _, generation = self.read(key)
            if expected is not None and generation != expected:
                raise ConflictError("This item changed in another window. Refresh and try again.")
            path = Path(self.location) / key
            path.parent.mkdir(parents=True, exist_ok=True)
            generation = str(int(generation) + 1)
            temp = path.with_suffix("." + secrets.token_hex(6) + ".tmp")
            temp.write_text(json.dumps({"generation": generation, "data": data}, ensure_ascii=False))
            os.replace(temp, path)
            return generation

    def list(self, prefix: str) -> list[dict]:
        if not self.location.startswith("gs://"):
            with self.lock:
                directory = Path(self.location) / prefix
                return [json.loads(p.read_text())["data"] for p in directory.glob("*.json")]
        bucket, _, base = self.location[5:].partition("/")
        params = {"prefix": f"{base}/{prefix}".lstrip("/"), "maxResults": 1000}
        names: list[str] = []
        s, auth = self._session(), self._auth()
        while True:
            r = s.get(f"https://storage.googleapis.com/storage/v1/b/{bucket}/o", params=params, headers=auth, timeout=30)
            r.raise_for_status()
            payload = r.json()
            names += [item["name"] for item in payload.get("items", []) if item["name"].endswith(".json")]
            if not payload.get("nextPageToken"):
                break
            params["pageToken"] = payload["nextPageToken"]

        def fetch(name: str) -> dict:
            url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{quote(name, safe='')}"
            result = s.get(url, params={"alt": "media"}, headers=auth, timeout=30)
            result.raise_for_status()
            return result.json()

        # A workspace with a long history opens in a few round trips, not one per record.
        with ThreadPoolExecutor(max_workers=8) as pool:
            return list(pool.map(fetch, names))


def default_store() -> RecordStore:
    location = settings.workspace_store_uri
    if not location:
        location = settings.output_gcs_uri.rstrip("/") + "/workspaces" if settings.output_gcs_uri else str(OUTPUT_DIR / "workspaces")
    return RecordStore(location)
