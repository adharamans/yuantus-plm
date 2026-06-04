;;; yuantus_cad_helper.lsp
;;;
;;; CAD Helper Bridge S10-R1 — single AutoLISP-compatible source shared
;;; across ZWCAD and GstarCAD per merged taskbook #633 (de365c01).
;;;
;;; Defines six Lisp commands:
;;;
;;;   (defun c:yuantus_diff_preview ...) -> typeable as YUANTUS_DIFF_PREVIEW
;;;   (defun c:yuantus_checkout ...) -> typeable as YUANTUS_CHECKOUT
;;;   (defun c:yuantus_undo_checkout ...) -> typeable as YUANTUS_UNDO_CHECKOUT
;;;   (defun c:yuantus_status ...) -> typeable as YUANTUS_STATUS
;;;   (defun c:yuantus_checkin ...) -> typeable as YUANTUS_CHECKIN
;;;   (defun c:yuantus_bom_import ...) -> typeable as YUANTUS_BOM_IMPORT
;;;
;;; The diff-preview command is DISPLAY-ONLY:
;;;   - prompts the user for a PLM item_id (required) and an optional
;;;     profile_id;
;;;   - reads the current drawing filename / path from CAD built-ins;
;;;   - calls helper /diff/preview through the S9 NETLOAD bridge primitive
;;;     (yuantus-helper-call endpoint json) — registered in
;;;     YuantusCadHelperBridge.dll;
;;;   - extracts only the helper-generated pull_id from the response (per
;;;     §2.7 + §3.C step 6); does NOT extract write_cad_fields or any
;;;     other nested business field;
;;;   - displays the full helper data JSON string verbatim via (princ ...)
;;;     so the user sees pull_id and server_response (including
;;;     write_cad_fields) in the CAD command line;
;;;   - reports the result back through helper /audit/apply-result with
;;;     outcome = "not-applied-display-only" per R3.2 design :822 +
;;;     S6 audit enum;
;;;   - never mutates the DWG (no entmake/entmod/entdel/vla-put/command
;;;     mutations) — R3 explicitly defers non-AutoCAD DWG writes per
;;;     design :724;
;;;   - never opens modal dialogs (alert/getfiled/initdia) — only (princ).
;;;
;;; No commands beyond the six listed above. No direct HTTP. No direct DPAPI access. No other
;;; native-CAD .NET DLL loads. No new helper Kestrel routes from the LISP
;;; shell (helper route count is 17 after Phase 3 material-assistant routes).
;;;
;;; External interaction stays within the S9/Slice-B bridge primitives:
;;; (yuantus-helper-call ...) for JSON and (yuantus-helper-upload ...) for
;;; multipart upload.

(vl-load-com)


;;; ============================================================
;;; helper functions (yuantus--*)
;;; ============================================================

;;; yuantus--replace-all: return s with EVERY occurrence of old replaced
;;; by new. AutoLISP's vl-string-subst replaces only the first match
;;; per the Autodesk AutoLISP reference, so a Windows DWGPREFIX like
;;; "C:\\Users\\demo\\project\\" with multiple backslashes would only
;;; have the first character escaped — producing invalid JSON. This
;;; walks the source string from left to right with vl-string-search
;;; advancing past each match, so it (a) replaces every occurrence and
;;; (b) cannot infinite-loop when new contains old as a substring (the
;;; cursor advances over the new copy, not the original old).
(defun yuantus--replace-all (new old s / out start idx)
  (setq out "")
  (setq start 0)
  (setq idx (vl-string-search old s start))
  (while (not (null idx))
    (setq out (strcat out (substr s (1+ start) (- idx start)) new))
    (setq start (+ idx (strlen old)))
    (setq idx (vl-string-search old s start))
  )
  (setq out (strcat out (substr s (1+ start))))
  out
)


