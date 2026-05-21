using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Shared.Identity
{
    public static class InstallId
    {
        private static InstallIdFileAccess _fileAccess = InstallIdFileAccess.Default;
        private static Action _createdObserver;

        public static Guid GetOrCreate()
        {
            var path = Paths.InstallIdFile;
            var parent = Path.GetDirectoryName(path);
            try
            {
                if (!string.IsNullOrWhiteSpace(parent))
                {
                    _fileAccess.CreateDirectory(parent);
                }

                try
                {
                    using (var stream = _fileAccess.CreateNew(path))
                    {
                        var installId = Guid.NewGuid();
                        var payload = new JObject
                        {
                            ["schema_version"] = "1.0",
                            ["install_id"] = installId.ToString("D"),
                            ["created_at"] = DateTimeOffset.UtcNow.ToString("o")
                        };
                        var json = payload.ToString(Formatting.None);
                        var bytes = new UTF8Encoding(false).GetBytes(json);
                        stream.Write(bytes, 0, bytes.Length);
                        stream.Flush();
                        if (_createdObserver != null)
                        {
                            _createdObserver();
                        }
                        return installId;
                    }
                }
                catch (IOException)
                {
                    return ReadExistingAfterCreateRace(path);
                }
            }
            catch (HelperException)
            {
                throw;
            }
            catch (Exception ex)
            {
                throw InstallIdUnavailable("unavailable", ex);
            }
        }

        internal static IDisposable UseFileAccessForTesting(InstallIdFileAccess access)
        {
            var previous = _fileAccess;
            _fileAccess = access;
            return new RestoreAction(() => _fileAccess = previous);
        }

        internal static IDisposable ObserveCreatedForTesting(Action observer)
        {
            var previous = _createdObserver;
            _createdObserver = observer;
            return new RestoreAction(() => _createdObserver = previous);
        }

        private static Guid ReadExisting(string path)
        {
            string raw;
            try
            {
                if (!_fileAccess.Exists(path))
                {
                    throw InstallIdUnavailable("missing", null);
                }
                if (_fileAccess.Length(path) == 0)
                {
                    throw InstallIdUnavailable("empty", null);
                }
                raw = _fileAccess.ReadAllText(path);
            }
            catch (HelperException)
            {
                throw;
            }
            catch (Exception ex)
            {
                throw InstallIdUnavailable("unavailable", ex);
            }

            JObject document;
            try
            {
                document = JObject.Parse(raw);
            }
            catch (JsonException ex)
            {
                throw InstallIdUnavailable("malformed_json", ex);
            }

            var token = document["install_id"];
            if (token == null || token.Type == JTokenType.Null)
            {
                throw InstallIdUnavailable("missing_field", null);
            }

            Guid installId;
            if (!Guid.TryParse(token.ToString(), out installId))
            {
                throw InstallIdUnavailable("invalid_guid", null);
            }
            return installId;
        }

        private static Guid ReadExistingAfterCreateRace(string path)
        {
            const int attempts = 20;
            for (var attempt = 1; attempt <= attempts; attempt++)
            {
                try
                {
                    return ReadExisting(path);
                }
                catch (HelperException ex)
                {
                    if (attempt == attempts || !IsTransientCreateRaceRead(ex))
                    {
                        throw;
                    }
                    Thread.Sleep(10);
                }
            }

            throw InstallIdUnavailable("unavailable", null);
        }

        private static bool IsTransientCreateRaceRead(HelperException ex)
        {
            if (ex.Code != ErrorCodes.HelperInstallIdUnavailable)
            {
                return false;
            }
            object reason;
            if (!ex.Details.TryGetValue("reason", out reason))
            {
                return false;
            }

            var reasonText = reason as string;
            return reasonText == "empty" ||
                   (reasonText == "unavailable" && ex.InnerException is IOException);
        }

        private static HelperException InstallIdUnavailable(string reason, Exception inner)
        {
            return new HelperException(
                ErrorCodes.HelperInstallIdUnavailable,
                "Unable to read or create install-id.json.",
                false,
                new Dictionary<string, object> { { "reason", reason } },
                inner);
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

    internal class InstallIdFileAccess
    {
        public static readonly InstallIdFileAccess Default = new InstallIdFileAccess();

        public virtual void CreateDirectory(string path)
        {
            Directory.CreateDirectory(path);
        }

        public virtual Stream CreateNew(string path)
        {
            return new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None);
        }

        public virtual bool Exists(string path)
        {
            return File.Exists(path);
        }

        public virtual long Length(string path)
        {
            return new FileInfo(path).Length;
        }

        public virtual string ReadAllText(string path)
        {
            return File.ReadAllText(path, Encoding.UTF8);
        }
    }
}
