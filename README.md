# Omni 1.1 Flash Video Demo

Web UI demo that accepts a shot-list prompt + third-party reference image URLs, downloads those images server-side, and calls **Gemini Omni 1.1 Flash** (`reference_to_video`) via the Agent Platform Interactions API.

## Why URL download?

Omni's Interactions API expects reference images as **GCS `uri`** or **inline base64 `data`**, not arbitrary HTTPS links. This demo downloads your OSS/CDN URLs and sends base64.

## Setup

1. Enable **Agent Platform API** on a GCP project.
2. Authenticate:

```bash
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

3. Configure env:

```bash
cp .env.example .env
# edit GOOGLE_CLOUD_PROJECT=...
# optional: OUTPUT_GCS_URI=gs://your-bucket/omni-output/
```

4. Install & run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## UI flow

1. Click **Load example** (or paste your own prompt + image URLs).
2. Confirm duration `8`, resolution `720p`, aspect `16:9`, audio on / no subtitles / no BGM.
3. Click **Generate video** — status polls until the MP4 appears in the Result panel.

## API

- `GET /api/example` — sample prompt + 6 reference URLs
- `POST /api/generate` — start a job
- `GET /api/jobs/{id}` — poll status
- `GET /api/jobs/{id}/video` — download / stream MP4

## Deploy to Cloud Run

Running on Cloud Run removes the local proxy/VPN from the path entirely: tokens
come from the instance metadata server and GCS traffic stays inside Google's
network, so no `HTTP_PROXY` is involved.

```bash
PROJECT_ID=your-gcp-project BUCKET=your-gcs-bucket ./deploy/cloud-run.sh
```

Optional: `REGION=asia-east1`, `OMNI_MODEL=...`, `PROMPT_ENHANCE_MODEL=...`.

The script enables Agent Platform / Cloud Run / Cloud Build APIs, creates a
runtime service account, and deploys from source. Docker is not required
locally — Cloud Build builds the image from the `Dockerfile`.

Deployment-specific behaviour, keyed off Cloud Run's `K_SERVICE`:

- Proxy env vars are stripped, `.env` proxy values are ignored, and
  `GOOGLE_API_TRUST_ENV=false` is set.
- Scratch files go to `/tmp` (the container filesystem is in-memory).
- `GET /api/health` reports `"runtime": "cloud_run"`.

The deploy flags are load-bearing: `JobStore` keeps job state in process memory
and generation continues after the HTTP response returns, so the service runs
with `--no-cpu-throttling` and `--max-instances 1`. Serving multiple instances
requires moving job state into external storage first.

It scales to zero when idle (`--min-instances 0`), so there is no standby cost,
at the price of a cold start on the first request and the loss of the in-memory
job history once an idle instance is reclaimed. Generated videos always remain
in `OUTPUT_GCS_URI`; keep the tab open during a run so polling holds the
instance up.

`--allow-unauthenticated` is the default so clients can open the URL directly.
Note that `*.run.app` is often unreachable from mainland China; a custom domain
behind an external load balancer may be needed.

When a clip finishes, `GET /api/jobs/{id}/suggestions` asks the same text model
for two or three follow-ups grounded in the shot list that produced it, returned
as JSON via a response schema. They render as clickable bubbles that fill in the
mode, the instruction and the source clip in one click.

Suggestions are cached on the job, so the model is called once per clip. Past
the 10s edit ceiling only `extend` ideas are offered. A failure returns an empty
list and the bubbles stay hidden.

## Notes

- Generation often takes **1–3+ minutes**; jobs run async in the server.
- Dialogue audio is steered by the prompt (Omni has no separate `generate_audio` flag like Veo).
- For reliable large outputs, set `OUTPUT_GCS_URI`.
