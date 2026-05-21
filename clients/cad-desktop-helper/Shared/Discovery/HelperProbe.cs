using System;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Yuantus.Cad.Shared.Discovery
{
    /// <summary>
    /// Performs the local helper liveness probe without injecting authentication headers.
    /// </summary>
    public sealed class HelperProbe : IDisposable
    {
        /// <summary>
        /// Default upper bound for a single local helper health request.
        /// </summary>
        public static readonly TimeSpan DefaultTimeout = TimeSpan.FromMilliseconds(500);

        private readonly HttpClient _httpClient;
        private readonly bool _disposeClient;

        /// <summary>
        /// Initializes a probe with an owned default HTTP client.
        /// </summary>
        public HelperProbe()
            : this(new HttpClient(), true)
        {
        }

        /// <summary>
        /// Initializes a probe with an owned HTTP client around the supplied message handler.
        /// </summary>
        /// <param name="handler">Message handler used by tests or callers that need custom transport.</param>
        public HelperProbe(HttpMessageHandler handler)
            : this(new HttpClient(handler), true)
        {
        }

        /// <summary>
        /// Initializes a probe with a caller-owned HTTP client.
        /// </summary>
        /// <param name="httpClient">HTTP client to use for health requests.</param>
        public HelperProbe(HttpClient httpClient)
            : this(httpClient, false)
        {
        }

        private HelperProbe(HttpClient httpClient, bool disposeClient)
        {
            _httpClient = httpClient;
            _disposeClient = disposeClient;
        }

        /// <summary>
        /// Calls the helper <c>/healthz</c> endpoint and validates both status code and response body.
        /// </summary>
        /// <param name="host">Loopback host to probe.</param>
        /// <param name="port">Helper port from the session discovery file.</param>
        /// <param name="timeout">Maximum duration for this probe attempt.</param>
        /// <param name="cancellationToken">Cancellation token for the probe.</param>
        /// <returns>Health probe outcome.</returns>
        public async Task<HelperProbeResult> HealthAsync(
            string host,
            int port,
            TimeSpan timeout,
            CancellationToken cancellationToken)
        {
            var uri = new Uri(string.Format("http://{0}:{1}/healthz", host, port));
            using (var timeoutSource = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken))
            {
                timeoutSource.CancelAfter(timeout);
                try
                {
                    using (var request = new HttpRequestMessage(HttpMethod.Get, uri))
                    using (var response = await _httpClient.SendAsync(request, timeoutSource.Token).ConfigureAwait(false))
                    {
                        if (response.StatusCode != HttpStatusCode.OK)
                        {
                            return HelperProbeResult.FromStatus(response.StatusCode, false);
                        }

                        var body = response.Content == null
                            ? string.Empty
                            : await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                        return HelperProbeResult.FromStatus(response.StatusCode, IsExpectedHealthBody(body));
                    }
                }
                catch (OperationCanceledException)
                {
                    if (cancellationToken.IsCancellationRequested)
                    {
                        throw;
                    }
                    return HelperProbeResult.Timeout();
                }
                catch (Exception ex)
                {
                    return HelperProbeResult.Failed(ex);
                }
            }
        }

        /// <summary>
        /// Calls the helper <c>/healthz</c> endpoint on 127.0.0.1 with the default timeout.
        /// </summary>
        /// <param name="port">Helper port from the session discovery file.</param>
        /// <param name="cancellationToken">Cancellation token for the probe.</param>
        /// <returns>Health probe outcome.</returns>
        public Task<HelperProbeResult> HealthAsync(int port, CancellationToken cancellationToken)
        {
            return HealthAsync("127.0.0.1", port, DefaultTimeout, cancellationToken);
        }

        private static bool IsExpectedHealthBody(string body)
        {
            if (string.IsNullOrWhiteSpace(body))
            {
                return false;
            }

            try
            {
                var document = JObject.Parse(body);
                var ok = document["ok"];
                if (ok != null && ok.Type == JTokenType.Boolean && ok.Value<bool>())
                {
                    return true;
                }

                var status = document["status"];
                return status != null &&
                       status.Type == JTokenType.String &&
                       string.Equals(status.Value<string>(), "ok", StringComparison.OrdinalIgnoreCase);
            }
            catch (JsonException)
            {
                return false;
            }
        }

        /// <summary>
        /// Releases the owned HTTP client when this probe created it.
        /// </summary>
        public void Dispose()
        {
            if (_disposeClient)
            {
                _httpClient.Dispose();
            }
        }
    }

    public sealed class HelperProbeResult
    {
        private HelperProbeResult(HttpStatusCode? statusCode, bool bodyAccepted, bool timedOut, Exception error)
        {
            StatusCode = statusCode;
            BodyAccepted = bodyAccepted;
            TimedOut = timedOut;
            Error = error;
        }

        public HttpStatusCode? StatusCode { get; private set; }
        public bool BodyAccepted { get; private set; }
        public bool TimedOut { get; private set; }
        public Exception Error { get; private set; }
        public bool IsHealthy
        {
            get { return StatusCode == HttpStatusCode.OK && BodyAccepted && !TimedOut && Error == null; }
        }

        public static HelperProbeResult FromStatus(HttpStatusCode statusCode, bool bodyAccepted)
        {
            return new HelperProbeResult(statusCode, bodyAccepted, false, null);
        }

        public static HelperProbeResult Timeout()
        {
            return new HelperProbeResult(null, false, true, null);
        }

        public static HelperProbeResult Failed(Exception error)
        {
            return new HelperProbeResult(null, false, false, error);
        }
    }
}
