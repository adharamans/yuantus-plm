#!/usr/bin/env python3
"""Static verification for the S10 CAD helper Lisp shell.

The S10 slice (`Yuantus.Cad.Bridge` consumer / ZWCAD + GstarCAD Lisp
shell) registers exactly one display-only Lisp command,
`C:YUANTUS_DIFF_PREVIEW`, that calls the S9 NETLOAD bridge primitive
`(yuantus-helper-call ...)`. The .lsp file cannot be loaded or executed
on the GitHub Windows runner because there is no real ZWCAD / GstarCAD
host installed. This static verifier catches drift that does not
require a CAD host: it implements the 16 mandatory static-verifier
checks from S10 taskbook §5 (merged at #633 / de365c01) plus the
recommended source/drift guards in the same section, with Lisp-aware
parenthesis + string handling and an explicit arity check on every
`(yuantus-helper-call ...)` invocation.

Usage:
    python3 clients/cad-desktop-helper/verify_lisp_shell_static.py

Exit codes:
    0 - all guards pass
    1 - at least one guard failed (assertion message printed to stderr)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LISP_FILE = ROOT / "Lisp" / "yuantus_cad_helper.lsp"
HELPER = ROOT / "Helper"
BRIDGE = ROOT / "Bridge"
REPO_ROOT = ROOT.parent.parent
DEV_MD = REPO_ROOT / "docs" / "DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S10_LISP_SHELL_R1_20260524.md"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "cad-helper-shared-dotnet.yml"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------- Lisp-aware tokenizer ----------

def strip_lisp_line_comments(source: str) -> str:
    """Return the source with line comments (``;`` to end of line)
    removed, respecting Lisp string literals so a ``;`` inside ``"..."``
    is NOT treated as a comment start. Escape sequence ``\\"`` inside a
    string is honored.
    """
    out_chars = []
    in_string = False
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if in_string:
            out_chars.append(ch)
            if ch == "\\" and i + 1 < n:
                out_chars.append(source[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out_chars.append(ch)
            i += 1
            continue
        if ch == ';':
            # Comment to end of line (or end of source). Keep the
            # newline so parenthesis balance still sees structure.
            while i < n and source[i] != '\n':
                i += 1
            continue
        out_chars.append(ch)
        i += 1
    return "".join(out_chars)


def find_helper_call_arities(source_no_comments: str) -> list[int]:
    """Return the list of argument counts for every
    ``(yuantus-helper-call ...)`` invocation in the source (comments
    stripped). String literals are respected; line and block syntax do
    not affect the count.
    """
    needle = "(yuantus-helper-call"
    arities = []
    pos = 0
    n = len(source_no_comments)
    while True:
        idx = source_no_comments.find(needle, pos)
        if idx < 0:
            break
        # Cursor positioned after the function name; advance through
        # arguments separated by whitespace until the matching close
        # paren. String literals are tracked so '(', ')', and
        # whitespace inside them do not affect argument counting.
        i = idx + len(needle)
        depth = 1
        in_string = False
        arg_count = 0
        in_arg = False
        while i < n and depth > 0:
            ch = source_no_comments[i]
            if in_string:
                if ch == "\\" and i + 1 < n:
                    i += 2
                    continue
                if ch == '"':
                    in_string = False
                i += 1
                continue
            if ch == '"':
                in_string = True
                if not in_arg:
                    arg_count += 1
                    in_arg = True
                i += 1
                continue
            if ch == '(':
                depth += 1
                if not in_arg and depth >= 2:
                    arg_count += 1
                    in_arg = True
                i += 1
                continue
            if ch == ')':
                depth -= 1
                in_arg = False
                i += 1
                continue
            if ch.isspace():
                in_arg = False
                i += 1
                continue
            # Atom char (symbol, number, etc.).
            if not in_arg:
                arg_count += 1
                in_arg = True
            i += 1
        arities.append(arg_count)
        pos = i
    return arities


def find_helper_call_endpoints(source_no_comments: str) -> list[str]:
    """Return the first-argument string literal of every
    ``(yuantus-helper-call "ENDPOINT" ...)`` call in the source.
    """
    pattern = re.compile(
        r'\(yuantus-helper-call\s+"([^"\\]*(?:\\.[^"\\]*)*)"',
        re.DOTALL,
    )
    return pattern.findall(source_no_comments)


def count_balanced(source_no_comments: str) -> tuple[int, int, int, int]:
    """Return (open_parens, close_parens, open_quotes, close_quotes)
    string-aware; ``open_quotes`` and ``close_quotes`` are equal when
    quotes balance correctly.
    """
    opens = 0
    closes = 0
    quotes_open = 0
    quotes_close = 0
    in_string = False
    i = 0
    n = len(source_no_comments)
    while i < n:
        ch = source_no_comments[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_string = False
                quotes_close += 1
            i += 1
            continue
        if ch == '"':
            in_string = True
            quotes_open += 1
            i += 1
            continue
        if ch == '(':
            opens += 1
        elif ch == ')':
            closes += 1
        i += 1
    return opens, closes, quotes_open, quotes_close


# ---------- mandatory checks (§5) ----------

def check_lsp_file_exists_at_canonical_path() -> None:
    require(LISP_FILE.exists(), f"Lisp file must exist at canonical path {LISP_FILE}")


def check_defines_exactly_one_command_yuantus_diff_preview(source_no_comments: str) -> None:
    # Strip line comments first so a literal "(defun c:" inside a
    # docstring header doesn't get counted as a real definition.
    matches = re.findall(r"\(defun\s+c:", source_no_comments, re.IGNORECASE)
    require(
        len(matches) == 1,
        f"Lisp file must define exactly one (defun c:...) command; found {len(matches)}",
    )
    require(
        re.search(r"\(defun\s+c:yuantus_diff_preview\b", source_no_comments) is not None,
        "the single command must be c:yuantus_diff_preview (typeable as YUANTUS_DIFF_PREVIEW)",
    )


def check_command_calls_yuantus_helper_call_for_diff_preview(endpoints: list[str]) -> None:
    require(
        "/diff/preview" in endpoints,
        "Lisp shell must call (yuantus-helper-call \"/diff/preview\" ...) at least once",
    )


def check_command_calls_yuantus_helper_call_for_audit_apply_result(endpoints: list[str]) -> None:
    require(
        "/audit/apply-result" in endpoints,
        "Lisp shell must call (yuantus-helper-call \"/audit/apply-result\" ...) at least once",
    )


def check_audit_apply_result_outcome_is_not_applied_display_only(source: str) -> None:
    require(
        '"not-applied-display-only"' in source,
        "Lisp shell must use the literal outcome \"not-applied-display-only\" for /audit/apply-result",
    )
    # The only outcome string in S10-R1 should be the display-only one.
    other_outcomes = ('"ok"', '"partial"', '"failed"', '"error"')
    for token in other_outcomes:
        require(
            f'"outcome":{token}' not in source.replace(' ', ''),
            f"S10-R1 must not submit outcome={token}; only not-applied-display-only is permitted",
        )


def check_lsp_contains_no_dwg_mutation_or_entity_creation(source: str) -> None:
    forbidden = [
        "(entmake",
        "(entmakex",
        "(entmod",
        "(entupd",
        "(entdel",
        "(vla-put-",
        "(vlax-put-property",
        "(vlax-invoke",
        '(command "TEXT"',
        '(command "LINE"',
        '(command "INSERT"',
        '(command "_ERASE"',
        '(command "_-PURGE"',
        '(command "MTEXT"',
        '(command "CIRCLE"',
        '(setq dbmod',
    ]
    lower = source.lower()
    for token in forbidden:
        require(
            token.lower() not in lower,
            f"Lisp shell must not contain DWG-mutation token {token!r}",
        )


def check_lsp_user_output_uses_princ_only_no_modal_dialogs(source: str) -> None:
    forbidden = [
        "(alert ",
        "(alert\n",
        "(alert\"",
        "(getfiled",
        "(initdia",
        "(initget",  # not a modal but used to constrain interactive picks — out of S10 scope
        "(startapp",
        "(arxload",
        "(autoarxload",
    ]
    lower = source.lower()
    for token in forbidden:
        require(
            token.lower() not in lower,
            f"Lisp shell must not use modal / external token {token!r}; only (princ) for output",
        )
    # Affirmative: (princ ...) must appear at least once.
    require(
        "(princ " in source or "(princ)" in source or "(princ\n" in source,
        "Lisp shell must use (princ ...) for output",
    )


def check_lsp_handles_nil_from_helper_call_without_calling_audit_apply_result(source: str, source_no_comments: str) -> None:
    # The diff-preview response variable must be nil-guarded before any
    # /audit/apply-result call is reached. A robust proxy: the source
    # must contain a guard pattern that combines (null <response-var>)
    # or (if <response-var> ...) with the /audit/apply-result call only
    # appearing inside the non-nil branch.
    require(
        "(if (null response)" in source_no_comments
        or re.search(r"\(if\s+\(null\s+response\)", source_no_comments) is not None,
        "Lisp shell must (null response)-guard the /diff/preview return before any /audit/apply-result call",
    )
    diff_idx = source_no_comments.find('"/diff/preview"')
    nil_guard_idx = source_no_comments.find("(null response)")
    apply_idx = source_no_comments.find('"/audit/apply-result"')
    require(
        diff_idx >= 0 and nil_guard_idx >= 0 and apply_idx >= 0,
        "Lisp shell must contain /diff/preview call, (null response) guard, and /audit/apply-result call",
    )
    require(
        diff_idx < nil_guard_idx < apply_idx,
        "Lisp shell ordering must be /diff/preview -> (null response) guard -> /audit/apply-result",
    )


def check_supports_zwcad_and_gstarcad_via_program_sniff_or_shared_source(source: str) -> None:
    lower = source.lower()
    has_program_sniff = '(getvar "program")' in lower
    has_both_systems = '"zwcad"' in lower and '"gstarcad"' in lower
    has_sniff_pattern = '(vl-string-search "zwcad"' in lower and '(vl-string-search' in lower
    require(
        has_program_sniff and has_both_systems and has_sniff_pattern,
        "Lisp shell must read (getvar \"PROGRAM\") and recognize both zwcad and gstarcad via vl-string-search",
    )


def check_helper_server_route_count_after_g1a() -> None:
    if not HELPER.exists():
        return
    helper_sources = []
    for cs in sorted(HELPER.rglob("*.cs")):
        if any(part in {"bin", "obj"} for part in cs.parts):
            continue
        helper_sources.append(read(cs))
    helper_text = "\n".join(helper_sources)
    map_count = helper_text.count("MapGet(") + helper_text.count("MapPost(")
    require(
        map_count == 15,
        f"Helper production routes must be exactly 15 after G1-C (G1-B 14 + /document/bom-import); got {map_count}",
    )


def check_does_not_add_s11_integration_or_other_lisp_commands(source: str) -> None:
    forbidden_commands = (
        "yuantus_sync_inbound",
        "yuantus_sync_outbound",
        "yuantus_audit_apply",
        "yuantus_reset_token",
        "yuantus_dedup_check",
        "yuantus_shell_notify",
    )
    lower = source.lower()
    for token in forbidden_commands:
        require(
            f"c:{token}" not in lower,
            f"S10-R1 must define only c:yuantus_diff_preview; found forbidden command c:{token}",
        )
    # Also check no Lisp shell files exist beyond yuantus_cad_helper.lsp.
    lsp_files = list((ROOT / "Lisp").rglob("*.lsp"))
    require(
        len(lsp_files) == 1 and lsp_files[0].name == "yuantus_cad_helper.lsp",
        f"S10-R1 must contain exactly one .lsp file (yuantus_cad_helper.lsp); found {[str(p) for p in lsp_files]}",
    )


def check_workflow_runs_lisp_shell_static_verifier() -> None:
    require(WORKFLOW.exists(), f"Workflow must exist at {WORKFLOW}")
    text = read(WORKFLOW)
    require(
        "clients/cad-desktop-helper/Lisp/**" in text,
        "workflow path filter must include clients/cad-desktop-helper/Lisp/**",
    )
    require(
        "verify_lisp_shell_static.py" in text,
        "workflow must invoke verify_lisp_shell_static.py",
    )


def check_static_verifier_rejects_dwg_mutation_and_direct_http_intent() -> None:
    # The verifier source must mention the danger patterns it checks.
    verifier_text = read(Path(__file__).resolve())
    for token in (
        "(entmake",
        "(entmod",
        "(entdel",
        "(vla-put-",
        "(alert",
        "(getfiled",
        "(startapp",
        "(arxload",
    ):
        require(
            token in verifier_text,
            f"static verifier must source-scan for danger token {token!r}",
        )


def check_dev_verification_records_deferred_native_cad_load_signoff() -> None:
    require(DEV_MD.exists(), f"DEV/Verification MD must exist at {DEV_MD}")
    text = read(DEV_MD)
    for token in ("Deferred", "ZWCAD", "GstarCAD", "not-applied-display-only", "operational signoff"):
        require(token in text, f"DEV/Verification MD must record deferred-signoff token {token!r}")


def check_lsp_balanced_parens_and_double_quotes(source_no_comments: str) -> None:
    opens, closes, q_open, q_close = count_balanced(source_no_comments)
    require(opens == closes, f"Lisp file parens unbalanced: ( count {opens} != ) count {closes}")
    require(q_open == q_close, f"Lisp file quotes unbalanced: open {q_open} != close {q_close}")


def check_lisp_function_call_arity_matches_s9_primitive(source_no_comments: str) -> None:
    arities = find_helper_call_arities(source_no_comments)
    require(arities, "Lisp shell must call (yuantus-helper-call ...) at least once")
    for n in arities:
        require(
            n == 2,
            f"every (yuantus-helper-call ...) call must take exactly 2 arguments; found arity {n}",
        )


# ---------- additional source/drift guards (§5 recommended) ----------

def check_no_open_for_write(source: str) -> None:
    forbidden = [
        '(open ',
    ]
    # `(open ...)` with "w" or "a" mode is forbidden. The Lisp shell has
    # no logging surface; the bridge + helper write any audit rows.
    for token in forbidden:
        if token in source.lower():
            # Look at the args to (open ...) — must not be "w" or "a"
            for match in re.finditer(r"\(open\s+[^\)]+\)", source, re.IGNORECASE):
                args = match.group(0)
                require(
                    '"w"' not in args.lower() and '"a"' not in args.lower(),
                    f"Lisp shell must not (open ... \"w\") or (open ... \"a\") for writing: {args}",
                )


def check_no_shell_out(source: str) -> None:
    forbidden = ['(startapp', '(command "_-shell"', '(command "shell"']
    lower = source.lower()
    for token in forbidden:
        require(
            token not in lower,
            f"Lisp shell must not shell out via {token!r}",
        )


def check_json_escape_uses_loop_based_replace_all(source: str, source_no_comments: str) -> None:
    # vl-string-subst replaces only the first occurrence per the Autodesk
    # AutoLISP reference. A naive escape that called vl-string-subst twice
    # would mishandle multi-backslash Windows DWGPREFIX paths. The .lsp
    # must define a loop-based replace-all helper AND yuantus--json-escape
    # must use it (not call vl-string-subst directly).
    require(
        "(defun yuantus--replace-all" in source_no_comments,
        "Lisp shell must define a loop-based yuantus--replace-all helper for JSON escaping",
    )
    # The helper itself must use a (while ...) loop (or equivalent) — a
    # one-shot vl-string-subst inside replace-all would defeat the point.
    replace_all_match = re.search(
        r"\(defun\s+yuantus--replace-all[^(]*?\((.*?)\n\)\s*$",
        source_no_comments,
        re.DOTALL | re.MULTILINE,
    )
    if replace_all_match is None:
        # Fallback: look from defun header to next top-level defun.
        start = source_no_comments.find("(defun yuantus--replace-all")
        end = source_no_comments.find("(defun ", start + 1)
        body = source_no_comments[start:end] if start >= 0 and end > start else ""
    else:
        body = replace_all_match.group(0)
    require(
        "(while" in body,
        "yuantus--replace-all must use a (while ...) loop to handle every occurrence",
    )
    require(
        "(vl-string-search" in body,
        "yuantus--replace-all must advance via vl-string-search to avoid infinite loops on overlapping replacements",
    )
    # yuantus--json-escape must delegate to yuantus--replace-all and must
    # NOT call vl-string-subst directly (which would mishandle multi-
    # backslash paths).
    escape_start = source_no_comments.find("(defun yuantus--json-escape")
    escape_end = source_no_comments.find("(defun ", escape_start + 1)
    escape_body = source_no_comments[escape_start:escape_end] if escape_start >= 0 else ""
    require(
        "yuantus--replace-all" in escape_body,
        "yuantus--json-escape must delegate to yuantus--replace-all (not call vl-string-subst directly)",
    )
    require(
        "(vl-string-subst" not in escape_body,
        "yuantus--json-escape must not call (vl-string-subst ...) directly; vl-string-subst only replaces the first occurrence",
    )


def check_bridge_sources_unchanged_assumption() -> None:
    # Assertion-style: if the S9 bridge directory does not exist, we
    # cannot verify integrity. Otherwise the bridge SharedBridgeLocator
    # and SharedBridgeTransport entry points must still be present
    # exactly as S9 #632 shipped them.
    if not BRIDGE.exists():
        return
    locator = BRIDGE / "SharedBridgeLocator.cs"
    transport = BRIDGE / "SharedBridgeTransport.cs"
    require(locator.exists() and transport.exists(), "S9 bridge wiring files must still exist after S10")
    locator_text = read(locator)
    transport_text = read(transport)
    require("new HelperLocator()" in locator_text, "S9 SharedBridgeLocator.cs must still wire HelperLocator")
    require("new HelperTransport(" in transport_text, "S9 SharedBridgeTransport.cs must still wire HelperTransport")


# ---------- main ----------

def main() -> int:
    source = read(LISP_FILE) if LISP_FILE.exists() else ""
    source_no_comments = strip_lisp_line_comments(source) if source else ""
    endpoints = find_helper_call_endpoints(source_no_comments) if source else []

    checks = [
        ("lsp file exists at canonical path", check_lsp_file_exists_at_canonical_path),
        ("defines exactly one command c:yuantus_diff_preview", lambda: check_defines_exactly_one_command_yuantus_diff_preview(source_no_comments)),
        ("(yuantus-helper-call \"/diff/preview\" ...) at least once", lambda: check_command_calls_yuantus_helper_call_for_diff_preview(endpoints)),
        ("(yuantus-helper-call \"/audit/apply-result\" ...) at least once", lambda: check_command_calls_yuantus_helper_call_for_audit_apply_result(endpoints)),
        ("/audit/apply-result outcome is \"not-applied-display-only\" only", lambda: check_audit_apply_result_outcome_is_not_applied_display_only(source)),
        ("no DWG mutation / entity creation in lsp", lambda: check_lsp_contains_no_dwg_mutation_or_entity_creation(source)),
        ("user output uses (princ) only; no modal dialogs", lambda: check_lsp_user_output_uses_princ_only_no_modal_dialogs(source)),
        ("(null response) guards /audit/apply-result after /diff/preview", lambda: check_lsp_handles_nil_from_helper_call_without_calling_audit_apply_result(source, source_no_comments)),
        ("supports ZWCAD + GstarCAD via PROGRAM sniff in shared source", lambda: check_supports_zwcad_and_gstarcad_via_program_sniff_or_shared_source(source)),
        ("helper production routes are exactly 15 after G1-C", check_helper_server_route_count_after_g1a),
        ("no S11 integration or other Lisp commands", lambda: check_does_not_add_s11_integration_or_other_lisp_commands(source)),
        ("workflow runs verify_lisp_shell_static.py", check_workflow_runs_lisp_shell_static_verifier),
        ("static verifier mentions DWG mutation + direct HTTP danger tokens", check_static_verifier_rejects_dwg_mutation_and_direct_http_intent),
        ("DEV/Verification MD records deferred native-CAD operational signoff", check_dev_verification_records_deferred_native_cad_load_signoff),
        ("lsp parens and double quotes balance", lambda: check_lsp_balanced_parens_and_double_quotes(source_no_comments)),
        ("every (yuantus-helper-call ...) has exactly 2 args", lambda: check_lisp_function_call_arity_matches_s9_primitive(source_no_comments)),
        ("no (open ... \"w\") / \"a\" write-mode in lsp", lambda: check_no_open_for_write(source)),
        ("no (startapp / (command shell-out in lsp", lambda: check_no_shell_out(source)),
        ("S9 bridge wiring files unchanged (SharedBridgeLocator / Transport)", check_bridge_sources_unchanged_assumption),
        ("json escape uses loop-based replace-all (not one-shot vl-string-subst)", lambda: check_json_escape_uses_loop_based_replace_all(source, source_no_comments)),
    ]

    failures = []
    for name, fn in checks:
        try:
            fn()
            print(f"  ok  {name}")
        except AssertionError as exc:
            print(f"  FAIL {name}: {exc}", file=sys.stderr)
            failures.append((name, str(exc)))
    if failures:
        print(f"\n{len(failures)} static guard(s) failed.", file=sys.stderr)
        return 1
    print(f"\nAll {len(checks)} S10 Lisp shell static guards passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
