#!/usr/bin/env bash
set -euo pipefail

METHOD="${1:-GET}"
PATH_OR_URL="${2:-}"
shift $(( $# >= 2 ? 2 : $# ))

if [[ -z "${PATH_OR_URL}" ]]; then
  echo "Usage: bash scripts/dss_api.sh <METHOD> <PATH_OR_URL> [--base-url URL] [--data JSON] [--form KEY=VALUE] [--header 'K: V'] [--raw]"
  exit 2
fi

BASE_URL="${DSS_URL:-}"
DATA_PAYLOAD=""
RAW_ONLY="false"
FORM_ARGS=()
EXTRA_HEADERS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url)
      BASE_URL="${2:-}"
      shift 2
      ;;
    --data)
      DATA_PAYLOAD="${2:-}"
      shift 2
      ;;
    --form)
      FORM_ARGS+=("${2:-}")
      shift 2
      ;;
    --header)
      EXTRA_HEADERS+=("${2:-}")
      shift 2
      ;;
    --raw)
      RAW_ONLY="true"
      shift
      ;;
    *)
      echo "Unknown argument: $1"
      exit 2
      ;;
  esac
done

if [[ -z "${BASE_URL}" ]]; then
  if [[ -f ".dss-url" ]]; then
    BASE_URL="$(cat .dss-url)"
  elif [[ -f "$HOME/.dss-url" ]]; then
    BASE_URL="$(cat "$HOME/.dss-url")"
  fi
fi

if [[ -z "${BASE_URL}" && ! "${PATH_OR_URL}" =~ ^https?:// ]]; then
  echo "Missing DSS base URL. Pass --base-url or set DSS_URL/.dss-url."
  exit 2
fi

API_KEY="${DSS_API_KEY:-}"
if [[ -z "${API_KEY}" ]]; then
  if [[ -f ".dss-api-key" ]]; then
    API_KEY="$(cat .dss-api-key)"
  elif [[ -f "$HOME/.dss-api-key" ]]; then
    API_KEY="$(cat "$HOME/.dss-api-key")"
  fi
fi

if [[ -z "${API_KEY}" ]]; then
  echo "Missing DSS API key. Set DSS_API_KEY or .dss-api-key."
  exit 2
fi

NORMALIZED_PATH="${PATH_OR_URL}"
if [[ ! "${PATH_OR_URL}" =~ ^https?:// ]]; then
  if [[ "${NORMALIZED_PATH}" == /public/api/* ]] && [[ "${BASE_URL}" =~ ^http://(localhost|127\.0\.0\.1):10000(/|$) ]]; then
    NORMALIZED_PATH="${NORMALIZED_PATH/#\/public\/api/\/dip\/publicapi}"
  fi
  URL="${BASE_URL%/}/${NORMALIZED_PATH#/}"
else
  URL="${PATH_OR_URL}"
fi

curl_args=(
  -sS
  -X "$METHOD"
  -H "Authorization: Bearer ${API_KEY}"
  "$URL"
)

if [[ -n "${DATA_PAYLOAD}" ]]; then
  curl_args+=(
    -H "Content-Type: application/json"
    --data "${DATA_PAYLOAD}"
  )
fi

for form_arg in "${FORM_ARGS[@]}"; do
  curl_args+=(-F "${form_arg}")
done

for hdr in "${EXTRA_HEADERS[@]}"; do
  curl_args+=(-H "${hdr}")
done

if [[ "${RAW_ONLY}" == "true" ]]; then
  curl "${curl_args[@]}"
else
  curl "${curl_args[@]}" -w "\n%{http_code}\n"
fi
