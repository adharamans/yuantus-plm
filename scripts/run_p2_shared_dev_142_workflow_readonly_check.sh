#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/run_p2_shared_dev_142_workflow_readonly_check.sh [options]

Options:
  --output-dir <path>       Output root
                            default: ./tmp/p2-shared-dev-142-workflow-readonly-check-<timestamp>
  --baseline-dir <path>     Baseline directory for readonly compare/eval
                            default: ./tmp/p2-shared-dev-observation-20260421-stable
                            tracked fallback dir: ./artifacts/p2-observation/shared-dev-142-readonly-20260421
  --baseline-archive <path> Baseline archive used only when the canonical baseline dir is missing
                            default: ./tmp/p2-shared-dev-observation-20260421-stable.tar.gz
  --baseline-label <label>  Baseline label for readonly diff
                            default: shared-dev-142-readonly-20260421
  --current-label <label>   Current label for readonly diff
                            default: workflow-probe-current
  --base-url <url>          Target API base URL for the workflow probe
                            default: http://142.171.239.56:7910
  --repo <owner/name>       Optional repository override for gh (-R)
  --ref <branch>            Ref used for workflow_dispatch
                            default: main
  --username <name>         Workflow login username when password auth is used
                            default: admin
  --company-id <id>         Optional company filter
  --eco-type <value>        Optional ECO category filter
  --eco-state <value>       Optional ECO state filter
  --deadline-from <iso>     Optional deadline lower bound filter
  --deadline-to <iso>       Optional deadline upper bound filter
  --poll-interval-sec <n>   Poll interval while discovering the run id
                            default: 5
  --max-discovery-sec <n>   Max seconds to wait for run discovery
                            default: 120
  --no-restore              Do not auto-restore the canonical baseline dir from the canonical archive
  --no-archive              Do not write <OUTPUT_DIR>.tar.gz
  -h, --help                Show help

Behavior:
  - runs the fixed shared-dev 142 GitHub workflow probe wrapper
  - if baseline_policy.json says overdue-only-stable, transforms the downloaded workflow artifact into
    an effective stable current before readonly compare/eval
  - compares the effective current against the current official readonly baseline
  - writes:
    - <OUTPUT_DIR>/workflow-probe/WORKFLOW_DISPATCH_RESULT.md
    - <OUTPUT_DIR>/WORKFLOW_READONLY_DIFF.md
    - <OUTPUT_DIR>/WORKFLOW_READONLY_EVAL.md
    - <OUTPUT_DIR>/WORKFLOW_READONLY_CHECK.md

Boundary:
  - this validates the GitHub workflow path against the frozen 142 readonly baseline
  - for the direct local readonly rerun path, use: bash scripts/run_p2_shared_dev_142_readonly_rerun.sh
EOF
}

default_baseline_dir="./tmp/p2-shared-dev-observation-20260421-stable"
default_baseline_archive="./tmp/p2-shared-dev-observation-20260421-stable.tar.gz"
default_tracked_baseline_dir="./artifacts/p2-observation/shared-dev-142-readonly-20260421"
default_baseline_label="shared-dev-142-readonly-20260421"
default_current_label="workflow-probe-current"
timestamp="$(date +%Y%m%d-%H%M%S)"

output_dir="./tmp/p2-shared-dev-142-workflow-readonly-check-${timestamp}"
baseline_dir="${default_baseline_dir}"
baseline_archive="${default_baseline_archive}"
baseline_label="${default_baseline_label}"
current_label="${default_current_label}"
base_url="http://142.171.239.56:7910"
repo=""
ref="main"
username="admin"
company_id=""
eco_type=""
eco_state=""
deadline_from=""
deadline_to=""
poll_interval_sec="5"
max_discovery_sec="120"
allow_restore="1"
archive_result="1"

read_baseline_policy_kind() {
  local policy_file="${baseline_dir}/baseline_policy.json"
  if [[ ! -f "${policy_file}" ]]; then
    echo "exact"
    return 0
  fi

  python3 - <<'PY' "${policy_file}"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
kind = payload.get("kind")
if not isinstance(kind, str) or not kind:
    raise SystemExit("baseline_policy.json must contain a non-empty string kind")
print(kind)
PY
}

require_value() {
  local flag="$1"
  local value="${2:-}"
  if [[ -z "${value}" ]]; then
    echo "Missing value for ${flag}" >&2
    usage >&2
    exit 1
  fi
}