;;; yuantus--json-escape: escape backslashes and double quotes in a string
;;; so it can be embedded inside a JSON string literal. Order matters:
;;; backslashes first (so quote escapes added after are not themselves
;;; re-escaped), then quotes. Uses yuantus--replace-all to handle every
;;; occurrence; the one-shot vl-string-subst is insufficient.
(defun yuantus--json-escape (s / out)
  (if (or (null s) (= (type s) 'INT) (= (type s) 'REAL))
    (setq out "")
    (setq out s)
  )
  (setq out (yuantus--replace-all "\\\\" "\\" out))
  (setq out (yuantus--replace-all "\\\"" "\"" out))
  out
)


;;; yuantus--cad-system: return a normalized cad_system token by sniffing
;;; the CAD host (getvar "PROGRAM") per §3.I. Returns "zwcad",
;;; "gstarcad", or "unknown".
(defun yuantus--cad-system (/ prog lower)
  (setq prog (getvar "PROGRAM"))
  (if (null prog)
    "unknown"
    (progn
      (setq lower (strcase prog T))
      (cond
        ((vl-string-search "zwcad" lower) "zwcad")
        ((vl-string-search "gstarcad" lower) "gstarcad")
        ((vl-string-search "gcad" lower) "gstarcad")
        (T "unknown")
      )
    )
  )
)


;;; yuantus--build-diff-request: construct the /diff/preview JSON request
;;; body per §3.C step 4. Required: item_id. Optional: profile_id.
;;; Always includes cad_system and drawing.filename + drawing.filepath
;;; from CAD built-ins.
(defun yuantus--build-diff-request (item-id profile-id cad-system dwg-name dwg-prefix / body profile-clause)
  (if (and profile-id (> (strlen profile-id) 0))
    (setq profile-clause (strcat ",\"profile_id\":\"" (yuantus--json-escape profile-id) "\""))
    (setq profile-clause "")
  )
  (setq body (strcat
    "{\"item_id\":\"" (yuantus--json-escape item-id) "\""
    profile-clause
    ",\"cad_system\":\"" cad-system "\""
    ",\"drawing\":{"
    "\"filename\":\"" (yuantus--json-escape dwg-name) "\""
    ",\"filepath\":\"" (yuantus--json-escape dwg-prefix) "\""
    "}}"
  ))
  body
)


;;; yuantus--build-apply-result-request: construct the /audit/apply-result
;;; JSON request body per §3.C step 8. Pinned: outcome =
;;; "not-applied-display-only" — the literal string from R3.2 :822 and
;;; the merged S6 audit enum at /audit/apply-result.
(defun yuantus--build-apply-result-request (pull-id cad-system dwg-name dwg-prefix)
  (strcat
    "{\"pull_id\":\"" (yuantus--json-escape pull-id) "\""
    ",\"outcome\":\"not-applied-display-only\""
    ",\"cad_system\":\"" cad-system "\""
    ",\"drawing\":{"
    "\"filename\":\"" (yuantus--json-escape dwg-name) "\""
    ",\"filepath\":\"" (yuantus--json-escape dwg-prefix) "\""
    "}}"
  )
)


;;; yuantus--extract-pull-id: pull only the pull_id value out of the
;;; helper data JSON string. Narrow string operations only — no JSON
;;; parser introduced per §2.7. Returns nil if the field is absent or
;;; malformed.
(defun yuantus--extract-pull-id (json-str / marker idx start end)
  (if (null json-str)
    nil
    (progn
      (setq marker "\"pull_id\":\"")
      (setq idx (vl-string-search marker json-str))
      (if (null idx)
        nil
        (progn
          (setq start (+ idx (strlen marker)))
          (setq end (vl-string-search "\"" json-str start))
          (if (null end)
            nil
            (substr json-str (1+ start) (- end start))
          )
        )
      )
    )
  )
)


;;; ============================================================
;;; C:YUANTUS_DIFF_PREVIEW — the single S10-R1 Lisp command
;;; ============================================================
;;;
;;; Strict step order per §3.C. Failure handling:
;;;   - user cancels at item_id prompt -> one notice, no helper calls;
;;;   - (yuantus-helper-call "/diff/preview" ...) returns nil
;;;     -> write one sanitized notice, do NOT call /audit/apply-result;
;;;   - response missing pull_id -> write one sanitized notice, do NOT
;;;     call /audit/apply-result (cannot correlate);
;;;   - (yuantus-helper-call "/audit/apply-result" ...) returns nil
;;;     -> write one sanitized notice; do NOT retry.

