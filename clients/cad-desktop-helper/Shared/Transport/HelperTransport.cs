using System;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Yuantus.Cad.Shared.Identity;
using Yuantus.Cad.Shared.Security;

namespace Yuantus.Cad.Shared.Transport
{
    public sealed class HelperTransport : IDisposable
    {
        private readonly Uri _baseUri;
        private readonly HttpClient _httpClient;
        private readonly bool _disposeClient;
        private readonly Func<string> _readLocalToken;

        public HelperTransport(Uri baseUri)
            : this(baseUri, new HttpClient(), true, LocalTokenStore.ReadLocalToken)
        {
        }

        public HelperTransport(Uri baseUri, HttpClient httpClient)
            : this(baseUri, httpClient, false, LocalTokenStore.ReadLocalToken)
        {
        }

        public HelperTransport(Uri baseUri, HttpClient httpClient, Func<string> readLocalToken)
            : this(baseUri, httpClient, false, readLocalToken)
        {
        }

        private HelperTransport(Uri baseUri, HttpClient httpClient, bool disposeClient, Func<string> readLocalToken)
        {
            _baseUri = baseUri;
            _httpClient = httpClient;
            _disposeClient = disposeClient;
            _readLocalToken = readLocalToken;
        }

        public Task<T> PostJsonAsync<T>(string path, object payload, CancellationToken cancellationToken)
        {
            return SendWithRetryAsync<T>(
                HttpMethod.Post,
                path,
                () =>
                {
                    var json = JsonConvert.SerializeObject(payload);
                    return new StringContent(json, Encoding.UTF8, "application/json");
                },
                cancellationToken);
        }

        public async Task<T> PostContentAsync<T>(string path, HttpContent content, CancellationToken cancellationToken)
        {
            var buffered = await BufferedContent.FromAsync(content).ConfigureAwait(false);
            return await SendWithRetryAsync<T>(
                HttpMethod.Post,
                path,
                buffered.CreateContent,
                cancellationToken).ConfigureAwait(false);
        }

        public Task<T> GetAsync<T>(string path, CancellationToken cancellationToken)
        {
            return SendWithRetryAsync<T>(HttpMethod.Get, path, () => null, cancellationToken);
        }

        public void Dispose()
        {
            if (_disposeClient)
            {
                _httpClient.Dispose();
            }
        }

        private async Task<T> SendWithRetryAsync<T>(
            HttpMethod method,
            string path,
            Func<HttpContent> contentFactory,
            CancellationToken cancellationToken)
        {
            var retried = false;
            while (true)
            {
                using (var request = CreateRequest(method, path, contentFactory()))
                using (var response = await _httpClient.SendAsync(request, cancellationToken).ConfigureAwait(false))
                {
                    if (response.StatusCode == HttpStatusCode.Unauthorized && !retried)
                    {
                        var error = await ReadErrorAsync(response).ConfigureAwait(false);
                        if (error != null &&
                            (error.Code == ErrorCodes.AuthLocalTokenInvalid ||
                             error.Code == ErrorCodes.AuthLocalTokenMissing))
                        {
                            retried = true;
                            continue;
                        }
                    }

                    return await ReadEnvelopeAsync<T>(response).ConfigureAwait(false);
                }
            }
        }

        private HttpRequestMessage CreateRequest(HttpMethod method, string path, HttpContent content)
        {
            var request = new HttpRequestMessage(method, new Uri(_baseUri, path));
            if (content != null)
            {
                request.Content = content;
            }
            var token = _readLocalToken();
            if (!string.IsNullOrWhiteSpace(token))
            {
                request.Headers.TryAddWithoutValidation("X-Yuantus-Local-Token", token);
            }
            request.Headers.TryAddWithoutValidation("X-Yuantus-Protocol", Paths.ProtocolVersion);
            return request;
        }

