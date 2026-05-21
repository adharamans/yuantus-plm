using System.Collections.Generic;
using Newtonsoft.Json;

namespace Yuantus.Cad.Shared.Transport
{
    public sealed class ResponseEnvelope<T>
    {
        [JsonProperty("ok")]
        public bool Ok { get; set; }

        [JsonProperty("data")]
        public T Data { get; set; }

        [JsonProperty("error")]
        public HelperError Error { get; set; }
    }

    public sealed class HelperError
    {
        [JsonProperty("code")]
        public string Code { get; set; }

        [JsonProperty("message")]
        public string Message { get; set; }

        [JsonProperty("retryable")]
        public bool Retryable { get; set; }

        [JsonProperty("details")]
        public Dictionary<string, object> Details { get; set; }
    }

    internal sealed class ErrorOnlyEnvelope
    {
        [JsonProperty("error")]
        public HelperError Error { get; set; }
    }
}