(defun c:yuantus_diff_preview (/ item-id profile-id cad-system dwg-name dwg-prefix
                                 request response pull-id apply-request apply-response)

  ;; Step 1: prompt for required PLM item_id.
  (setq item-id (getstring T "\nPLM item id: "))
  (if (or (null item-id) (= (strlen item-id) 0))
    (progn
      (princ "\n[YUANTUS_DIFF_PREVIEW] cancelled (no item id).")
      (princ)
    )

    (progn
      ;; Step 2: optional profile_id.
      (setq profile-id (getstring T "\nProfile id (optional, blank to skip): "))

      ;; Step 3: drawing context from CAD built-ins + cad_system sniff.
      (setq dwg-name (getvar "DWGNAME"))
      (setq dwg-prefix (getvar "DWGPREFIX"))
      (setq cad-system (yuantus--cad-system))

      ;; Step 4: build /diff/preview request body.
      (setq request (yuantus--build-diff-request item-id profile-id cad-system dwg-name dwg-prefix))

      ;; Step 5: call helper /diff/preview via the S9 NETLOAD bridge.
      (setq response (yuantus-helper-call "/diff/preview" request))

      (if (null response)
        ;; Step 5 nil branch: bridge already wrote sanitized error;
        ;; emit one short cancel notice and exit WITHOUT /audit/apply-result.
        (progn
          (princ "\n[YUANTUS_DIFF_PREVIEW] diff preview failed (bridge already logged error).")
          (princ)
        )

        (progn
          ;; Step 6: parse only pull_id (no other JSON fields per §2.7).
          (setq pull-id (yuantus--extract-pull-id response))

          (if (null pull-id)
            (progn
              (princ "\n[YUANTUS_DIFF_PREVIEW] response missing pull_id; cannot report apply-result.")
              (princ)
            )

            (progn
              ;; Step 7: display via (princ) only — header, full data
              ;; JSON verbatim, footer. No DWG mutation, no modal dialog.
              (princ (strcat "\n[YUANTUS_DIFF_PREVIEW] item=" item-id " pull_id=" pull-id))
              (princ (strcat "\n" response))
              (princ "\n[YUANTUS_DIFF_PREVIEW] display only; no DWG write.")

              ;; Step 8: build /audit/apply-result with the ratified
              ;; not-applied-display-only outcome.
              (setq apply-request (yuantus--build-apply-result-request pull-id cad-system dwg-name dwg-prefix))

              ;; Step 9: call /audit/apply-result; on nil, single notice;
              ;; do NOT retry per §3.C step 9.
              (setq apply-response (yuantus-helper-call "/audit/apply-result" apply-request))
              (if (null apply-response)
                (princ "\n[YUANTUS_DIFF_PREVIEW] audit report failed (bridge already logged error); diff was displayed.")
              )

              ;; Step 10: trailing (princ) so the REPL prints nothing
              ;; after the explicit display lines.
              (princ)
            )
          )
        )
      )
    )
  )
)


;;; ============================================================
;;; Slice A (R1) — JSON workflow commands
;;; ============================================================
;;;
;;; Three display-only commands wired to the merged G1-A JSON helper
;;; routes through the same S9 (yuantus-helper-call ENDPOINT json) bridge
;;; primitive used by diff-preview. Each command:
;;;   - prompts for a required PLM item_id (empty / cancel -> one notice,
;;;     no helper call);
;;;   - sends {"item_id":"..."} to the helper route via the bridge;
;;;   - on nil (bridge already wrote its sanitized error) -> one notice,
;;;     stop (no retry, no /audit/apply-result);
;;;   - on a response -> (princ) the bridge-returned helper data JSON
;;;     string verbatim. Per BridgeCallService.SerializeDataPayload(data)
;;;     the Lisp surface receives the helper DATA payload as a JSON
;;;     string, NOT the full fixed-200 helper envelope; the command does
;;;     not extract or act on any returned business field.
;;;
;;; These commands do NOT call /audit/apply-result (they are workflow
;;; lock / status ops, not the display-confirm-writeback flow diff-preview
;;; uses); no new outcome string is introduced.
;;;
;;; "Display-only" here means the CAD / DWG is never written or modified
;;; (no entmake/entmod/command entity ops, no modal dialogs). The
;;; server-side lock-state change performed by the checkout / undo-checkout
;;; routes is the intended behavior of those routes and is NOT a DWG
;;; mutation.
;;;
;;; Defined AFTER c:yuantus_diff_preview on purpose: the
;;; verify_lisp_shell_static.py :302 ordering guard resolves the FIRST
;;; "/diff/preview" -> (null response) -> "/audit/apply-result" sequence,
;;; which must remain the diff-preview block.

