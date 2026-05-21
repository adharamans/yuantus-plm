using System;
using System.Collections.Generic;
using Microsoft.Win32;

namespace Yuantus.Cad.Shared.Registry
{
    public static class HkcuRegistry
    {
        private static IRegistryBackend _backend = new WindowsRegistryBackend();

        public static IRegistryKey OpenHkcu(string subKey, RegistryView view = RegistryView.Registry64)
        {
            return _backend.OpenSubKey(RegistryHive.CurrentUser, subKey, view);
        }

        public static IRegistryKey OpenHklm(string subKey, RegistryView view = RegistryView.Registry64)
        {
            return _backend.OpenSubKey(RegistryHive.LocalMachine, subKey, view);
        }

        internal static IDisposable UseBackendForTesting(IRegistryBackend backend)
        {
            var previous = _backend;
            _backend = backend;
            return new RestoreAction(() => _backend = previous);
        }

        private sealed class WindowsRegistryBackend : IRegistryBackend
        {
            public IRegistryKey OpenSubKey(RegistryHive hive, string subKey, RegistryView view)
            {
                var baseKey = Microsoft.Win32.RegistryKey.OpenBaseKey(hive, view);
                var key = baseKey.OpenSubKey(subKey, false);
                if (key == null)
                {
                    baseKey.Dispose();
                    return null;
                }
                return new RegistryKeyWrapper(baseKey, key);
            }
        }

        private sealed class RegistryKeyWrapper : IRegistryKey
        {
            private readonly Microsoft.Win32.RegistryKey _baseKey;
            private readonly Microsoft.Win32.RegistryKey _key;

            public RegistryKeyWrapper(Microsoft.Win32.RegistryKey baseKey, Microsoft.Win32.RegistryKey key)
            {
                _baseKey = baseKey;
                _key = key;
            }

            public string GetStringValue(string name)
            {
                return _key.GetValue(name) as string;
            }

            public IEnumerable<string> GetSubKeyNames()
            {
                return _key.GetSubKeyNames();
            }

            public IRegistryKey OpenSubKey(string name)
            {
                var child = _key.OpenSubKey(name, false);
                return child == null ? null : new RegistryKeyWrapper(null, child);
            }

            public void Dispose()
            {
                _key.Dispose();
                if (_baseKey != null)
                {
                    _baseKey.Dispose();
                }
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

    public interface IRegistryKey : IDisposable
    {
        string GetStringValue(string name);
        IEnumerable<string> GetSubKeyNames();
        IRegistryKey OpenSubKey(string name);
    }

    internal interface IRegistryBackend
    {
        IRegistryKey OpenSubKey(RegistryHive hive, string subKey, RegistryView view);
    }
}
