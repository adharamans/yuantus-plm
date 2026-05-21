using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Principal;
using Microsoft.Win32;
using Yuantus.Cad.Shared.Registry;

namespace Yuantus.Cad.Detector
{
    public sealed class DetectorRuntime
    {
        public DetectorRuntime(
            bool isWindows,
            ICadRegistryReader registry,
            IDetectorFileSystem fileSystem,
            Func<HostInfo> hostInfoFactory)
        {
            IsWindows = isWindows;
            Registry = registry;
            FileSystem = fileSystem;
            HostInfoFactory = hostInfoFactory;
        }

        public bool IsWindows { get; private set; }
        public ICadRegistryReader Registry { get; private set; }
        public IDetectorFileSystem FileSystem { get; private set; }
        public Func<HostInfo> HostInfoFactory { get; private set; }

        public static DetectorRuntime Default
        {
            get
            {
                return new DetectorRuntime(
                    RuntimeInformation.IsOSPlatform(OSPlatform.Windows),
                    new SharedCadRegistryReader(),
                    new RealDetectorFileSystem(),
                    CreateHostInfo);
            }
        }

        private static HostInfo CreateHostInfo()
        {
            return new HostInfo
            {
                Os = RuntimeInformation.OSDescription,
                Arch = RuntimeInformation.OSArchitecture.ToString(),
                Username = Environment.UserName,
                IsAdmin = IsCurrentUserAdmin()
            };
        }

        private static bool IsCurrentUserAdmin()
        {
            try
            {
                using (var identity = WindowsIdentity.GetCurrent())
                {
                    var principal = new WindowsPrincipal(identity);
                    return principal.IsInRole(WindowsBuiltInRole.Administrator);
                }
            }
            catch
            {
                return false;
            }
        }
    }

    public interface IDetectorFileSystem
    {
        bool FileExists(string path);
        bool DirectoryExists(string path);
        string ReadAllText(string path);
        void WriteAllText(string path, string contents);
        void CreateDirectory(string path);
        string GetFolderPath(Environment.SpecialFolder folder);
    }

    public sealed class RealDetectorFileSystem : IDetectorFileSystem
    {
        public bool FileExists(string path)
        {
            return File.Exists(path);
        }

        public bool DirectoryExists(string path)
        {
            return Directory.Exists(path);
        }

        public string ReadAllText(string path)
        {
            return File.ReadAllText(path);
        }

        public void WriteAllText(string path, string contents)
        {
            File.WriteAllText(path, contents);
        }

        public void CreateDirectory(string path)
        {
            Directory.CreateDirectory(path);
        }

        public string GetFolderPath(Environment.SpecialFolder folder)
        {
            return Environment.GetFolderPath(folder);
        }
    }

    public interface ICadRegistryReader
    {
        IDetectorRegistryKey OpenHklm(string subKey, RegistryView view);
    }

    public interface IDetectorRegistryKey : IDisposable
    {
        string GetStringValue(string name);
        IEnumerable<string> GetSubKeyNames();
        IDetectorRegistryKey OpenSubKey(string name);
    }

    public sealed class SharedCadRegistryReader : ICadRegistryReader
    {
        public IDetectorRegistryKey OpenHklm(string subKey, RegistryView view)
        {
            var key = HkcuRegistry.OpenHklm(subKey, view);
            return key == null ? null : new SharedRegistryKeyAdapter(key);
        }

        private sealed class SharedRegistryKeyAdapter : IDetectorRegistryKey
        {
            private readonly IRegistryKey _key;

            public SharedRegistryKeyAdapter(IRegistryKey key)
            {
                _key = key;
            }

            public string GetStringValue(string name)
            {
                return _key.GetStringValue(name);
            }

            public IEnumerable<string> GetSubKeyNames()
            {
                return _key.GetSubKeyNames();
            }

            public IDetectorRegistryKey OpenSubKey(string name)
            {
                var child = _key.OpenSubKey(name);
                return child == null ? null : new SharedRegistryKeyAdapter(child);
            }

            public void Dispose()
            {
                _key.Dispose();
            }
        }
    }
}
