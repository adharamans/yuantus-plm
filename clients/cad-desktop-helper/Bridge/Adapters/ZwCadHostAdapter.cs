// CAD-host adapter for the S9 NETLOAD Lisp bridge — ZWCAD (中望) host.
//
// This is the ZWCAD counterpart of AutoCadHostAdapter.cs / GstarCadHostAdapter.cs.
// It registers the SAME two Lisp transport primitives — `(yuantus-helper-call ...)`
// and `(yuantus-helper-upload ...)` — but binds to ZWCAD's managed .NET API
// (the `ZwSoft.ZwCAD.*` namespaces; assemblies ZwManaged.dll + ZwDatabaseMgd.dll
// in the ZWCAD install folder) instead of the AutoCAD `Autodesk.AutoCAD.*`
// assemblies. ZWCAD's .NET API mirrors the AutoCAD .NET API shape (LispFunction /
// ResultBuffer / TypedValue / LispDataType / Editor.WriteMessage), so this file
// is a near-mechanical namespace port of the AutoCAD adapter and keeps the exact
// same transport-only contract.
//
// Like the AutoCAD/GstarCAD adapters, the host-bound code is excluded from the
// SDK-free CI build via a compilation symbol (`ZWCAD_HOST`) because the ZWCAD
// managed assemblies are not present on the CI runner. Build for a real host by
// defining ZWCAD_HOST and referencing the two ZWCAD managed assemblies — see
// docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_ZWCAD_HOST_ADAPTER_R1_20260621.md.
//
// Operational NETLOAD evidence (DLL loads inside ZWCAD.exe, the ZWCAD
// command-line writer prints sanitized error lines, the six in-CAD commands run
// against a live helper + PLM) is DEFERRED to native-CAD operational signoff per
// docs/CAD_HELPER_BRIDGE_NATIVE_CAD_OPERATIONAL_SIGNOFF_RUNBOOK_20260527.md
// — exactly as for the AutoCAD/GstarCAD hosts.

#if ZWCAD_HOST

using System;
using ZwSoft.ZwCAD.ApplicationServices;
using ZwSoft.ZwCAD.DatabaseServices;
using ZwSoft.ZwCAD.Runtime;

namespace Yuantus.Cad.Bridge.Adapters
{
    /// <summary>
    /// ZWCAD-host shim. Registers exactly two Lisp functions,
    /// <c>yuantus-helper-call</c> and <c>yuantus-helper-upload</c>, with strict
    /// string-argument checks (non-string Lisp values return <c>nil</c>, not
    /// coerced via <c>ToString()</c>). All failure paths print through the
    /// production <see cref="ZwCadCommandLineWriter"/>. Returns a Lisp string on
    /// success or <c>null</c> (which the ZWCAD Lisp layer maps to <c>nil</c>) on
    /// failure. No CAD business behavior, no DWG mutation, no modal UI —
    /// transport only, identical contract to <see cref="AutoCadHostAdapter"/>.
    /// </summary>
    public static class ZwCadHostAdapter
    {
        // Lisp string type code (LispDataType.Text). Hardcoded as the documented
        // resbuf numeric value — universal across the AutoCAD-compatible resbuf
        // model that ZWCAD mirrors — so the source compiles regardless of how the
        // enum happens to be namespaced in a given ZWCAD SDK version.
        private const short LispStringTypeCode = 5005;

        private static readonly ZwCadCommandLineWriter Writer = new ZwCadCommandLineWriter();
        private static readonly BridgeCallService Service = BridgeCallService.CreateProduction(Writer);

        [LispFunction("yuantus-helper-call")]
        public static object YuantusHelperCall(ResultBuffer args)
        {
            string endpoint;
            string jsonRequest;
            if (!TryReadStringArgs(args, out endpoint, out jsonRequest))
            {
                Writer.WriteFailure("HELPER_INPUT_VALIDATION_FAILED", "arity");
                return null;
            }

            var result = Service.Call(endpoint, jsonRequest);
            if (!result.Ok)
            {
                return null;
            }
            return result.Payload;
        }

        [LispFunction("yuantus-helper-upload")]
        public static object YuantusHelperUpload(ResultBuffer args)
        {
            string endpoint;
            string itemId;
            string filePath;
            if (!TryReadThreeStringArgs(args, out endpoint, out itemId, out filePath))
            {
                Writer.WriteFailure("HELPER_INPUT_VALIDATION_FAILED", "arity");
                return null;
            }

            var result = Service.Upload(endpoint, itemId, filePath);
            if (!result.Ok)
            {
                return null;
            }
            return result.Payload;
        }

        private static bool TryReadStringArgs(ResultBuffer args, out string endpoint, out string json)
        {
            endpoint = null;
            json = null;
            if (args == null)
            {
                return false;
            }
            var values = args.AsArray();
            if (values == null || values.Length != 2)
            {
                return false;
            }
            if (!IsLispStringValue(values[0]) || !IsLispStringValue(values[1]))
            {
                return false;
            }
            endpoint = (string)values[0].Value;
            json = (string)values[1].Value;
            return true;
        }

        private static bool TryReadThreeStringArgs(ResultBuffer args, out string endpoint, out string itemId, out string filePath)
        {
            endpoint = null;
            itemId = null;
            filePath = null;
            if (args == null)
            {
                return false;
            }
            var values = args.AsArray();
            if (values == null || values.Length != 3)
            {
                return false;
            }
            if (!IsLispStringValue(values[0]) || !IsLispStringValue(values[1]) || !IsLispStringValue(values[2]))
            {
                return false;
            }
            endpoint = (string)values[0].Value;
            itemId = (string)values[1].Value;
            filePath = (string)values[2].Value;
            return true;
        }

        private static bool IsLispStringValue(TypedValue value)
        {
            if (value == null || value.Value == null)
            {
                return false;
            }
            if (value.TypeCode != LispStringTypeCode)
            {
                return false;
            }
            return value.Value is string;
        }
    }

    /// <summary>
    /// ZWCAD editor-backed implementation of
    /// <see cref="IBridgeCommandLineWriter"/>. Production NETLOAD-only path; not
    /// exercised by the SDK-free CI build. Mirrors <c>AutoCadCommandLineWriter</c>,
    /// swapping the AutoCAD editor for the ZWCAD
    /// <c>Application.DocumentManager.MdiActiveDocument.Editor</c>.
    /// </summary>
    public sealed class ZwCadCommandLineWriter : IBridgeCommandLineWriter
    {
        public void WriteFailure(string code, string reason)
        {
            var line = "[YUANTUS_HELPER_CALL_FAILED] code=" + Sanitize(code) +
                       " reason=" + Sanitize(reason) + "\n";
            var document = Application.DocumentManager.MdiActiveDocument;
            if (document == null || document.Editor == null)
            {
                return;
            }
            document.Editor.WriteMessage(line);
        }

        private static string Sanitize(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return "unknown";
            }
            return System.Text.RegularExpressions.Regex.Replace(
                value, @"[^A-Za-z0-9_\-./]", "_");
        }
    }
}

#endif
