#!/usr/bin/env bash
#
# Deploy the Omni video demo to Cloud Run.
#
#   PROJECT_ID=your-gcp-project BUCKET=your-gcs-bucket ./deploy/cloud-run.sh
#
# Optional: REGION, SERVICE, OMNI_MODEL, PROMPT_ENHANCE_MODEL, ALLOW_UNAUTHENTICATED
#
# Requires: gcloud CLI, authenticated as a user with project owner/editor.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID to your GCP project}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-omni-video-demo}"
BUCKET="${BUCKET:?Set BUCKET to your GCS bucket name (no gs:// prefix)}"
INPUT_GCS_URI="${INPUT_GCS_URI:-gs://${BUCKET}/omni-input/}"
OUTPUT_GCS_URI="${OUTPUT_GCS_URI:-gs://${BUCKET}/omni-output/}"
OMNI_MODEL="${OMNI_MODEL:-gemini-omni-1.1-flash-preview}"
PROMPT_ENHANCE="${PROMPT_ENHANCE:-true}"
PROMPT_ENHANCE_MODEL="${PROMPT_ENHANCE_MODEL:-gemini-2.5-flash}"
PROMPT_ENHANCE_LOCATION="${PROMPT_ENHANCE_LOCATION:-global}"
SA_NAME="${SA_NAME:-omni-video-demo}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
# Set to false to require IAM auth (clients then need `gcloud run services proxy`).
ALLOW_UNAUTHENTICATED="${ALLOW_UNAUTHENTICATED:-true}"

cd "$(dirname "$0")/.."

echo "==> Enabling required APIs on ${PROJECT_ID}"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com \
  storage.googleapis.com \
  iamcredentials.googleapis.com \
  --project "${PROJECT_ID}"

echo "==> Ensuring runtime service account ${SA_EMAIL}"
if ! gcloud iam service-accounts describe "${SA_EMAIL}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name "Omni video demo runtime" \
    --project "${PROJECT_ID}"
fi

echo "==> Granting Vertex / Agent Platform access"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${SA_EMAIL}" \
  --role roles/aiplatform.user \
  --condition None >/dev/null

echo "==> Granting read/write on gs://${BUCKET}"
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member "serviceAccount:${SA_EMAIL}" \
  --role roles/storage.objectAdmin \
  --project "${PROJECT_ID}" >/dev/null

# V4 signed URLs need a private key. The runtime SA has none, so the client
# signs through the IAM signBlob API — which requires the SA to impersonate itself.
echo "==> Granting self-impersonation for signed URLs"
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --member "serviceAccount:${SA_EMAIL}" \
  --role roles/iam.serviceAccountTokenCreator \
  --project "${PROJECT_ID}" >/dev/null

AUTH_FLAG="--no-allow-unauthenticated"
if [ "${ALLOW_UNAUTHENTICATED}" = "true" ]; then
  AUTH_FLAG="--allow-unauthenticated"
fi

echo "==> Deploying ${SERVICE} to ${REGION}"
# --no-cpu-throttling: generation jobs keep polling Omni for minutes after the
#   HTTP response returns; without it the CPU is frozen between requests.
# --min-instances 0: scales to zero when idle (no standby cost). The browser
#   polls /api/jobs/{id} every 2.5s during generation, which keeps the instance
#   alive; JobStore is in-memory, so history is lost once it does scale down.
# --max-instances 1: every poll must land on the instance that started the job.
gcloud run deploy "${SERVICE}" \
  --source . \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --service-account "${SA_EMAIL}" \
  --cpu 2 \
  --memory 2Gi \
  --no-cpu-throttling \
  --min-instances 0 \
  --max-instances 1 \
  --concurrency 40 \
  --timeout 3600 \
  ${AUTH_FLAG} \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID},INPUT_GCS_URI=${INPUT_GCS_URI},OUTPUT_GCS_URI=${OUTPUT_GCS_URI},OMNI_MODEL=${OMNI_MODEL},PROMPT_ENHANCE=${PROMPT_ENHANCE},PROMPT_ENHANCE_MODEL=${PROMPT_ENHANCE_MODEL},PROMPT_ENHANCE_LOCATION=${PROMPT_ENHANCE_LOCATION},VIDEO_PLAYBACK=gcs_signed,GCS_SIGNING_SERVICE_ACCOUNT=${SA_EMAIL},GOOGLE_API_TRUST_ENV=false"

URL="$(gcloud run services describe "${SERVICE}" \
  --project "${PROJECT_ID}" --region "${REGION}" --format 'value(status.url)')"

echo
echo "==> Deployed: ${URL}"
echo "    Health:   ${URL}/api/health"