restore_canonical_baseline_if_missing() {
  if [[ -d "${baseline_dir}" ]]; then
    return 0
  fi

  if [[ "${allow_restore}" != "1" ]]; then
    echo "Missing baseline dir: ${baseline_dir}" >&2
    exit 1
  fi

  if [[ "${baseline_dir}" != "${default_baseline_dir}" ]]; then
    echo "Missing custom baseline dir: ${baseline_dir}" >&2
    echo "Auto-restore only supports the canonical shared-dev 142 baseline dir." >&2
    exit 1
  fi

  if [[ ! -f "${baseline_archive}" ]]; then
    if [[ -d "${default_tracked_baseline_dir}" ]]; then
      mkdir -p "$(dirname "${baseline_dir}")"
      rm -rf "${baseline_dir}"
      cp -R "${default_tracked_baseline_dir}" "${baseline_dir}"

      if [[ ! -d "${baseline_dir}" ]]; then
        echo "Tracked repo baseline restore completed but directory still missing: ${baseline_dir}" >&2
        exit 1
      fi

      echo "Restored canonical baseline dir from tracked repo baseline:"
      echo "  ${default_tracked_baseline_dir}"
      return 0
    fi

    echo "Missing baseline dir, archive, and tracked repo baseline: ${baseline_dir} / ${baseline_archive} / ${default_tracked_baseline_dir}" >&2
    exit 1
  fi

  mkdir -p "$(dirname "${baseline_dir}")"
  tar -xzf "${baseline_archive}" -C "$(dirname "${baseline_dir}")"

  if [[ ! -d "${baseline_dir}" ]]; then
    echo "Baseline restore completed but directory still missing: ${baseline_dir}" >&2
    exit 1
  fi

  echo "Restored canonical baseline dir from archive:"
  echo "  ${baseline_archive}"
}

extract_probe_failure_reason() {
  local summary_json="${probe_output_dir}/workflow_dispatch.json"
  if [[ ! -f "${summary_json}" ]]; then
    return 1
  fi

  SUMMARY_JSON="${summary_json}" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["SUMMARY_JSON"])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)

if not isinstance(payload, dict):
    raise SystemExit(1)

reason = payload.get("failure_reason")
if not isinstance(reason, str) or not reason:
    raise SystemExit(1)

print(reason)
PY
}

format_output_ref_or_not_generated() {
  local path="$1"
  if [[ -e "${path}" ]]; then
    printf '\`%s\`' "${path}"
    return 0
  fi
  printf 'not generated'
}

