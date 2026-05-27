using System;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

namespace Yuantus.Cad.Bridge
{
    /// <summary>
    /// Narrow seam for posting a JSON payload to a helper endpoint and
    /// receiving the helper <c>data</c> payload back. Production
    /// implementation (<see cref="SharedBridgeTransport"/>) delegates to S1
    /// <c>Yuantus.Cad.Shared.Transport.HelperTransport.PostJsonAsync</c>,
    /// which already injects <c>X-Yuantus-Local-Token</c> and
    /// <c>X-Yuantus-Protocol</c>, parses the helper envelope, and maps
    /// non-2xx / <c>ok=false</c> envelopes to <c>HelperException</c>.
    /// </summary>
    public interface IBridgeTransport
    {
        Task<JToken> PostJsonAsync(
            Uri baseUri,
            string endpoint,
            JObject payload,
            CancellationToken cancellationToken);

        /// <summary>
        /// Posts the canonical Slice B multipart envelope to a helper upload
        /// endpoint and returns the helper <c>data</c> payload. The envelope is
        /// one <c>file</c> part (<c>application/octet-stream</c>, base
        /// <paramref name="fileName"/>) plus a single <c>item_id</c> text part
        /// only when <paramref name="itemId"/> is non-empty. Production
        /// (<see cref="SharedBridgeTransport"/>) reuses Shared
        /// <c>HelperTransport.PostContentAsync&lt;JToken&gt;</c>; the bridge
        /// builds no <c>HttpClient</c> and adds no workflow flags
        /// (<c>create_bom_job</c> / <c>auto_create_part</c> remain helper-side).
        /// </summary>
        Task<JToken> PostMultipartAsync(
            Uri baseUri,
            string endpoint,
            string itemId,
            byte[] fileBytes,
            string fileName,
            CancellationToken cancellationToken);
    }
}
