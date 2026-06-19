#!/usr/bin/env bash
# smoke_combined_profile.sh -- V1 combined-profile deployment smoke (OPERATOR ARTIFACT).
#
# Runs against a LIVE deployment -- NOT CI, NOT during development. The in-process
# equivalents (base flag-OFF surface unchanged; compose profile flags) are pinned by
#   src/yuantus/api/tests/test_metasheet_bridge_flag_contracts.py
#   src/yuantus/meta_engine/tests/test_ci_contracts_compose_sku_profiles.py
# which DO run in CI. Use this to confirm a real combined deployment is healthy.
#
# Proves:
#   - the combined profile is up and the advisory capability manifest is reachable
#   - a base (YUANTUS_ENABLE_METASHEET=false) deployment leaves the bridge route absent
set -euo pipefail

BASE="${YUANTUS_BASE_URL:?set YUANTUS_BASE_URL of the COMBINED deployment, e.g. http://localhost:8000}"
AUTH="${AUTH_HEADER:?an auth header, e.g. 'Authorization: Bearer <token>'}"
TENANT="${PILOT_TENANT:?the pilot tenant}"
BASE_ONLY_URL="${YUANTUS_BASE_ONLY_URL:-}"  # optional: a base-profile deployment to check bridge absence

fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }
pass() { echo "  ok: $*"; }
command -v jq >/dev/null 2>&1 || fail "jq is required for this smoke (install jq, or curl the endpoints by hand)"

echo "== combined: health 200 =="
curl -fsS -o /dev/null "$BASE/api/v1/health" || fail "combined /health not 200"
pass "combined up"

echo "== combined: advisory capability manifest reachable + advisory:true =="
m=$(curl -fsS -H "$AUTH" -H "x-tenant-id: $TENANT" "$BASE/api/v1/integrations/capabilities")
[ "$(echo "$m" | jq -r '.advisory')" = "true" ] || fail "manifest .advisory != true (advisory must never be an auth source)"
[ "$(echo "$m" | jq -r '.features.bom_multitable.supported')" = "true" ] || fail "bom_multitable not supported in manifest"
pass "manifest advisory:true, bom_multitable.supported:true"

if [ -n "$BASE_ONLY_URL" ]; then
  echo "== base profile: MetaSheet bridge route ABSENT (YUANTUS_ENABLE_METASHEET=false) =="
  code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE_ONLY_URL/api/v1/metasheet-bridge/health")
  [ "$code" = "404" ] || fail "base profile exposes metasheet-bridge (got $code, expected 404)"
  pass "base profile bridge route absent (404)"
else
  echo "  (skip base-profile bridge-absence check: set YUANTUS_BASE_ONLY_URL to enable)"
fi

echo "SMOKE PASS: combined profile"
