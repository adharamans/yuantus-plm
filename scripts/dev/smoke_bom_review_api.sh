#!/usr/bin/env bash
# smoke_bom_review_api.sh -- V1 BOM Review (Path A) smoke (OPERATOR ARTIFACT).
#
# Runs against a LIVE combined-profile deployment -- NOT CI, NOT during development.
# The in-process equivalents are pinned by test_bom_multitable_projection.py and
# test_integration_capabilities.py (which DO run in CI).
#
# Path A = capability manifest + BOM context ONLY. The embed-token mint path is V1.2
# (token / iframe / postMessage) and is NOT exercised in V1 dogfood.
#
# Three Path-A states:
#   1. unentitled tenant     -> context:null AND existing-part == missing-part (no existence leak)
#   2. entitled tenant+part  -> context with .part and .lines[]
#   3. capability manifest    -> bom_multitable.entitled toggles true (entitled) / false (unentitled)
set -euo pipefail

BASE="${YUANTUS_BASE_URL:?set YUANTUS_BASE_URL, e.g. http://localhost:8000}"
ENT_TENANT="${ENTITLED_TENANT:?pilot tenant holding an active plm.bom_multitable license}"
UNENT_TENANT="${UNENTITLED_TENANT:-no-such-tenant}"
PART="${PART_ID:?a Part id that exists for ENTITLED_TENANT}"
MISSING_PART="${MISSING_PART_ID:-01HZZZZZZZZZZZZZZZZZZZZZZZZ}"
AUTH="${AUTH_HEADER:?an auth header, e.g. 'Authorization: Bearer <token>'}"

fail() { echo "SMOKE FAIL: $*" >&2; exit 1; }
pass() { echo "  ok: $*"; }
command -v jq >/dev/null 2>&1 || fail "jq is required for this smoke (install jq, or curl the endpoints by hand)"

ctx_url()  { echo "$BASE/api/v1/bom/multitable/$1/context"; }
caps_url() { echo "$BASE/api/v1/integrations/capabilities"; }

echo "== state 1: unentitled tenant -> context:null + no existence leak =="
u_exist=$(curl -fsS -H "$AUTH" -H "x-tenant-id: $UNENT_TENANT" "$(ctx_url "$PART")")
u_missing=$(curl -fsS -H "$AUTH" -H "x-tenant-id: $UNENT_TENANT" "$(ctx_url "$MISSING_PART")")
[ "$(echo "$u_exist" | jq -r '.context')" = "null" ] || fail "unentitled context not null"
[ "$u_exist" = "$u_missing" ] || fail "existing vs missing differ for unentitled (existence leak)"
pass "unentitled -> context:null, existing==missing"

echo "== state 2: entitled tenant -> context with part + lines[] =="
e=$(curl -fsS -H "$AUTH" -H "x-tenant-id: $ENT_TENANT" "$(ctx_url "$PART")")
[ "$(echo "$e" | jq -r '.entitled')" = "true" ] || fail "entitled flag not true (check PLM_TENANT_ID / x-tenant-id routing)"
[ "$(echo "$e" | jq -r '.context.part.part_id')" = "$PART" ] || fail "context.part.part_id mismatch"
[ "$(echo "$e" | jq -r '.context.lines | type')" = "array" ] || fail "context.lines not an array"
pass "entitled -> context.part + context.lines[]"

echo "== state 3: capability manifest reflects entitlement (advisory, never an auth source) =="
me=$(curl -fsS -H "$AUTH" -H "x-tenant-id: $ENT_TENANT" "$(caps_url)")
mu=$(curl -fsS -H "$AUTH" -H "x-tenant-id: $UNENT_TENANT" "$(caps_url)")
[ "$(echo "$me" | jq -r '.advisory')" = "true" ] || fail "manifest .advisory != true"
[ "$(echo "$me" | jq -r '.features.bom_multitable.entitled')" = "true" ] || fail "entitled tenant: manifest entitled != true"
[ "$(echo "$mu" | jq -r '.features.bom_multitable.entitled')" = "false" ] || fail "unentitled tenant: manifest entitled != false"
pass "manifest entitled toggles true/false by tenant"

echo "SMOKE PASS: BOM Review Path A (manifest + context). embed-token mint is V1.2 -- not required for V1 dogfood."
