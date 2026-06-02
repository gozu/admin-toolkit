#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: bash scripts/plik_upload.sh <FILE_PATH> [--base-url URL]"
  exit 2
fi

FILE_PATH="$1"
shift

PLIK_BASE_URL="${PLIK_URL:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url)
      PLIK_BASE_URL="${2:-}"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1"
      exit 2
      ;;
  esac
done

if [[ -z "${PLIK_BASE_URL}" ]]; then
  if [[ -f ".plik-url" ]]; then
    PLIK_BASE_URL="$(cat .plik-url)"
  elif [[ -f "$HOME/.plik-url" ]]; then
    PLIK_BASE_URL="$(cat "$HOME/.plik-url")"
  else
    PLIK_BASE_URL="https://dl.dataiku.com"
  fi
fi

if [[ ! -f "${FILE_PATH}" ]]; then
  echo "File not found: ${FILE_PATH}"
  exit 2
fi

UPLOAD_URL="${PLIK_BASE_URL%/}/"
RESPONSE="$(curl -sS -w "\n%{http_code}\n" -F "file=@${FILE_PATH}" "${UPLOAD_URL}")"
HTTP_CODE="$(echo "${RESPONSE}" | tail -n1)"
BODY="$(echo "${RESPONSE}" | sed '$d')"
LINK="$(echo "${BODY}" | tr -d '\r' | head -n1)"

if [[ "${HTTP_CODE}" != "200" ]]; then
  echo "Plik upload failed (HTTP ${HTTP_CODE})"
  echo "${BODY}"
  exit 1
fi

if [[ ! "${LINK}" =~ ^https?:// ]]; then
  echo "Plik upload succeeded but did not return a link"
  echo "${BODY}"
  exit 1
fi

echo "${LINK}"
