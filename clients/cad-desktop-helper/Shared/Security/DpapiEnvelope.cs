using System;
using System.Security.Cryptography;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Shared.Security
{
    public static class DpapiEnvelope
    {
        private static IDpapiProtector _protector = new WindowsDpapiProtector();

        public static byte[] Protect(byte[] data, byte[] entropy)
        {
            try
            {
                return _protector.Protect(data, entropy);
            }
            catch (Exception ex)
            {
                throw new HelperException(
                    ErrorCodes.HelperDpapiUnavailable,
                    "Windows DPAPI protect failed.",
                    false,
                    null,
                    ex);
            }
        }

        public static byte[] Unprotect(byte[] encrypted, byte[] entropy)
        {
            try
            {
                return _protector.Unprotect(encrypted, entropy);
            }
            catch (Exception ex)
            {
                throw new HelperException(
                    ErrorCodes.HelperDpapiUnavailable,
                    "Windows DPAPI unprotect failed.",
                    false,
                    null,
                    ex);
            }
        }

        internal static IDisposable UseProtectorForTesting(IDpapiProtector protector)
        {
            var previous = _protector;
            _protector = protector;
            return new RestoreAction(() => _protector = previous);
        }

        private sealed class WindowsDpapiProtector : IDpapiProtector
        {
            public byte[] Protect(byte[] data, byte[] entropy)
            {
                return ProtectedData.Protect(data, entropy, DataProtectionScope.CurrentUser);
            }

            public byte[] Unprotect(byte[] encrypted, byte[] entropy)
            {
                return ProtectedData.Unprotect(encrypted, entropy, DataProtectionScope.CurrentUser);
            }
        }

        private sealed class RestoreAction : IDisposable
        {
            private readonly Action _restore;
            private bool _disposed;

            public RestoreAction(Action restore)
            {
                _restore = restore;
            }

            public void Dispose()
            {
                if (_disposed)
                {
                    return;
                }
                _disposed = true;
                _restore();
            }
        }
    }

    internal interface IDpapiProtector
    {
        byte[] Protect(byte[] data, byte[] entropy);
        byte[] Unprotect(byte[] encrypted, byte[] entropy);
    }
}
