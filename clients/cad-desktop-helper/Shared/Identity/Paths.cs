using System;
using System.IO;

namespace Yuantus.Cad.Shared.Identity
{
    public static class Paths
    {
        public const string ProtocolVersion = "1.0";
        public const string HelperExeName = "yuantus-cad-helper.exe";

        private static Func<string> _appDataRootProvider = DefaultAppDataRoot;

        public static string RootDirectory
        {
            get { return Path.Combine(_appDataRootProvider(), "YuantusPLM"); }
        }

        public static string InstallIdFile
        {
            get { return Path.Combine(RootDirectory, "install-id.json"); }
        }

        public static string LocalTokenFile
        {
            get { return Path.Combine(RootDirectory, "local-helper-token.dat"); }
        }

        public static string HelperDirectory
        {
            get { return Path.Combine(RootDirectory, "helper"); }
        }

        public static string HelperExePath
        {
            get { return Path.Combine(HelperDirectory, HelperExeName); }
        }

        public static string HelperSessionFilePath
        {
            get
            {
                return Path.Combine(
                    RootDirectory,
                    string.Format("helper-session-{0}.json", SessionContext.CurrentSessionId));
            }
        }

        internal static IDisposable UseAppDataRootForTesting(string root)
        {
            var previous = _appDataRootProvider;
            _appDataRootProvider = () => root;
            return new RestoreAction(() => _appDataRootProvider = previous);
        }

        private static string DefaultAppDataRoot()
        {
            var appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            if (string.IsNullOrWhiteSpace(appData))
            {
                appData = Environment.GetEnvironmentVariable("APPDATA");
            }
            if (string.IsNullOrWhiteSpace(appData))
            {
                appData = Path.GetTempPath();
            }
            return appData;
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
}
