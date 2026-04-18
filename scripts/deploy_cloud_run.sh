#!/usr/bin/env bash
set -euo pipefail

IMAGE=""
SERVICE_NAME=""
REGION=""
PROJECT_ID=""
ENVIRONMENT=""
DRY_RUN=0

usage() {
  echo "Usage: $0 --image URI:tag --service NAME --region REGION --project PROJECT_ID --environment {dev|staging|prod} [--dry-run]" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image)
      IMAGE="${2:-}"
      shift 2
      ;;
    --service)
      SERVICE_NAME="${2:-}"
      shift 2
      ;;
    --region)
      REGION="${2:-}"
      shift 2
      ;;
    --project)
      PROJECT_ID="${2:-}"
      shift 2
      ;;
    --environment)
      ENVIRONMENT="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$IMAGE" || -z "$SERVICE_NAME" || -z "$REGION" || -z "$PROJECT_ID" || -z "$ENVIRONMENT" ]]; then
  usage
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "ERROR: gcloud not found" >&2
  exit 1
fi

if ! gcloud auth print-access-token --quiet >/dev/null 2>&1; then
  echo "ERROR: not authenticated. Run gcloud auth login" >&2
  exit 1
fi

DEPLOY_CMD=(
  gcloud run deploy "${SERVICE_NAME}"
  --image "${IMAGE}"
  --region "${REGION}"
  --project "${PROJECT_ID}"
  --platform managed
  --quiet
)

if [[ "$ENVIRONMENT" == "prod" ]]; then
  DEPLOY_CMD+=(--tag "${ENVIRONMENT}-$(date +%Y%m%d-%H%M%S)")
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  printf '%q ' "${DEPLOY_CMD[@]}"
  echo
  echo "DRY RUN - no changes made."
  exit 0
fi

"${DEPLOY_CMD[@]}"

URL="$(gcloud run services describe "${SERVICE_NAME}" \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --format "value(status.url)" 2>/dev/null || true)"
echo "Service URL: ${URL}"

REV="$(gcloud run services describe "${SERVICE_NAME}" \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --format "value(status.traffic[0].revisionName)" 2>/dev/null || true)"
echo "Serving traffic at revision: ${REV}"

exit 0
