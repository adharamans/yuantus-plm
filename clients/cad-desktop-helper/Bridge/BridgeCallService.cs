using System;
using System.IO;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Bridge
{
    /// <summary>
    /// SDK-free core of the S9 NETLOAD Lisp transport bridge. The CAD-host
    /// adapter (see <c>Adapters/AutoCadHostAdapter.cs</c>) is a thin shim
    /// that performs Lisp arity / type checking, delegates to <see cref="CallAsync"/>,
    /// and maps the returned <see cref="BridgeResult"/> to a Lisp string or
    /// <c>nil</c>.
    /// </summary>
    /// <remarks>
    /// <para>
    /// The service intentionally takes <see cref="IBridgeLocator"/> and
    /// <see cref="IBridgeTransport"/> dependencies rather than touching the
    /// network directly. The production wiring in
    /// <see cref="CreateProduction"/> reaches S1
    /// <c>Yuantus.Cad.Shared.Discovery.HelperLocator</c> and
    /// <c>Yuantus.Cad.Shared.Transport.HelperTransport</c>; unit tests inject
    /// fakes to verify the strict call-shape requirement in taskbook §5
    /// test 6 without re-exercising the OS / network seam that S1 Shared.Tests
    /// already covers.
    /// </para>
    /// </remarks>
    public sealed class BridgeCallService
    {
        private readonly IBridgeLocator _locator;
        private readonly IBridgeTransport _transport;
        private readonly IBridgeCommandLineWriter _writer;
        private readonly IBridgeFileSource _fileSource;

        // Slice B: yuantus-helper-upload may target only these two helper
        // routes. The primitive is NOT a generic multipart tunnel.
        private static readonly string[] UploadEndpointAllowlist =
        {
            "/document/checkin",
            "/document/bom-import",
        };

        public BridgeCallService(
            IBridgeLocator locator,
            IBridgeTransport transport,
            IBridgeCommandLineWriter writer)
            : this(locator, transport, writer, new BridgeFileSource())
        {
        }

        public BridgeCallService(
            IBridgeLocator locator,
            IBridgeTransport transport,
            IBridgeCommandLineWriter writer,
            IBridgeFileSource fileSource)
        {
            if (locator == null) throw new ArgumentNullException("locator");
            if (transport == null) throw new ArgumentNullException("transport");
            if (writer == null) throw new ArgumentNullException("writer");
            if (fileSource == null) throw new ArgumentNullException("fileSource");
            _locator = locator;
            _transport = transport;
            _writer = writer;
            _fileSource = fileSource;
        }

        /// <summary>
        /// Production wiring: locator delegates to S1 <c>HelperLocator</c>,
        /// transport delegates to S1 <c>HelperTransport</c>. The
        /// <paramref name="writer"/> receives the §3.G sanitized failure
        /// line for every failure path (endpoint validation, JSON parse,
        /// helper locator, helper transport, sync wrapper). The CAD-host
        /// adapter passes <c>AutoCadCommandLineWriter</c>; a null argument
        /// falls back to <see cref="ConsoleBridgeCommandLineWriter"/> for
        /// service / debug-mode invocations.
        /// </summary>
        public static BridgeCallService CreateProduction(IBridgeCommandLineWriter writer = null)
        {
            return new BridgeCallService(
                new SharedBridgeLocator(),
                new SharedBridgeTransport(),
                writer ?? new ConsoleBridgeCommandLineWriter());
        }

        /// <summary>
        /// Synchronous entry point for the CAD-host Lisp shim. Wraps
        /// <see cref="CallAsync"/> with <c>.GetAwaiter().GetResult()</c> per
        /// taskbook §3.F so any <see cref="HelperException"/> surfaces with
        /// its real <c>Code</c> rather than wrapped in
        /// <c>AggregateException</c>.
        /// </summary>
        public BridgeResult Call(string endpoint, string jsonRequest)
        {
            return CallAsync(endpoint, jsonRequest, CancellationToken.None).GetAwaiter().GetResult();
        }

        /// <summary>
        /// Async core of the bridge call. Validates the endpoint and JSON
        /// request, calls the locator to ensure helper is running, then
        /// forwards the parsed JSON object through the transport. Maps
        /// <see cref="HelperException"/> and other transport failures to
        /// <see cref="BridgeResult.Failure"/> while emitting one sanitized
        /// command-line line. Successful responses serialize the helper
        /// <c>data</c> payload as a JSON string; helper successes with
        /// missing or JSON-null <c>data</c> return the literal string
        /// <c>"null"</c> per §3.E.
        /// </summary>
        public async Task<BridgeResult> CallAsync(
            string endpoint,
            string jsonRequest,
            CancellationToken cancellationToken)
        {
            string rejectionReason;
            if (!EndpointValidator.TryValidate(endpoint, out rejectionReason))
            {
                return Fail(ErrorCodes.HelperInputValidationFailed, rejectionReason);
            }

            JObject payload;
            try
            {
                if (string.IsNullOrEmpty(jsonRequest))
                {
                    return Fail(ErrorCodes.HelperInputValidationFailed, "json_missing");
                }
                var token = JToken.Parse(jsonRequest);
                payload = token as JObject;
                if (payload == null)
                {
                    return Fail(ErrorCodes.HelperInputValidationFailed, "json_not_object");
                }
            }
            catch (JsonException)
            {
                return Fail(ErrorCodes.HelperInputValidationFailed, "json_invalid");
            }

            Uri baseUri;
            try
            {
                baseUri = await _locator
                    .EnsureHelperRunningAsync(cancellationToken)
                    .ConfigureAwait(false);
            }
            catch (HelperException ex)
            {
                return Fail(ex.Code, ShortReason(ex));
            }
            catch (OperationCanceledException)
            {
                return Fail(ErrorCodes.HelperUnhealthy, "cancelled");
            }
            catch (Exception ex)
            {
                return Fail(ErrorCodes.HelperUnhealthy, ShortReason(ex));
            }

            JToken data;
            try
            {
                data = await _transport
                    .PostJsonAsync(baseUri, endpoint, payload, cancellationToken)
                    .ConfigureAwait(false);
            }
            catch (HelperException ex)
            {
                return Fail(ex.Code, ShortReason(ex));
            }
            catch (OperationCanceledException)
            {
                return Fail(ErrorCodes.HelperUnhealthy, "cancelled");
            }
            catch (Exception ex)
            {
                return Fail(ErrorCodes.PlmValidationFailed, ShortReason(ex));
            }

            return BridgeResult.Success(SerializeDataPayload(data));
        }

        /// <summary>
        /// Synchronous entry point for the <c>yuantus-helper-upload</c> Lisp
        /// shim. Wraps <see cref="UploadAsync"/> with
        /// <c>.GetAwaiter().GetResult()</c>, like <see cref="Call"/>.
        /// </summary>
        public BridgeResult Upload(string endpoint, string itemId, string filePath)
        {
            return UploadAsync(endpoint, itemId, filePath, CancellationToken.None).GetAwaiter().GetResult();
        }

        /// <summary>
        /// Async core of the Slice B multipart upload. Validation is strictly
        /// ordered so a rejected call never reads a file or touches the
        /// network: structural endpoint validation -> exact upload-endpoint
        /// allowlist -> route-specific <c>item_id</c> rule -> locator ->
        /// file-source read -> multipart transport. <c>/document/checkin</c>
        /// requires a non-blank <c>itemId</c> (failed here, before locator /
        /// file read / transport); <c>/document/bom-import</c> allows a blank
        /// <c>itemId</c> and omits the part so the helper auto-creates the
        /// root. The locator runs before the file read so a stopped helper
        /// fails before a large CAD file is loaded into memory. Failures emit
        /// one sanitized writer line with a fixed reason token (never the
        /// path) and map to <c>nil</c> at the Lisp surface.
        /// </summary>
        public async Task<BridgeResult> UploadAsync(
            string endpoint,
            string itemId,
            string filePath,
            CancellationToken cancellationToken)
        {
            string rejectionReason;
            if (!EndpointValidator.TryValidate(endpoint, out rejectionReason))
            {
                return Fail(ErrorCodes.HelperInputValidationFailed, rejectionReason);
            }
            if (Array.IndexOf(UploadEndpointAllowlist, endpoint) < 0)
            {
                return Fail(ErrorCodes.HelperInputValidationFailed, "endpoint_not_upload");
            }

            var isCheckin = string.Equals(endpoint, "/document/checkin", StringComparison.Ordinal);
            if (isCheckin && string.IsNullOrWhiteSpace(itemId))
            {
                return Fail(ErrorCodes.HelperInputValidationFailed, "item_id_required");
            }

            Uri baseUri;
            try
            {
                baseUri = await _locator
                    .EnsureHelperRunningAsync(cancellationToken)
                    .ConfigureAwait(false);
            }
            catch (HelperException ex)
            {
                return Fail(ex.Code, ShortReason(ex));
            }
            catch (OperationCanceledException)
            {
                return Fail(ErrorCodes.HelperUnhealthy, "cancelled");
            }
            catch (Exception ex)
            {
                return Fail(ErrorCodes.HelperUnhealthy, ShortReason(ex));
            }

            byte[] fileBytes;
            string fileReason;
            if (!_fileSource.TryReadAllBytes(filePath, out fileBytes, out fileReason))
            {
                // fileReason is a fixed token from IBridgeFileSource; the path
                // is never echoed into the writer line.
                return Fail(ErrorCodes.HelperInputValidationFailed, fileReason);
            }

            var fileName = SanitizeFileName(filePath);
            var effectiveItemId = string.IsNullOrWhiteSpace(itemId) ? null : itemId;

            JToken data;
            try
            {
                data = await _transport
                    .PostMultipartAsync(baseUri, endpoint, effectiveItemId, fileBytes, fileName, cancellationToken)
                    .ConfigureAwait(false);
            }
            catch (HelperException ex)
            {
                return Fail(ex.Code, ShortReason(ex));
            }
            catch (OperationCanceledException)
            {
                return Fail(ErrorCodes.HelperUnhealthy, "cancelled");
            }
            catch (Exception ex)
            {
                return Fail(ErrorCodes.PlmValidationFailed, ShortReason(ex));
            }

            return BridgeResult.Success(SerializeDataPayload(data));
        }

        /// <summary>
        /// Derives the multipart <c>filename</c> from a local path: take the
        /// base name, keep only ASCII <c>[A-Za-z0-9._-]</c> (every other
        /// character, including directory separators, becomes <c>_</c>), and
        /// fall back to <c>upload.bin</c> when the result is empty. RFC 5987
        /// <c>filename*</c> encoding is deferred.
        /// </summary>
        internal static string SanitizeFileName(string filePath)
        {
            var baseName = string.IsNullOrEmpty(filePath) ? string.Empty : Path.GetFileName(filePath);
            if (string.IsNullOrEmpty(baseName))
            {
                return "upload.bin";
            }
            var builder = new StringBuilder(baseName.Length);
            foreach (var ch in baseName)
            {
                var keep =
                    (ch >= 'A' && ch <= 'Z') ||
                    (ch >= 'a' && ch <= 'z') ||
                    (ch >= '0' && ch <= '9') ||
                    ch == '.' || ch == '_' || ch == '-';
                builder.Append(keep ? ch : '_');
            }
            var sanitized = builder.ToString();
            return sanitized.Length == 0 ? "upload.bin" : sanitized;
        }

        /// <summary>
        /// Serializes the helper <c>data</c> payload to a JSON string. Per
        /// §3.E, missing or JSON-null data returns the literal <c>"null"</c>
        /// string so callers can distinguish a successful JSON-null payload
        /// from a transport failure (which returns <c>nil</c> at the Lisp
        /// surface).
        /// </summary>
        private static string SerializeDataPayload(JToken data)
        {
            if (data == null || data.Type == JTokenType.Null)
            {
                return "null";
            }
            return JsonConvert.SerializeObject(data);
        }

        private BridgeResult Fail(string code, string reason)
        {
            _writer.WriteFailure(code, reason);
            return BridgeResult.Failure(code, reason);
        }

        private static string ShortReason(Exception ex)
        {
            return ex == null ? "unknown" : ex.GetType().Name;
        }
    }
}
