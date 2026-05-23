// CAD-host adapter for the S9 NETLOAD Lisp bridge.
//
// This file contains the AutoCAD-specific NETLOAD entry point and the
// `(yuantus-helper-call ...)` Lisp registration shim. Real AutoCAD managed
// assemblies (acmgd.dll / accoremgd.dll) are not available on the GitHub
// Windows runner, so the SDK-free CI build excludes the host-bound code via
// the `AUTOCAD_HOST` compilation symbol. The bridge's SDK-free core
// (`BridgeCallService`, `EndpointValidator`, the transport / locator seams,
// and the command-line writer interface) is fully unit-tested in
// `Bridge.Tests` without AutoCAD references.
//
// Operational NETLOAD evidence (DLL loads inside acad.exe / ZWCAD.exe /
// gscad.exe, real CAD command-line writer prints sanitized error lines, etc.)
// is deferred to native-CAD operational signoff per taskbook §3.K.

#if AUTOCAD_HOST

using System;
using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.Runtime;

namespace Yuantus.Cad.Bridge.Adapters
{
    /// <summary>
    /// AutoCAD-host shim. Registers exactly one Lisp function,
    /// <c>yuantus-helper-call</c>, with a strict two-string-argument check
    /// (non-string Lisp values return <c>nil</c> per §3.B, not coerced via
    /// <c>ToString()</c>). All failure paths — arity, type, endpoint
    /// validation, JSON parse, helper locator, helper transport — print
    /// through the production <see cref="AutoCadCommandLineWriter"/>, not
    /// the SDK-free console writer. Returns a Lisp string on success or
    /// <c>null</c> (which AutoCAD's Lisp layer maps to <c>nil</c>) on
    /// failure. No CAD business behavior, no DWG mutation, no modal UI.
    /// </summary>
    public static class AutoCadHostAdapter
    {
        // AutoCAD Lisp string type code (Autodesk.AutoCAD.Runtime.LispDataType.Text).
        // Hardcoded as the documented numeric value so the source compiles
        // even on AutoCAD SDK versions where the enum name is namespaced
        // differently.
        private const short LispStringTypeCode = 5005;

        private static readonly AutoCadCommandLineWriter Writer = new AutoCadCommandLineWriter();
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
    /// AutoCAD editor-backed implementation of
    /// <see cref="IBridgeCommandLineWriter"/>. Production NETLOAD-only path;
    /// not exercised by the SDK-free CI build.
    /// </summary>
    public sealed class AutoCadCommandLineWriter : IBridgeCommandLineWriter
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
