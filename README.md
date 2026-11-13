# Omni Flash Video Lab

[English](./README.md) · [日本語](./README.ja.md) · [中文](./README.zh-CN.md)

Local / Cloud Run lab for **Gemini Omni Flash**. The default model is GA **Omni 1.2 Flash** (`gemini-omni-1.2-flash`). The UI can switch to **1.1 Flash Preview** (`gemini-omni-1.1-flash-preview`) or accept a custom model id. Write a shot list, attach reference images or videos, and call the Google Cloud **Agent Platform Interactions API** for generate, multi-turn edit, extend, and upscale.

Model limits and prompt patterns: [learning_guide.md](./learning_guide.md) (Chinese). The in-product **API guide** tab (also at `/docs`) lists every capability, the limits by model, and the official pages. This README covers how to run, deploy, and call this repo.

## What it can do

| UI mode | API `task` | Role |
| --- | --- | --- |
| **Generate → Text** | `text_to_video` | Text shot list → new clip |
| **Generate → First frame** | `image_to_video` | One still as **frame 0** |
| **Generate → First + last** | `image_to_video` | Interpolate between two stills |
| **Generate → References** | `reference_to_video` | Up to 10 stills and 3 reference clips (not first frames, not an edit source) |
| **Continue edit** | `edit` | 1.2 defaults to chaining the prior interaction; can fall back to re-sending `gs://` |
| **Edit video** | `edit` | Edit any `gs://` source. 1.2 may attach up to 2 reference images |
| **Extend** | `extend` | Append 3–10s and return the concatenated clip. **1.2 accepts any-length input**; **this demo still caps 1.1 at ≤ 30s**. Output is about ≤ **40s** |
| **Upscale** | `upscale` | 1.2 only. Raise resolution for clips ≤ 15s. No text input |

1.2 generate/edit is **3–15s** (first+last frames stay 3–10s). 1.1 stays **3–10s**. Audio files are not inputs; dialogue / SFX / music belong in the prompt.

### Extend: the 1.1 vs 1.2 difference you must show customers

Official sources: [Extend videos](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/extend-videos), [1.2 model card](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-1-2-flash), [1.1 model card](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-1-1-flash) (Deprecated).

| | Omni 1.2 Flash | Omni 1.1 in this demo |
| --- | --- | --- |
| Extend **input** | **Any duration** | Still blocked above **30s** |
| **Watch** (~10s) | Model uses about the **last 10s** only as context for what happens next | Same idea once a clip is accepted |
| **Keep** (~30s) | Output keeps about the **last 30s** of the source, then appends the new segment; earlier footage is dropped | Same ~40s ceiling |
| Per-call add | **3–10s** (not 15s) | Same 3–10s |
| Output length | About ≤ **40s** ≈ last 30s of source + up to 10s new | Same ~40s ceiling |

A 60s source extended by 10s returns about **40s** (last 30 + new 10), not 70s. The “last 10s” line is the **context window**, not “only 10s of source survive in the file.” Do **not** plan `15 + 15 + 15 = 45`.

## How media moves

Omni Interactions accepts only GCS `uri` or inline base64—not arbitrary HTTPS image URLs.

1. Local uploads become base64 in the browser; public image URLs are downloaded by the server (also available as `GET /api/fetch-image`) so CORS blocks do not stop a job
2. Stills go under `INPUT_GCS_URI`, then Omni receives `gs://`
3. Outputs land under `OUTPUT_GCS_URI`
4. Playback uses a short-lived signed GCS URL

Without GCS the service falls back to base64 / local disk (fragile for large files). On Cloud Run the browser posts media to the service, which writes GCS—no separate “upload first” step for the presenter.

**1.2 Continue / Edit can be multi-turn** with `store: true` and `previous_interaction_id` (~7 days), and that follow-up omits `video_config.task`. Uncheck “stateful”, or use 1.1, and the demo re-attaches the latest `gs://` clip. **Extend and Upscale always re-send the mp4** with `task=extend` or `task=upscale` — the live API rejects that task together with `previous_interaction_id`. Upscale targets follow the official ladder (360p → 720p/1080p, 720p → 1080p/4k; 1080p → 4k is not supported). **Upscale is terminal**: later Edit / Extend must re-send the mp4, not the upscale interaction id.

## Local run

1. Enable billing and **Agent Platform API** (`aiplatform.googleapis.com`).
2. Create a GCS bucket with `omni-input/` and `omni-output/` prefixes.
3. ADC:

```bash
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

```bash
cp .env.example .env
# set GOOGLE_CLOUD_PROJECT, INPUT_GCS_URI, OUTPUT_GCS_URI
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open http://127.0.0.1:8000 . Generation is usually 1–3+ minutes and runs in the background; the page keeps polling `/api/jobs`.

## Studio, portfolio, and task history

- **Studio**: eight functions (text, first frame, first + last, references, edit, continue edit, extend, upscale). Each shows only its own controls and keeps its own draft, so a walk like Generate → Upscale → Edit → Extend stays valid. Drafts and the open work come back after a refresh.
- **Preview**: every finished clip offers Continue edit / Edit / Extend / Upscale with that clip as the source, plus **Use these settings** to load a task's inputs back into the studio. Tabs show the inputs, the exact prompt and request sent, the task timeline, and the versions derived from the same original with side-by-side playback.
- **Portfolio**: finished clips with title, collection, notes, favorite, archive, and a reversible trash; search and filters.
- **Tasks**: the full history with status filters. Opening a task shows the same preview in a dialog without touching the studio draft.
- **Workspace**: history belongs to a browser workspace (an http-only cookie). *Workspace → Export recovery file* opens the same workspace on another device. Task records and portfolio metadata are stored under `OUTPUT_GCS_URI/workspaces/` (override with `WORKSPACE_STORE_URI`); without a bucket they are JSON files under `outputs/workspaces/`. After a restart, a task that already has an interaction id resumes by polling that id; nothing is submitted twice.
- UI language: English, 日本語, 中文 (Workspace dialog). Prompts are sent as written.

## Deploy to Cloud Run

```bash
PROJECT_ID=your-gcp-project BUCKET=your-gcs-bucket ./deploy/cloud-run.sh
```

Optional: `REGION`, `SERVICE`, `OMNI_MODEL`, `ALLOW_UNAUTHENTICATED=false`. The script enables APIs, creates the runtime SA, and builds from source. Keep `--no-cpu-throttling` and `--max-instances 1`: a running generation is polled by the instance that started it. History itself is durable (see Workspace above), so scale-to-zero and redeploys keep it. `MAX_ACTIVE_JOBS` (default 8) caps generations in flight on the instance.

## Known limits

- Slow generations—treat as async jobs.
- No Veo-style `generate_audio` flag; dialogue is prompt-locked.
- Voice / dialogue content cannot be edited; picture edits can.
- Extend follows the official contract: **any-length** source; `response_format.duration` is the append length (3–10s). Live rejects `aspect_ratio` on extend — this demo does not send it. The demo does **not** pre-trim long sources.
- Upscale rejects any text part.
- After upscale, Edit / Extend must re-send `gs://`.
- Failed Omni interactions surface the API `errors` text in the job message.
- Stock / CMAF **fragmented MP4** inputs (`iso5` + `moof`, `mvhd.duration=0`) are remuxed to progressive faststart (same duration) so the container is readable; meta may show `source_remuxed: true`.

See [learning_guide.md](./learning_guide.md) for the full task matrix.