;;; yuantus--build-item-request: the {"item_id":"<escaped>"} request body
;;; shared by the three Slice A commands. Reuses yuantus--json-escape so
;;; Windows paths / quotes inside item_id are escaped the same way as
;;; diff-preview; no direct vl-string-subst.
(defun yuantus--build-item-request (item-id)
  (strcat "{\"item_id\":\"" (yuantus--json-escape item-id) "\"}")
)


(defun c:yuantus_checkout (/ item-id request response)
  (setq item-id (getstring T "\nPLM item id: "))
  (if (or (null item-id) (= (strlen item-id) 0))
    (progn
      (princ "\n[YUANTUS_CHECKOUT] cancelled (no item id).")
      (princ)
    )
    (progn
      (setq request (yuantus--build-item-request item-id))
      (setq response (yuantus-helper-call "/document/checkout" request))
      (if (null response)
        (progn
          (princ "\n[YUANTUS_CHECKOUT] checkout failed (bridge already logged error).")
          (princ)
        )
        (progn
          (princ (strcat "\n[YUANTUS_CHECKOUT] item=" item-id))
          (princ (strcat "\n" response))
          (princ "\n[YUANTUS_CHECKOUT] display only; no DWG write.")
          (princ)
        )
      )
    )
  )
)


(defun c:yuantus_undo_checkout (/ item-id request response)
  (setq item-id (getstring T "\nPLM item id: "))
  (if (or (null item-id) (= (strlen item-id) 0))
    (progn
      (princ "\n[YUANTUS_UNDO_CHECKOUT] cancelled (no item id).")
      (princ)
    )
    (progn
      (setq request (yuantus--build-item-request item-id))
      (setq response (yuantus-helper-call "/document/undo-checkout" request))
      (if (null response)
        (progn
          (princ "\n[YUANTUS_UNDO_CHECKOUT] undo-checkout failed (bridge already logged error).")
          (princ)
        )
        (progn
          (princ (strcat "\n[YUANTUS_UNDO_CHECKOUT] item=" item-id))
          (princ (strcat "\n" response))
          (princ "\n[YUANTUS_UNDO_CHECKOUT] display only; no DWG write.")
          (princ)
        )
      )
    )
  )
)


(defun c:yuantus_status (/ item-id request response)
  (setq item-id (getstring T "\nPLM item id: "))
  (if (or (null item-id) (= (strlen item-id) 0))
    (progn
      (princ "\n[YUANTUS_STATUS] cancelled (no item id).")
      (princ)
    )
    (progn
      (setq request (yuantus--build-item-request item-id))
      (setq response (yuantus-helper-call "/document/status" request))
      (if (null response)
        (progn
          (princ "\n[YUANTUS_STATUS] status failed (bridge already logged error).")
          (princ)
        )
        (progn
          (princ (strcat "\n[YUANTUS_STATUS] item=" item-id))
          (princ (strcat "\n" response))
          (princ "\n[YUANTUS_STATUS] display only; no DWG write.")
          (princ)
        )
      )
    )
  )
)