        private static async Task<T> ReadEnvelopeAsync<T>(HttpResponseMessage response)
        {
            if (response.StatusCode == HttpStatusCode.UpgradeRequired)
            {
                throw new HelperException(
                    ErrorCodes.ProtoVersionUnsupported,
                    "Helper protocol version is unsupported.",
                    false);
            }

            if (!response.IsSuccessStatusCode)
            {
                var body = await ReadContentAsStringAsync(response.Content).ConfigureAwait(false);
                var error = TryDeserializeError(body);
                if (error != null)
                {
                    throw ToException(error);
                }
                throw new HelperException(
                    string.Format("HTTP_{0}", (int)response.StatusCode),
                    body,
                    response.StatusCode == HttpStatusCode.RequestTimeout ||
                    (int)response.StatusCode >= 500);
            }

            var envelope = await ReadJsonEnvelopeAsync<T>(response.Content).ConfigureAwait(false);
            if (envelope == null)
            {
                throw new HelperException("HELPER_EMPTY_RESPONSE", "Helper returned an empty response.", true);
            }
            if (!envelope.Ok)
            {
                throw ToException(envelope.Error);
            }
            return envelope.Data;
        }

        private static async Task<HelperError> ReadErrorAsync(HttpResponseMessage response)
        {
            var body = await ReadContentAsStringAsync(response.Content).ConfigureAwait(false);
            return TryDeserializeError(body);
        }

        private static async Task<string> ReadContentAsStringAsync(HttpContent content)
        {
            return content == null
                ? string.Empty
                : await content.ReadAsStringAsync().ConfigureAwait(false);
        }

        private static async Task<ResponseEnvelope<T>> ReadJsonEnvelopeAsync<T>(HttpContent content)
        {
            if (content == null)
            {
                return null;
            }

            using (var stream = await content.ReadAsStreamAsync().ConfigureAwait(false))
            using (var reader = new StreamReader(stream, Encoding.UTF8))
            using (var jsonReader = new JsonTextReader(reader))
            {
                return JsonSerializer.CreateDefault().Deserialize<ResponseEnvelope<T>>(jsonReader);
            }
        }

        private static HelperError TryDeserializeError(string body)
        {
            if (string.IsNullOrWhiteSpace(body))
            {
                return null;
            }
            try
            {
                var direct = JsonConvert.DeserializeObject<ErrorOnlyEnvelope>(body);
                if (direct != null && direct.Error != null)
                {
                    return direct.Error;
                }
                var envelope = JsonConvert.DeserializeObject<ResponseEnvelope<object>>(body);
                return envelope == null ? null : envelope.Error;
            }
            catch (JsonException)
            {
                return null;
            }
        }

        private static HelperException ToException(HelperError error)
        {
            if (error == null)
            {
                return new HelperException("HELPER_UNKNOWN_ERROR", "Helper returned ok=false without an error.", true);
            }
            return new HelperException(
                error.Code,
                error.Message,
                error.Retryable,
                error.Details);
        }

        private sealed class BufferedContent
        {
            private readonly byte[] _bytes;
            private readonly HttpContentHeadersSnapshot _headers;

            private BufferedContent(byte[] bytes, HttpContentHeadersSnapshot headers)
            {
                _bytes = bytes;
                _headers = headers;
            }

            public static async Task<BufferedContent> FromAsync(HttpContent content)
            {
                var bytes = await content.ReadAsByteArrayAsync().ConfigureAwait(false);
                return new BufferedContent(bytes, HttpContentHeadersSnapshot.From(content));
            }

            public HttpContent CreateContent()
            {
                var clone = new ByteArrayContent(_bytes);
                _headers.ApplyTo(clone);
                return clone;
            }
        }

        private sealed class HttpContentHeadersSnapshot
        {
            private readonly System.Collections.Generic.List<System.Collections.Generic.KeyValuePair<string, string[]>> _headers =
                new System.Collections.Generic.List<System.Collections.Generic.KeyValuePair<string, string[]>>();

            public static HttpContentHeadersSnapshot From(HttpContent content)
            {
                var snapshot = new HttpContentHeadersSnapshot();
                foreach (var header in content.Headers)
                {
                    snapshot._headers.Add(new System.Collections.Generic.KeyValuePair<string, string[]>(
                        header.Key,
                        new System.Collections.Generic.List<string>(header.Value).ToArray()));
                }
                return snapshot;
            }

            public void ApplyTo(HttpContent content)
            {
                foreach (var header in _headers)
                {
                    content.Headers.TryAddWithoutValidation(header.Key, header.Value);
                }
            }
        }
    }
}
