using System;
using System.IO;
using System.Text;
using Yuantus.Cad.Shared.Identity;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Shared.Security
{
    public static class LocalTokenStore
    {
        private static readonly byte[] Entropy =
            Encoding.UTF8.GetBytes("yuantus-cad-helper-local-token-v1");

        public static string ReadLocalToken()
        {
            try
            {
                var path = Paths.LocalTokenFile;
                if (!File.Exists(path))
                {
                    return null;
                }
                var encrypted = File.ReadAllBytes(path);
                var plain = DpapiEnvelope.Unprotect(encrypted, Entropy);
                return Encoding.UTF8.GetString(plain);
            }
            catch (HelperException)
            {
                throw;
            }
            catch (Exception ex)
            {
                throw new HelperException(
                    ErrorCodes.HelperDpapiUnavailable,
                    "Unable to read local helper token.",
                    false,
                    null,
                    ex);
            }
        }

        public static void WriteLocalToken(string hexToken)
        {
            try
            {
                Directory.CreateDirectory(Paths.RootDirectory);
                var plain = Encoding.UTF8.GetBytes(hexToken ?? string.Empty);
                var encrypted = DpapiEnvelope.Protect(plain, Entropy);
                File.WriteAllBytes(Paths.LocalTokenFile, encrypted);
            }
            catch (Exception ex)
            {
                throw new HelperException(
                    ErrorCodes.HelperLocalTokenBootstrapFailed,
                    "Unable to write local helper token.",
                    false,
                    null,
                    ex);
            }
        }
    }
}
