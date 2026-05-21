using System;
using System.Collections.Generic;

namespace Yuantus.Cad.Shared.Transport
{
    public sealed class HelperException : Exception
    {
        public HelperException(string code, string message, bool retryable)
            : this(code, message, retryable, null, null)
        {
        }

        public HelperException(
            string code,
            string message,
            bool retryable,
            IDictionary<string, object> details = null,
            Exception innerException = null)
            : base(message, innerException)
        {
            Code = code;
            Retryable = retryable;
            Details = details == null
                ? new Dictionary<string, object>()
                : new Dictionary<string, object>(details);
        }

        public string Code { get; private set; }
        public bool Retryable { get; private set; }
        public IReadOnlyDictionary<string, object> Details { get; private set; }
    }
}
