using System;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Bridge
{
    /// <summary>
    /// Production <see cref="IBridgeTransport"/> implementation. Each call
    /// constructs and disposes a fresh S1 <see cref="HelperTransport"/>
    /// bound to the locator-resolved base URI. The Bridge never builds its
    /// own <see cref="System.Net.Http.HttpClient"/> and never re-implements
    /// the helper envelope parser.
    /// </summary>
    public sealed class SharedBridgeTransport : IBridgeTransport
    {
        public async Task<JToken> PostJsonAsync(
            Uri baseUri,
            string endpoint,
            JObject payload,
            CancellationToken cancellationToken)
        {
            using (var transport = new HelperTransport(baseUri))
            {
                return await transport
                    .PostJsonAsync<JToken>(endpoint, payload, cancellationToken)
                    .ConfigureAwait(false);
            }
        }

        public async Task<JToken> PostMultipartAsync(
            Uri baseUri,
            string endpoint,
            string itemId,
            byte[] fileBytes,
            string fileName,
            CancellationToken cancellationToken)
        {
            using (var transport = new HelperTransport(baseUri))
            using (var content = new MultipartFormDataContent())
            {
                var fileContent = new ByteArrayContent(fileBytes ?? new byte[0]);
                fileContent.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");
                content.Add(fileContent, "file", fileName);

                // Defense-in-depth: omit the part for null/empty/whitespace,
                // matching BridgeCallService.UploadAsync normalization even if a
                // future caller bypasses it.
                if (!string.IsNullOrWhiteSpace(itemId))
                {
                    content.Add(new StringContent(itemId, Encoding.UTF8), "item_id");
                }

                return await transport
                    .PostContentAsync<JToken>(endpoint, content, cancellationToken)
                    .ConfigureAwait(false);
            }
        }
    }
}