write_failure_summary() {
  local failure_reason="$1"
  local diff_ref
  local eval_ref
  local diff_log_ref
  local eval_log_ref

  diff_ref="$(format_output_ref_or_not_generated "${diff_output}")"
  eval_ref="$(format_output_ref_or_not_generated "${eval_output}")"
  diff_log_ref="$(format_output_ref_or_not_generated "${diff_log_output}")"
  eval_log_ref="$(format_output_ref_or_not_generated "${eval_log_output}")"

  mkdir -p "${output_dir}"
  cat > "${summary_output}" <<EOF
# Shared-dev 142 Workflow Readonly Check

- status: failure
- reason: ${failure_reason}
- workflow_probe_dir: \`${probe_output_dir}\`
- workflow_dispatch_result: \`${probe_output_dir}/WORKFLOW_DISPATCH_RESULT.md\`
- current_artifact_dir: \`${current_dir}\`
- baseline_dir: \`${baseline_dir}\`
- diff: ${diff_ref}
- eval: ${eval_ref}
- diff_log: ${diff_log_ref}
- eval_log: ${eval_log_ref}
- verdict: unavailable

## Next

- open \`${probe_output_dir}/WORKFLOW_DISPATCH_RESULT.md\`
- inspect \`${probe_output_dir}/artifact/WORKFLOW_PRECHECK.md\` when present
- inspect diff/eval log files when present
- if the probe failed during workflow auth precheck, configure \`P2_OBSERVATION_TOKEN\` or \`P2_OBSERVATION_PASSWORD\` for \`p2-observation-regression\`
- if you need the direct local path instead of GitHub workflow dispatch, use:
  - \`bash scripts/run_p2_shared_dev_142_readonly_rerun.sh\`
EOF
}

write_skip_summary() {
  local skip_reason="$1"

  mkdir -p "${output_dir}"
  cat > "${summary_output}" <<EOF
# Shared-dev 142 Workflow Readonly Check

- status: skipped
- reason: ${skip_reason}
- workflow_probe_dir: \`${probe_output_dir}\`
- workflow_dispatch_result: not generated
- current_artifact_dir: \`${current_dir}\`
- baseline_dir: \`${baseline_dir}\`
- diff: not generated
- eval: not generated
- verdict: skipped

## Next

- restore or replace the public shared-dev observation target: \`${base_url}\`
- rerun this guard after the target responds to \`/api/v1/health\`
EOF
}

precheck_probe_target_if_github_actions() {
  if [[ "${GITHUB_ACTIONS:-}" != "true" ]]; then
    return 0
  fi
  if [[ "${baseline_dir}" != "${default_baseline_dir}" ]]; then
    return 0
  fi
  if ! command -v curl >/dev/null 2>&1; then
    write_skip_summary "curl unavailable in GitHub Actions runner"
    return 1
  fi
  if ! curl -fsS --connect-timeout 10 --max-time 30 -o /dev/null "${base_url%/}/api/v1/health"; then
    write_skip_summary "public observation target unavailable: ${base_url%/}/api/v1/health"
    return 1
  fi
  return 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir)
      require_value "$1" "${2:-}"
      output_dir="$2"
      shift 2
      ;;
    --baseline-dir)
      require_value "$1" "${2:-}"
      baseline_dir="$2"
      shift 2
      ;;
    --baseline-archive)
      require_value "$1" "${2:-}"
      baseline_archive="$2"
      shift 2
      ;;
    --baseline-label)
      require_value "$1" "${2:-}"
      baseline_label="$2"
      shift 2
      ;;
    --current-label)
      require_value "$1" "${2:-}"
      current_label="$2"
      shift 2
      ;;
    --base-url)
      require_value "$1" "${2:-}"
      base_url="${2%/}"
      shift 2
      ;;
    --repo)
      require_value "$1" "${2:-}"
      repo="$2"
      shift 2
      ;;
    --ref)
      require_value "$1" "${2:-}"
      ref="$2"
      shift 2
      ;;
    --username)
      require_value "$1" "${2:-}"
      username="$2"
      shift 2
      ;;
    --company-id)
      require_value "$1" "${2:-}"
      company_id="$2"
      shift 2
      ;;
    --eco-type)
      require_value "$1" "${2:-}"
      eco_type="$2"
      shift 2
      ;;
    --eco-state)
      require_value "$1" "${2:-}"
      eco_state="$2"
      shift 2
      ;;
    --deadline-from)
      require_value "$1" "${2:-}"
      deadline_from="$2"
      shift 2
      ;;
    --deadline-to)
      require_value "$1" "${2:-}"
      deadline_to="$2"
      shift 2
      ;;
    --poll-interval-sec)
      require_value "$1" "${2:-}"
      poll_interval_sec="$2"
      shift 2
      ;;
    --max-discovery-sec)
      require_value "$1" "${2:-}"
      max_discovery_sec="$2"
      shift 2
      ;;
    --no-restore)
      allow_restore="0"
      shift
      ;;
    --no-archive)
      archive_result="0"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

output_dir="${output_dir%/}"
probe_output_dir="${output_dir}/workflow-probe"
current_dir="${probe_output_dir}/artifact"
effective_current_dir="${current_dir}"
diff_output="${output_dir}/WORKFLOW_READONLY_DIFF.md"
eval_output="${output_dir}/WORKFLOW_READONLY_EVAL.md"
summary_output="${output_dir}/WORKFLOW_READONLY_CHECK.md"
diff_log_output="${output_dir}/WORKFLOW_READONLY_DIFF.log"
eval_log_output="${output_dir}/WORKFLOW_READONLY_EVAL.log"
archive_path="${output_dir}.tar.gz"

echo "== Shared-dev 142 workflow readonly check =="
echo "OUTPUT_DIR=${output_dir}"
echo "PROBE_OUTPUT_DIR=${probe_output_dir}"
echo "BASELINE_DIR=${baseline_dir}"
echo "BASELINE_ARCHIVE=${baseline_archive}"
echo "BASELINE_LABEL=${baseline_label}"
echo "CURRENT_LABEL=${current_label}"
echo "BASE_URL=${base_url}"
echo "ARCHIVE_RESULT=${archive_result}"
echo

if ! precheck_probe_target_if_github_actions; then
  echo "Skipped:"
  echo "  ${summary_output}"
  exit 0
fi

restore_canonical_baseline_if_missing
baseline_policy_kind="$(read_baseline_policy_kind)"

cmd=(
  bash
  scripts/run_p2_shared_dev_142_workflow_probe.sh
  --base-url "${base_url}"
  --out-dir "${probe_output_dir}"
  --ref "${ref}"
  --username "${username}"
  --poll-interval-sec "${poll_interval_sec}"
  --max-discovery-sec "${max_discovery_sec}"
)

if [[ -n "${repo}" ]]; then
  cmd+=(--repo "${repo}")
fi
if [[ -n "${company_id}" ]]; then
  cmd+=(--company-id "${company_id}")
fi
if [[ -n "${eco_type}" ]]; then
  cmd+=(--eco-type "${eco_type}")
fi
if [[ -n "${eco_state}" ]]; then
  cmd+=(--eco-state "${eco_state}")
fi
if [[ -n "${deadline_from}" ]]; then
  cmd+=(--deadline-from "${deadline_from}")
fi
if [[ -n "${deadline_to}" ]]; then
  cmd+=(--deadline-to "${deadline_to}")
fi

if ! "${cmd[@]}"; then
  probe_failure_reason="$(extract_probe_failure_reason || true)"
  probe_failure_reason="${probe_failure_reason:-workflow probe failed before readonly compare/eval}"
  write_failure_summary "${probe_failure_reason}"
  exit 1
fi

if [[ ! -d "${current_dir}" ]]; then
  echo "Missing workflow artifact dir: ${current_dir}" >&2
  exit 1
fi

if [[ "${baseline_policy_kind}" == "overdue-only-stable" ]]; then
  effective_current_dir="${output_dir}/workflow-probe-stable-current"
  python3 scripts/render_p2_shared_dev_142_stable_current.py \
    "${current_dir}" \
    --output-dir "${effective_current_dir}" \
    --output-md "${output_dir}/WORKFLOW_STABLE_CURRENT_TRANSFORM.md" \
    --output-json "${output_dir}/workflow_stable_current_transform.json" \
    --environment "shared-dev-142-workflow-readonly-stable-current" \
    --operator "workflow"
fi

if ! python3 scripts/compare_p2_observation_results.py \
  "${baseline_dir}" \
  "${effective_current_dir}" \
  --baseline-label "${baseline_label}" \
  --current-label "${current_label}" \
  --output "${diff_output}" \
  >"${diff_log_output}" 2>&1; then
  write_failure_summary "readonly diff generation failed; see ${diff_log_output}"
  exit 1
fi
rm -f "${diff_log_output}"

if ! python3 scripts/evaluate_p2_observation_results.py \
  "${effective_current_dir}" \
  --mode readonly \
  --baseline-dir "${baseline_dir}" \
  --output "${eval_output}" \
  >"${eval_log_output}" 2>&1; then
  write_failure_summary "readonly evaluation failed; see ${eval_log_output}"
  exit 1
fi
rm -f "${eval_log_output}"

verdict="$(
  grep -E '^- verdict: ' "${eval_output}" | head -n 1 | sed 's/^- verdict: //' || true
)"
verdict="${verdict:-unknown}"

mkdir -p "${output_dir}"
cat > "${summary_output}" <<EOF
# Shared-dev 142 Workflow Readonly Check

- workflow_probe_dir: \`${probe_output_dir}\`
- workflow_dispatch_result: \`${probe_output_dir}/WORKFLOW_DISPATCH_RESULT.md\`
- raw_current_artifact_dir: \`${current_dir}\`
- effective_current_dir: \`${effective_current_dir}\`
- baseline_dir: \`${baseline_dir}\`
- diff: \`${diff_output}\`
- eval: \`${eval_output}\`
- verdict: ${verdict}

## Next

- open \`${summary_output}\`
- inspect \`${diff_output}\` and \`${eval_output}\`
- if you need the direct local path instead of GitHub workflow dispatch, use:
  - \`bash scripts/run_p2_shared_dev_142_readonly_rerun.sh\`
EOF

if [[ "${archive_result}" == "1" ]]; then
  mkdir -p "$(dirname "${archive_path}")"
  tar -czf "${archive_path}" -C "$(dirname "${output_dir}")" "$(basename "${output_dir}")"
fi

echo
echo "Done:"
echo "  ${probe_output_dir}/WORKFLOW_DISPATCH_RESULT.md"
if [[ "${baseline_policy_kind}" == "overdue-only-stable" ]]; then
  echo "  ${output_dir}/WORKFLOW_STABLE_CURRENT_TRANSFORM.md"
  echo "  ${output_dir}/workflow_stable_current_transform.json"
fi
echo "  ${diff_output}"
echo "  ${eval_output}"
echo "  ${summary_output}"
if [[ "${archive_result}" == "1" ]]; then
  echo "  ${archive_path}"
fi
