#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: verify_compose_sku_profiles.sh [--render PROFILE]

Render and validate the SKU compose overlays:
  - base
  - collab
  - combined

Environment:
  METASHEET2_ROOT   Path to the metasheet2 checkout used by the combined profile.
                    Default: ../metasheet2 (relative to the Yuantus repo root)
  TMPDIR            Optional temp directory root for rendered compose output.

Options:
  --render PROFILE  Print the rendered config for one profile (base|collab|combined)
  --help            Show this help text
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

RENDER_PROFILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --render)
      [[ $# -ge 2 ]] || die "--render requires one of: base, collab, combined"
      RENDER_PROFILE="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

case "${RENDER_PROFILE}" in
  ""|base|collab|combined)
    ;;
  *)
    die "Unsupported profile for --render: ${RENDER_PROFILE}"
    ;;
esac

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  die "docker compose (or docker-compose) is required"
fi

ROOT_FILE="${REPO_ROOT}/docker-compose.yml"
BASE_FILE="${REPO_ROOT}/docker-compose.profile-base.yml"
COLLAB_FILE="${REPO_ROOT}/docker-compose.profile-collab.yml"
COMBINED_FILE="${REPO_ROOT}/docker-compose.profile-combined.yml"

[[ -f "${ROOT_FILE}" ]] || die "Missing ${ROOT_FILE}"
[[ -f "${BASE_FILE}" ]] || die "Missing ${BASE_FILE}"
[[ -f "${COLLAB_FILE}" ]] || die "Missing ${COLLAB_FILE}"
[[ -f "${COMBINED_FILE}" ]] || die "Missing ${COMBINED_FILE}"

if [[ -z "${RENDER_PROFILE}" || "${RENDER_PROFILE}" == "combined" ]]; then
  METASHEET2_ROOT="${METASHEET2_ROOT:-${REPO_ROOT}/../metasheet2}"
  [[ -d "${METASHEET2_ROOT}" ]] || die "METASHEET2_ROOT does not exist: ${METASHEET2_ROOT}"
  [[ -f "${METASHEET2_ROOT}/Dockerfile.backend" ]] || die "Missing ${METASHEET2_ROOT}/Dockerfile.backend"
  [[ -f "${METASHEET2_ROOT}/Dockerfile.frontend" ]] || die "Missing ${METASHEET2_ROOT}/Dockerfile.frontend"
fi

OUT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/verify-compose-sku-profiles-XXXXXX")"
trap 'rm -rf "${OUT_DIR}"' EXIT

render_profile() {
  local name="$1"
  local overlay="$2"
  local out="${OUT_DIR}/${name}.yml"

  if [[ -n "${METASHEET2_ROOT:-}" ]]; then
    METASHEET2_ROOT="${METASHEET2_ROOT}" "${COMPOSE_CMD[@]}" \
      -f "${ROOT_FILE}" \
      -f "${overlay}" \
      config >"${out}"
  else
    "${COMPOSE_CMD[@]}" \
      -f "${ROOT_FILE}" \
      -f "${overlay}" \
      config >"${out}"
  fi

  case "${name}" in
    base)
      grep -Fq "YUANTUS_DELIVERY_PROFILE: base" "${out}" || die "base render missing YUANTUS_DELIVERY_PROFILE=base"
      grep -Fq 'YUANTUS_ENABLE_METASHEET: "false"' "${out}" || die "base render missing YUANTUS_ENABLE_METASHEET=false"
      ;;
    collab)
      grep -Fq "YUANTUS_DELIVERY_PROFILE: collab" "${out}" || die "collab render missing YUANTUS_DELIVERY_PROFILE=collab"
      grep -Fq 'YUANTUS_ENABLE_METASHEET: "true"' "${out}" || die "collab render missing YUANTUS_ENABLE_METASHEET=true"
      ;;
    combined)
      grep -Fq "YUANTUS_DELIVERY_PROFILE: combined" "${out}" || die "combined render missing YUANTUS_DELIVERY_PROFILE=combined"
      grep -Fq 'YUANTUS_ENABLE_METASHEET: "true"' "${out}" || die "combined render missing YUANTUS_ENABLE_METASHEET=true"
      grep -Fq "PLM_API_MODE: yuantus" "${out}" || die "combined render missing PLM_API_MODE=yuantus"
      grep -Fq "PLM_BASE_URL: http://api:7910" "${out}" || die "combined render missing PLM_BASE_URL=http://api:7910"
      grep -Fq "backend:" "${out}" || die "combined render missing backend service"
      grep -Fq "web:" "${out}" || die "combined render missing web service"
      ;;
  esac

  echo "${name}: ok (${out})"
  if [[ -n "${RENDER_PROFILE}" && "${RENDER_PROFILE}" == "${name}" ]]; then
    cat "${out}"
  fi
}

if [[ -n "${RENDER_PROFILE}" ]]; then
  case "${RENDER_PROFILE}" in
    base)
      render_profile base "${BASE_FILE}"
      ;;
    collab)
      render_profile collab "${COLLAB_FILE}"
      ;;
    combined)
      render_profile combined "${COMBINED_FILE}"
      ;;
  esac
  exit 0
fi

render_profile base "${BASE_FILE}"
render_profile collab "${COLLAB_FILE}"
render_profile combined "${COMBINED_FILE}"