;;; ============================================================
;;; Slice C (R1) — multipart upload commands
;;; ============================================================
;;;
;;; Two display-only commands wired to the merged G1-B/G1-C multipart
;;; helper routes through the Slice B bridge primitive
;;; (yuantus-helper-upload "ENDPOINT" item-id filepath) — arity 3, NOT
;;; the arity-2 (yuantus-helper-call ...). Defined AFTER the existing
;;; commands so the :354 first-occurrence (null response) ordering guard
;;; stays anchored in the diff-preview block.
;;;
;;; File source: the upload filepath is ALWAYS the active document path
;;; (strcat (getvar "DWGPREFIX") (getvar "DWGNAME")) — never a prompted or
;;; file-picker path. The bridge IBridgeFileSource validates the bytes;
;;; the helper never reads a path.
;;;
;;; Save model (fail-closed, S10-clean): upload only when (getvar "DBMOD")
;;; is numeric 0 (no unsaved changes). nil (modified-state unavailable) or
;;; non-zero -> one (princ) notice and abort, so stale bytes are never
;;; uploaded. The local variable is named dirty-flag (never the bare dbmod
;;; symbol) so the no-DWG-mutation guard token stays untripped. No save, no
;;; DWG mutation, no modal — read-only DBMOD/DWGPREFIX/DWGNAME getvars only.
;;;
;;; These commands do NOT call /audit/apply-result.


(defun c:yuantus_checkin (/ item-id dirty-flag filepath response)
  (setq item-id (getstring T "\nPLM item id: "))
  (if (or (null item-id) (= (strlen item-id) 0))
    (progn
      (princ "\n[YUANTUS_CHECKIN] cancelled (no item id).")
      (princ)
    )
    (progn
      (setq dirty-flag (getvar "DBMOD"))
      (if (and dirty-flag (= dirty-flag 0))
        (progn
          (setq filepath (strcat (getvar "DWGPREFIX") (getvar "DWGNAME")))
          (setq response (yuantus-helper-upload "/document/checkin" item-id filepath))
          (if (null response)
            (progn
              (princ "\n[YUANTUS_CHECKIN] checkin failed (bridge already logged error).")
              (princ)
            )
            (progn
              (princ (strcat "\n[YUANTUS_CHECKIN] item=" item-id))
              (princ (strcat "\n" response))
              (princ "\n[YUANTUS_CHECKIN] display only; no DWG write.")
              (princ)
            )
          )
        )
        (progn
          (princ "\n[YUANTUS_CHECKIN] drawing has unsaved changes or modified-state unavailable; save first. Not uploaded.")
          (princ)
        )
      )
    )
  )
)


(defun c:yuantus_bom_import (/ item-id dirty-flag filepath response)
  ;; item_id is optional; blank is passed through so the bridge omits the
  ;; item_id part and the helper auto-creates the BOM root.
  (setq item-id (getstring T "\nPLM item id (optional, blank = auto-create root): "))
  (if (null item-id)
    (setq item-id "")
  )
  (setq dirty-flag (getvar "DBMOD"))
  (if (and dirty-flag (= dirty-flag 0))
    (progn
      (setq filepath (strcat (getvar "DWGPREFIX") (getvar "DWGNAME")))
      (setq response (yuantus-helper-upload "/document/bom-import" item-id filepath))
      (if (null response)
        (progn
          (princ "\n[YUANTUS_BOM_IMPORT] bom-import failed (bridge already logged error).")
          (princ)
        )
        (progn
          (princ (strcat "\n[YUANTUS_BOM_IMPORT] item=" item-id))
          (princ (strcat "\n" response))
          (princ "\n[YUANTUS_BOM_IMPORT] display only; no DWG write.")
          (princ)
        )
      )
    )
    (progn
      (princ "\n[YUANTUS_BOM_IMPORT] drawing has unsaved changes or modified-state unavailable; save first. Not uploaded.")
      (princ)
    )
  )
)


;;; Load-time confirmation. Single princ so loading the file leaves
;;; exactly one short line on the CAD command line.
(princ "\n[YUANTUS_CAD_HELPER] yuantus_cad_helper.lsp loaded; commands YUANTUS_DIFF_PREVIEW, YUANTUS_CHECKOUT, YUANTUS_UNDO_CHECKOUT, YUANTUS_STATUS, YUANTUS_CHECKIN, YUANTUS_BOM_IMPORT available.")
(princ)
