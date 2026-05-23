using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Hosting;
using Microsoft.Data.Sqlite;
using Microsoft.Extensions.Hosting;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Yuantus.Cad.Shared.Discovery;
using Yuantus.Cad.Shared.Identity;
using Yuantus.Cad.Shared.Security;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Helper
{
    public sealed class HelperRuntime
    {
        public static readonly HelperRuntime Default = new HelperRuntime(
            new DefaultHelperPaths(),
            new SharedInstallIdProvider(),
            new SharedLocalTokenStore(),
            new CryptographicRandomBytes(),
            new PortAllocator(new TcpPortBinder()),
            null,
            new SystemNamedMutexFactory(),
            new SharedHealthProbe(),
            new DefaultProcessInspector(),
            new SystemDelay(),
            new SystemClock(),
            new ConsoleErrorWriter(),
            new KestrelHelperHostRunner());

        public HelperRuntime(
            IHelperPaths paths,
            IInstallIdProvider installIds,
            ILocalTokenStore tokenStore,
            IRandomBytes randomBytes,
            IPortAllocator portAllocator,
            ISessionFileStore sessionFiles,
            INamedMutexFactory mutexFactory,
            IHealthProbe healthProbe,
            IProcessInspector processInspector,
            IDelay delay,
            IClock clock,
            IErrorWriter errorWriter,
            IHelperHostRunner hostRunner)
        {
            Paths = paths;
            InstallIds = installIds;
            TokenStore = tokenStore;
            RandomBytes = randomBytes;
            PortAllocator = portAllocator;
            SessionFiles = sessionFiles ?? new JsonSessionFileStore(paths);
            MutexFactory = mutexFactory;
            HealthProbe = healthProbe;
            ProcessInspector = processInspector;
            Delay = delay;
            Clock = clock;
            ErrorWriter = errorWriter;
            HostRunner = hostRunner;
        }

        public IHelperPaths Paths { get; private set; }
        public IInstallIdProvider InstallIds { get; private set; }
        public ILocalTokenStore TokenStore { get; private set; }
        public IRandomBytes RandomBytes { get; private set; }
        public IPortAllocator PortAllocator { get; private set; }
        public ISessionFileStore SessionFiles { get; private set; }
        public INamedMutexFactory MutexFactory { get; private set; }
        public IHealthProbe HealthProbe { get; private set; }
        public IProcessInspector ProcessInspector { get; private set; }
        public IDelay Delay { get; private set; }
        public IClock Clock { get; private set; }
        public IErrorWriter ErrorWriter { get; private set; }
        public IHelperHostRunner HostRunner { get; private set; }
    }

    public static class HelperCommand
    {
        public static async Task<int> RunAsync(string[] args, HelperRuntime runtime, CancellationToken cancellationToken)
        {
            var ownsLifecycle = false;
            if (args != null && args.Length > 0)
            {
                runtime.ErrorWriter.Write(ErrorCodes.HelperInputValidationFailed, "Unsupported helper startup arguments.");
                return 1;
            }

            INamedMutexLease lease = null;
            try
            {
                var installId = runtime.InstallIds.GetOrCreate();
                var coordinator = new SingleInstanceCoordinator(
                    runtime.MutexFactory,
                    runtime.SessionFiles,
                    runtime.HealthProbe,
                    runtime.ProcessInspector,
                    runtime.Delay);
                var decision = await coordinator.ResolveAsync(
                    SingleInstanceCoordinator.MutexName(installId),
                    cancellationToken).ConfigureAwait(false);

                if (decision.Kind == SingleInstanceDecisionKind.ExistingHealthy)
                {
                    return 0;
                }

                if (decision.Kind == SingleInstanceDecisionKind.Failed)
                {
                    runtime.ErrorWriter.Write(decision.ErrorCode, decision.Message);
                    return 1;
                }

                lease = decision.Lease;
                ownsLifecycle = true;

                var bootstrapper = new LocalTokenBootstrapper(runtime.TokenStore, runtime.RandomBytes);
                var localToken = bootstrapper.EnsureToken();

                var port = runtime.PortAllocator.Allocate().Port;
                var session = HelperSessionDocument.Create(runtime.Paths, port, runtime.Clock.UtcNow);
                runtime.SessionFiles.Publish(session);

                var idleTimeout = HelperConfig.LoadIdleTimeout(runtime.Paths.ConfigFilePath);
                var securityOptions = HelperSecurityOptions.Load(runtime.Paths.ConfigFilePath);
                try
                {
                    await runtime.HostRunner
                        .RunAsync(port, session, idleTimeout, localToken, securityOptions, runtime.Clock, runtime.Delay, cancellationToken)
                        .ConfigureAwait(false);
                    return 0;
                }
                finally
                {
                    runtime.SessionFiles.DeleteCurrent();
                }
            }
            catch (HelperException ex)
            {
                if (ownsLifecycle)
                {
                    runtime.SessionFiles.DeleteCurrent();
                }
                runtime.ErrorWriter.Write(ex.Code, ex.Message);
                return 1;
            }
            finally
            {
                if (lease != null)
                {
                    lease.Dispose();
                }
            }
        }
    }

    public interface IHelperPaths
    {
        string RootDirectory { get; }
        string HelperSessionFilePath { get; }
        string HelperExePath { get; }
        string ConfigFilePath { get; }
    }

    public sealed class DefaultHelperPaths : IHelperPaths
    {
        public string RootDirectory
        {
            get { return Paths.RootDirectory; }
        }

        public string HelperSessionFilePath
        {
            get { return Paths.HelperSessionFilePath; }
        }

        public string HelperExePath
        {
            get { return Paths.HelperExePath; }
        }

        public string ConfigFilePath
        {
            get { return Path.Combine(Paths.RootDirectory, "config.json"); }
        }
    }

    public interface IInstallIdProvider
    {
        Guid GetOrCreate();
    }

    public sealed class SharedInstallIdProvider : IInstallIdProvider
    {
        public Guid GetOrCreate()
        {
            return InstallId.GetOrCreate();
        }
    }

    public interface ILocalTokenStore
    {
        string Read();
        void Write(string hexToken);
    }

    public sealed class SharedLocalTokenStore : ILocalTokenStore
    {
        public string Read()
        {
            return LocalTokenStore.ReadLocalToken();
        }

        public void Write(string hexToken)
        {
            LocalTokenStore.WriteLocalToken(hexToken);
        }
    }

    public interface IRandomBytes
    {
        byte[] GetBytes(int count);
    }

    public sealed class CryptographicRandomBytes : IRandomBytes
    {
        public byte[] GetBytes(int count)
        {
            var bytes = new byte[count];
            using (var generator = RandomNumberGenerator.Create())
            {
                generator.GetBytes(bytes);
            }
            return bytes;
        }
    }

    public sealed class LocalTokenBootstrapper
    {
        private readonly ILocalTokenStore _store;
        private readonly IRandomBytes _randomBytes;

        public LocalTokenBootstrapper(ILocalTokenStore store, IRandomBytes randomBytes)
        {
            _store = store;
            _randomBytes = randomBytes;
        }

        public string EnsureToken()
        {
            var existing = _store.Read();
            if (!string.IsNullOrWhiteSpace(existing))
            {
                return existing;
            }

            var token = ToLowerHex(_randomBytes.GetBytes(32));
            _store.Write(token);
            return token;
        }

        private static string ToLowerHex(byte[] bytes)
        {
            var builder = new StringBuilder(bytes.Length * 2);
            for (var i = 0; i < bytes.Length; i++)
            {
                builder.Append(bytes[i].ToString("x2"));
            }
            return builder.ToString();
        }
    }

    public interface IPortAllocator
    {
        PortBinding Allocate();
    }

    public sealed class PortAllocator : IPortAllocator
    {
        public const string LoopbackHost = "127.0.0.1";
        public const int FirstPort = 7959;
        public const int LastPort = 7999;

        private readonly IPortBinder _binder;

        public PortAllocator(IPortBinder binder)
        {
            _binder = binder;
        }

        public PortBinding Allocate()
        {
            for (var port = FirstPort; port <= LastPort; port++)
            {
                if (_binder.CanBind(LoopbackHost, port))
                {
                    return new PortBinding(LoopbackHost, port);
                }
            }

            throw new HelperException(
                ErrorCodes.HelperPortBusy,
                "No available yuantus-cad-helper loopback port in 7959..7999.",
                true);
        }
    }

    public sealed class PortBinding
    {
        public PortBinding(string host, int port)
        {
            Host = host;
            Port = port;
        }

        public string Host { get; private set; }
        public int Port { get; private set; }
    }

    public interface IPortBinder
    {
        bool CanBind(string host, int port);
    }

    public sealed class TcpPortBinder : IPortBinder
    {
        public bool CanBind(string host, int port)
        {
            TcpListener listener = null;
            try
            {
                listener = new TcpListener(IPAddress.Parse(host), port);
                listener.Start();
                return true;
            }
            catch (SocketException)
            {
                return false;
            }
            finally
            {
                if (listener != null)
                {
                    listener.Stop();
                }
            }
        }
    }

    public sealed class HelperSessionDocument
    {
        [JsonProperty("schema_version")]
        public string SchemaVersion { get; set; }

        [JsonProperty("session_id")]
        public int SessionId { get; set; }

        [JsonProperty("port")]
        public int Port { get; set; }

        [JsonProperty("pid")]
        public int Pid { get; set; }

        [JsonProperty("image_path")]
        public string ImagePath { get; set; }

        [JsonProperty("started_at")]
        public DateTimeOffset StartedAt { get; set; }

        [JsonProperty("protocol_version")]
        public string ProtocolVersion { get; set; }

        [JsonProperty("helper_version")]
        public string HelperVersion { get; set; }

        [JsonProperty("endpoints_base")]
        public string EndpointsBase { get; set; }

        public static HelperSessionDocument Create(IHelperPaths paths, int port, DateTimeOffset startedAt)
        {
            return new HelperSessionDocument
            {
                SchemaVersion = "1.0",
                SessionId = SessionContext.CurrentSessionId,
                Port = port,
                Pid = Process.GetCurrentProcess().Id,
                ImagePath = paths.HelperExePath,
                StartedAt = startedAt,
                ProtocolVersion = Paths.ProtocolVersion,
                HelperVersion = "0.1.0",
                EndpointsBase = "http://127.0.0.1:" + port
            };
        }
    }

    public interface ISessionFileStore
    {
        HelperSessionDocument Read();
        void Publish(HelperSessionDocument session);
        void DeleteCurrent();
    }

    public sealed class JsonSessionFileStore : ISessionFileStore
    {
        private readonly IHelperPaths _paths;

        public JsonSessionFileStore(IHelperPaths paths)
        {
            _paths = paths;
        }

        public HelperSessionDocument Read()
        {
            try
            {
                if (!File.Exists(_paths.HelperSessionFilePath))
                {
                    return null;
                }

                using (var stream = new FileStream(
                    _paths.HelperSessionFilePath,
                    FileMode.Open,
                    FileAccess.Read,
                    FileShare.ReadWrite | FileShare.Delete))
                using (var reader = new StreamReader(stream))
                {
                    var json = reader.ReadToEnd();
                    if (string.IsNullOrWhiteSpace(json))
                    {
                        return null;
                    }
                    return JsonConvert.DeserializeObject<HelperSessionDocument>(json);
                }
            }
            catch (IOException)
            {
                return null;
            }
            catch (UnauthorizedAccessException)
            {
                return null;
            }
            catch (JsonException)
            {
                return null;
            }
        }

        public void Publish(HelperSessionDocument session)
        {
            Directory.CreateDirectory(_paths.RootDirectory);
            var json = JsonConvert.SerializeObject(session, Formatting.Indented);
            var temp = _paths.HelperSessionFilePath + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                File.WriteAllText(temp, json, Encoding.UTF8);
                if (File.Exists(_paths.HelperSessionFilePath))
                {
                    File.Replace(temp, _paths.HelperSessionFilePath, null);
                }
                else
                {
                    File.Move(temp, _paths.HelperSessionFilePath);
                }
            }
            finally
            {
                try
                {
                    if (File.Exists(temp))
                    {
                        File.Delete(temp);
                    }
                }
                catch (IOException)
                {
                }
                catch (UnauthorizedAccessException)
                {
                }
            }
        }

        public void DeleteCurrent()
        {
            try
            {
                if (File.Exists(_paths.HelperSessionFilePath))
                {
                    File.Delete(_paths.HelperSessionFilePath);
                }
            }
            catch (IOException)
            {
            }
            catch (UnauthorizedAccessException)
            {
            }
        }
    }

    public enum SingleInstanceDecisionKind
    {
        Start,
        ExistingHealthy,
        Failed
    }

    public sealed class SingleInstanceDecision
    {
        private SingleInstanceDecision(
            SingleInstanceDecisionKind kind,
            INamedMutexLease lease,
            string errorCode,
            string message)
        {
            Kind = kind;
            Lease = lease;
            ErrorCode = errorCode;
            Message = message;
        }

        public SingleInstanceDecisionKind Kind { get; private set; }
        public INamedMutexLease Lease { get; private set; }
        public string ErrorCode { get; private set; }
        public string Message { get; private set; }

        public static SingleInstanceDecision Start(INamedMutexLease lease)
        {
            return new SingleInstanceDecision(SingleInstanceDecisionKind.Start, lease, null, null);
        }

        public static SingleInstanceDecision ExistingHealthy()
        {
            return new SingleInstanceDecision(SingleInstanceDecisionKind.ExistingHealthy, null, null, null);
        }

        public static SingleInstanceDecision Failed(string errorCode, string message)
        {
            return new SingleInstanceDecision(SingleInstanceDecisionKind.Failed, null, errorCode, message);
        }
    }

    public sealed class SingleInstanceCoordinator
    {
        public const int MaxAttempts = 3;
        public static readonly TimeSpan MissingSessionRetryDelay = TimeSpan.FromMilliseconds(500);

        private readonly INamedMutexFactory _mutexFactory;
        private readonly ISessionFileStore _sessions;
        private readonly IHealthProbe _healthProbe;
        private readonly IProcessInspector _processInspector;
        private readonly IDelay _delay;

        public SingleInstanceCoordinator(
            INamedMutexFactory mutexFactory,
            ISessionFileStore sessions,
            IHealthProbe healthProbe,
            IProcessInspector processInspector,
            IDelay delay)
        {
            _mutexFactory = mutexFactory;
            _sessions = sessions;
            _healthProbe = healthProbe;
            _processInspector = processInspector;
            _delay = delay;
        }

        public static string MutexName(Guid installId)
        {
            return "Local\\YuantusCadHelper-" + installId.ToString("D");
        }

        public async Task<SingleInstanceDecision> ResolveAsync(string mutexName, CancellationToken cancellationToken)
        {
            for (var attempt = 1; attempt <= MaxAttempts; attempt++)
            {
                var lease = _mutexFactory.TryAcquire(mutexName);
                if (lease.Acquired)
                {
                    return SingleInstanceDecision.Start(lease);
                }
                lease.Dispose();

                var session = _sessions.Read();
                if (session == null)
                {
                    if (attempt < MaxAttempts)
                    {
                        await _delay.DelayAsync(MissingSessionRetryDelay, cancellationToken).ConfigureAwait(false);
                        continue;
                    }
                    return SingleInstanceDecision.Failed(
                        ErrorCodes.HelperSingletonLost,
                        "Helper mutex is held but no current session file is available.");
                }

                if (await _healthProbe.IsHealthyAsync(session.Port, cancellationToken).ConfigureAwait(false))
                {
                    return SingleInstanceDecision.ExistingHealthy();
                }

                var process = _processInspector.Inspect(session.Pid);
                if (!process.Exists || !string.Equals(process.ImagePath, session.ImagePath, StringComparison.OrdinalIgnoreCase))
                {
                    _sessions.DeleteCurrent();
                    if (attempt < MaxAttempts)
                    {
                        continue;
                    }
                    return SingleInstanceDecision.Failed(
                        ErrorCodes.HelperSingletonLost,
                        "Stale helper session file was removed but helper mutex is still held.");
                }

                return SingleInstanceDecision.Failed(
                    ErrorCodes.HelperUnhealthy,
                    "Helper process is still running but /healthz is unhealthy.");
            }

            return SingleInstanceDecision.Failed(
                ErrorCodes.HelperSingletonLost,
                "Unable to acquire helper singleton mutex.");
        }
    }

    public interface INamedMutexLease : IDisposable
    {
        bool Acquired { get; }
    }

    public interface INamedMutexFactory
    {
        INamedMutexLease TryAcquire(string name);
    }

    public sealed class SystemNamedMutexFactory : INamedMutexFactory
    {
        public INamedMutexLease TryAcquire(string name)
        {
            var mutex = new Mutex(false, name);
            try
            {
                if (mutex.WaitOne(0))
                {
                    return new SystemNamedMutexLease(mutex, true);
                }
            }
            catch (AbandonedMutexException)
            {
                return new SystemNamedMutexLease(mutex, true);
            }

            mutex.Dispose();
            return new SystemNamedMutexLease(null, false);
        }
    }

    public sealed class SystemNamedMutexLease : INamedMutexLease
    {
        private readonly Mutex _mutex;
        private bool _disposed;

        public SystemNamedMutexLease(Mutex mutex, bool acquired)
        {
            _mutex = mutex;
            Acquired = acquired;
        }

        public bool Acquired { get; private set; }

        public void Dispose()
        {
            if (_disposed)
            {
                return;
            }
            _disposed = true;
            if (_mutex == null)
            {
                return;
            }
            try
            {
                if (Acquired)
                {
                    _mutex.ReleaseMutex();
                }
            }
            catch (ApplicationException)
            {
            }
            _mutex.Dispose();
        }
    }

    public interface IHealthProbe
    {
        Task<bool> IsHealthyAsync(int port, CancellationToken cancellationToken);
    }

    public sealed class SharedHealthProbe : IHealthProbe
    {
        public async Task<bool> IsHealthyAsync(int port, CancellationToken cancellationToken)
        {
            using (var probe = new HelperProbe())
            {
                var result = await probe.HealthAsync(port, cancellationToken).ConfigureAwait(false);
                return result.IsHealthy;
            }
        }
    }

    public sealed class ProcessSnapshot
    {
        public ProcessSnapshot(bool exists, string imagePath)
        {
            Exists = exists;
            ImagePath = imagePath;
        }

        public bool Exists { get; private set; }
        public string ImagePath { get; private set; }
    }

    public interface IProcessInspector
    {
        ProcessSnapshot Inspect(int pid);
    }

    public sealed class DefaultProcessInspector : IProcessInspector
    {
        public ProcessSnapshot Inspect(int pid)
        {
            try
            {
                using (var process = Process.GetProcessById(pid))
                {
                    var imagePath = string.Empty;
                    try
                    {
                        imagePath = process.MainModule == null ? string.Empty : process.MainModule.FileName;
                    }
                    catch (Exception)
                    {
                        imagePath = string.Empty;
                    }
                    return new ProcessSnapshot(true, imagePath);
                }
            }
            catch (ArgumentException)
            {
                return new ProcessSnapshot(false, null);
            }
        }
    }

    public interface IDelay
    {
        Task DelayAsync(TimeSpan delay, CancellationToken cancellationToken);
    }

    public sealed class SystemDelay : IDelay
    {
        public Task DelayAsync(TimeSpan delay, CancellationToken cancellationToken)
        {
            return Task.Delay(delay, cancellationToken);
        }
    }

    public interface IClock
    {
        DateTimeOffset UtcNow { get; }
    }

    public sealed class SystemClock : IClock
    {
        public DateTimeOffset UtcNow
        {
            get { return DateTimeOffset.UtcNow; }
        }
    }

    public sealed class IdleTracker
    {
        private readonly TimeSpan _timeout;
        private DateTimeOffset _lastActivity;

        public IdleTracker(TimeSpan timeout, DateTimeOffset startedAt)
        {
            _timeout = timeout;
            _lastActivity = startedAt;
        }

        public void Touch(DateTimeOffset timestamp)
        {
            _lastActivity = timestamp;
        }

        public bool IsExpired(DateTimeOffset timestamp)
        {
            return timestamp - _lastActivity >= _timeout;
        }
    }

    public sealed class OriginAllowlistEntry
    {
        public OriginAllowlistEntry(string imageName, string pathPattern)
        {
            ImageName = imageName;
            PathPattern = pathPattern;
        }

        public string ImageName { get; private set; }
        public string PathPattern { get; private set; }
    }

    public sealed class HelperSecurityOptions
    {
        private static readonly OriginAllowlistEntry[] Defaults = new[]
        {
            new OriginAllowlistEntry("acad.exe", @"C:\Program Files\Autodesk\AutoCAD*\acad.exe"),
            new OriginAllowlistEntry("ZWCAD.exe", @"C:\Program Files\ZWSOFT\ZWCAD*\ZWCAD.exe"),
            new OriginAllowlistEntry("gscad.exe", @"C:\Program Files\Gstarsoft\GstarCAD*\gscad.exe"),
            new OriginAllowlistEntry("yuantus-tauri-companion.exe", @"*\YuantusPLM\companion\yuantus-tauri-companion.exe")
        };

        public HelperSecurityOptions(IEnumerable<OriginAllowlistEntry> originAllowlist)
        {
            OriginAllowlist = (originAllowlist ?? Defaults).ToArray();
        }

        public IReadOnlyList<OriginAllowlistEntry> OriginAllowlist { get; private set; }

        public static HelperSecurityOptions Default()
        {
            return new HelperSecurityOptions(Defaults);
        }

        public static HelperSecurityOptions Load(string configPath)
        {
            var entries = new List<OriginAllowlistEntry>(Defaults);
            try
            {
                if (string.IsNullOrWhiteSpace(configPath) || !File.Exists(configPath))
                {
                    return new HelperSecurityOptions(entries);
                }

                var document = JObject.Parse(File.ReadAllText(configPath));
                var configured = document["origin_whitelist"] as JArray;
                if (configured == null)
                {
                    return new HelperSecurityOptions(entries);
                }

                var parsed = new List<OriginAllowlistEntry>();
                foreach (var item in configured)
                {
                    var imageName = item.Value<string>("image_name");
                    var pathPattern = item.Value<string>("path_pattern");
                    if (string.IsNullOrWhiteSpace(imageName) || string.IsNullOrWhiteSpace(pathPattern))
                    {
                        return new HelperSecurityOptions(entries);
                    }
                    parsed.Add(new OriginAllowlistEntry(imageName.Trim(), pathPattern.Trim()));
                }

                entries.AddRange(parsed);
                return new HelperSecurityOptions(Deduplicate(entries));
            }
            catch (Exception)
            {
                return new HelperSecurityOptions(entries);
            }
        }

        private static IEnumerable<OriginAllowlistEntry> Deduplicate(IEnumerable<OriginAllowlistEntry> entries)
        {
            return entries
                .GroupBy(entry => entry.ImageName.ToLowerInvariant() + "\n" + entry.PathPattern.ToLowerInvariant())
                .Select(group => group.First())
                .OrderBy(entry => entry.ImageName, StringComparer.OrdinalIgnoreCase)
                .ThenBy(entry => entry.PathPattern, StringComparer.OrdinalIgnoreCase)
                .ToArray();
        }
    }

    public sealed class HelperSecurityRequest
    {
        public string Method { get; set; }
        public string Path { get; set; }
        public string[] LocalTokenHeaders { get; set; }
        public string[] ProtocolHeaders { get; set; }
        public OriginConnection Connection { get; set; }
    }

    public sealed class HelperSecurityDecision
    {
        private HelperSecurityDecision(bool allowed, int statusCode, string code, string message)
        {
            Allowed = allowed;
            StatusCode = statusCode;
            Code = code;
            Message = message;
        }

        public bool Allowed { get; private set; }
        public int StatusCode { get; private set; }
        public string Code { get; private set; }
        public string Message { get; private set; }

        public static HelperSecurityDecision Allow()
        {
            return new HelperSecurityDecision(true, 0, null, null);
        }

        public static HelperSecurityDecision Deny(int statusCode, string code, string message)
        {
            return new HelperSecurityDecision(false, statusCode, code, message);
        }
    }

    public sealed class HelperSecurityGate
    {
        private readonly string _localToken;
        private readonly HelperSecurityOptions _options;
        private readonly IOriginProcessResolver _originResolver;

        public HelperSecurityGate(string localToken, HelperSecurityOptions options, IOriginProcessResolver originResolver)
        {
            _localToken = localToken ?? string.Empty;
            _options = options ?? HelperSecurityOptions.Default();
            _originResolver = originResolver;
        }

        public HelperSecurityDecision Authorize(HelperSecurityRequest request)
        {
            if (IsExempt(request))
            {
                return HelperSecurityDecision.Allow();
            }

            var token = SingleHeaderValue(request.LocalTokenHeaders);
            if (string.IsNullOrWhiteSpace(token))
            {
                return HelperSecurityDecision.Deny(
                    StatusCodes.Status401Unauthorized,
                    ErrorCodes.AuthLocalTokenMissing,
                    "Local helper token is missing.");
            }

            if (!FixedTimeTokenEquals(_localToken, token))
            {
                return HelperSecurityDecision.Deny(
                    StatusCodes.Status401Unauthorized,
                    ErrorCodes.AuthLocalTokenInvalid,
                    "Local helper token is invalid.");
            }

            var protocol = SingleHeaderValue(request.ProtocolHeaders);
            if (!string.Equals(protocol, Paths.ProtocolVersion, StringComparison.Ordinal))
            {
                return HelperSecurityDecision.Deny(
                    StatusCodes.Status426UpgradeRequired,
                    ErrorCodes.ProtoVersionUnsupported,
                    "Helper protocol version is unsupported.");
            }

            var origin = _originResolver == null ? null : _originResolver.Resolve(request.Connection);
            if (origin == null || !origin.Exists || string.IsNullOrWhiteSpace(origin.ImageName) || string.IsNullOrWhiteSpace(origin.ImagePath))
            {
                return OriginDenied();
            }

            if (!IsAllowedOrigin(origin))
            {
                return OriginDenied();
            }

            return HelperSecurityDecision.Allow();
        }

        public static bool IsExempt(HelperSecurityRequest request)
        {
            if (request == null || !string.Equals(request.Method, "GET", StringComparison.Ordinal))
            {
                return false;
            }

            var path = request.Path ?? string.Empty;
            return string.Equals(path, "/healthz", StringComparison.OrdinalIgnoreCase) ||
                   string.Equals(path, "/version", StringComparison.OrdinalIgnoreCase);
        }

        public static bool FixedTimeTokenEquals(string expected, string supplied)
        {
            var expectedBytes = Encoding.UTF8.GetBytes(expected ?? string.Empty);
            var suppliedBytes = Encoding.UTF8.GetBytes(supplied ?? string.Empty);
            var length = Math.Max(expectedBytes.Length, suppliedBytes.Length);
            var diff = expectedBytes.Length ^ suppliedBytes.Length;

            for (var i = 0; i < length; i++)
            {
                var left = i < expectedBytes.Length ? expectedBytes[i] : (byte)0;
                var right = i < suppliedBytes.Length ? suppliedBytes[i] : (byte)0;
                diff |= left ^ right;
            }

            return diff == 0;
        }

        private bool IsAllowedOrigin(OriginProcess origin)
        {
            foreach (var entry in _options.OriginAllowlist)
            {
                if (string.Equals(origin.ImageName, entry.ImageName, StringComparison.OrdinalIgnoreCase) &&
                    GlobMatches(entry.PathPattern, origin.ImagePath))
                {
                    return true;
                }
            }

            return false;
        }

        private static string SingleHeaderValue(string[] values)
        {
            if (values == null || values.Length != 1)
            {
                return null;
            }
            return values[0];
        }

        private static HelperSecurityDecision OriginDenied()
        {
            return HelperSecurityDecision.Deny(
                StatusCodes.Status403Forbidden,
                ErrorCodes.OriginProcessNotAllowed,
                "Origin process is not allowed.");
        }

        private static bool GlobMatches(string pattern, string value)
        {
            if (pattern == null || value == null)
            {
                return false;
            }

            var regex = "^" + Regex.Escape(pattern).Replace("\\*", ".*").Replace("\\?", ".") + "$";
            return Regex.IsMatch(value, regex, RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        }
    }

    public sealed class OriginConnection
    {
        public IPAddress LocalAddress { get; set; }
        public int LocalPort { get; set; }
        public IPAddress RemoteAddress { get; set; }
        public int RemotePort { get; set; }
    }

    public sealed class OriginProcess
    {
        public OriginProcess(bool exists, string imageName, string imagePath)
        {
            Exists = exists;
            ImageName = imageName;
            ImagePath = imagePath;
        }

        public bool Exists { get; private set; }
        public string ImageName { get; private set; }
        public string ImagePath { get; private set; }
    }

    public interface IOriginProcessResolver
    {
        OriginProcess Resolve(OriginConnection connection);
    }

    public sealed class WindowsTcpOriginProcessResolver : IOriginProcessResolver
    {
        private const int AF_INET = 2;
        private readonly IProcessInspector _processInspector;

        public WindowsTcpOriginProcessResolver(IProcessInspector processInspector)
        {
            _processInspector = processInspector;
        }

        public OriginProcess Resolve(OriginConnection connection)
        {
            if (connection == null ||
                connection.LocalAddress == null ||
                connection.RemoteAddress == null ||
                connection.LocalAddress.AddressFamily != AddressFamily.InterNetwork ||
                connection.RemoteAddress.AddressFamily != AddressFamily.InterNetwork)
            {
                return new OriginProcess(false, null, null);
            }

            var pid = FindOwningPid(connection);
            if (pid <= 0)
            {
                return new OriginProcess(false, null, null);
            }

            var snapshot = _processInspector.Inspect(pid);
            if (!snapshot.Exists || string.IsNullOrWhiteSpace(snapshot.ImagePath))
            {
                return new OriginProcess(false, null, null);
            }

            return new OriginProcess(true, Path.GetFileName(snapshot.ImagePath), snapshot.ImagePath);
        }

        private static int FindOwningPid(OriginConnection connection)
        {
            var size = 0;
            var result = GetExtendedTcpTable(IntPtr.Zero, ref size, true, AF_INET, TcpTableClass.OwnerPidAll, 0);
            if (result != 0 && result != 122)
            {
                return 0;
            }

            var table = Marshal.AllocHGlobal(size);
            try
            {
                result = GetExtendedTcpTable(table, ref size, true, AF_INET, TcpTableClass.OwnerPidAll, 0);
                if (result != 0)
                {
                    return 0;
                }

                var rowCount = Marshal.ReadInt32(table);
                var rowPtr = IntPtr.Add(table, 4);
                var rowSize = Marshal.SizeOf(typeof(TcpRowOwnerPid));
                for (var i = 0; i < rowCount; i++)
                {
                    var row = (TcpRowOwnerPid)Marshal.PtrToStructure(rowPtr, typeof(TcpRowOwnerPid));
                    if (Matches(row, connection))
                    {
                        return (int)row.OwningPid;
                    }
                    rowPtr = IntPtr.Add(rowPtr, rowSize);
                }

                return 0;
            }
            finally
            {
                Marshal.FreeHGlobal(table);
            }
        }

        private static bool Matches(TcpRowOwnerPid row, OriginConnection connection)
        {
            return AddressEquals(row.LocalAddr, connection.LocalAddress) &&
                   AddressEquals(row.RemoteAddr, connection.RemoteAddress) &&
                   PortFromRow(row.LocalPort) == connection.LocalPort &&
                   PortFromRow(row.RemotePort) == connection.RemotePort;
        }

        private static bool AddressEquals(uint rowAddress, IPAddress address)
        {
            return new IPAddress(rowAddress).Equals(address);
        }

        private static int PortFromRow(uint rowPort)
        {
            var bytes = BitConverter.GetBytes(rowPort);
            return (bytes[0] << 8) + bytes[1];
        }

        [DllImport("iphlpapi.dll", SetLastError = true)]
        private static extern uint GetExtendedTcpTable(
            IntPtr tcpTable,
            ref int tcpTableLength,
            bool sort,
            int ipVersion,
            TcpTableClass tableClass,
            uint reserved);

        private enum TcpTableClass
        {
            OwnerPidAll = 5
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct TcpRowOwnerPid
        {
            public uint State;
            public uint LocalAddr;
            public uint LocalPort;
            public uint RemoteAddr;
            public uint RemotePort;
            public uint OwningPid;
        }
    }

    public static class HelperSecurityResponse
    {
        public static async Task WriteAsync(HttpContext context, HelperSecurityDecision decision, CancellationToken cancellationToken)
        {
            context.Response.StatusCode = decision.StatusCode;
            context.Response.ContentType = "application/json; charset=utf-8";
            var envelope = new ResponseEnvelope<object>
            {
                Ok = false,
                Data = null,
                Error = new HelperError
                {
                    Code = decision.Code,
                    Message = decision.Message,
                    Retryable = false,
                    Details = new Dictionary<string, object>()
                }
            };
            await context.Response.WriteAsync(JsonConvert.SerializeObject(envelope), cancellationToken).ConfigureAwait(false);
        }

        public static string ToJson(HelperSecurityDecision decision)
        {
            var envelope = new ResponseEnvelope<object>
            {
                Ok = false,
                Data = null,
                Error = new HelperError
                {
                    Code = decision.Code,
                    Message = decision.Message,
                    Retryable = false,
                    Details = new Dictionary<string, object>()
                }
            };
            return JsonConvert.SerializeObject(envelope);
        }
    }

    public sealed class HelperRouteResult
    {
        private HelperRouteResult(bool ok, object data, string code, string message)
        {
            Ok = ok;
            Data = data;
            Code = code;
            Message = message;
        }

        public bool Ok { get; private set; }
        public object Data { get; private set; }
        public string Code { get; private set; }
        public string Message { get; private set; }

        public static HelperRouteResult Success(object data)
        {
            return new HelperRouteResult(true, data, null, null);
        }

        public static HelperRouteResult Error(string code, string message)
        {
            return new HelperRouteResult(false, null, code, message);
        }
    }

    public static class HelperRouteResponse
    {
        public static Task WriteAsync(HttpContext context, HelperRouteResult result, CancellationToken cancellationToken)
        {
            context.Response.StatusCode = StatusCodes.Status200OK;
            context.Response.ContentType = "application/json; charset=utf-8";
            var envelope = new ResponseEnvelope<object>
            {
                Ok = result.Ok,
                Data = result.Data,
                Error = result.Ok ? null : new HelperError
                {
                    Code = result.Code,
                    Message = result.Message,
                    Retryable = false,
                    Details = new Dictionary<string, object>()
                }
            };
            return context.Response.WriteAsync(JsonConvert.SerializeObject(envelope), cancellationToken);
        }

        public static string ToJson(HelperRouteResult result)
        {
            var envelope = new ResponseEnvelope<object>
            {
                Ok = result.Ok,
                Data = result.Data,
                Error = result.Ok ? null : new HelperError
                {
                    Code = result.Code,
                    Message = result.Message,
                    Retryable = false,
                    Details = new Dictionary<string, object>()
                }
            };
            return JsonConvert.SerializeObject(envelope);
        }
    }

    public sealed class HelperVersionResponse
    {
        [JsonProperty("helper_version")]
        public string HelperVersion { get; set; }

        [JsonProperty("protocol_version")]
        public string ProtocolVersion { get; set; }

        [JsonProperty("features")]
        public string[] Features { get; set; }

        public static HelperVersionResponse Current()
        {
            return new HelperVersionResponse
            {
                HelperVersion = "0.1.0",
                ProtocolVersion = Paths.ProtocolVersion,
                Features = new[] { "session", "current_drawing" }
            };
        }
    }

    public sealed class SessionLoginRequest
    {
        [JsonProperty("server_url")]
        public string ServerUrl { get; set; }

        [JsonProperty("tenant_id")]
        public string TenantId { get; set; }

        [JsonProperty("org_id")]
        public string OrgId { get; set; }

        [JsonProperty("default_profile_id")]
        public string DefaultProfileId { get; set; }

        [JsonProperty("username")]
        public string Username { get; set; }

        [JsonProperty("password")]
        public string Password { get; set; }
    }

    public sealed class SessionLoginResponse
    {
        [JsonProperty("logged_in")]
        public bool LoggedIn { get; set; }

        [JsonProperty("server_url")]
        public string ServerUrl { get; set; }

        [JsonProperty("tenant_id")]
        public string TenantId { get; set; }

        [JsonProperty("org_id")]
        public string OrgId { get; set; }

        [JsonProperty("default_profile_id")]
        public string DefaultProfileId { get; set; }

        [JsonProperty("username")]
        public string Username { get; set; }
    }

    public sealed class SessionStatusResponse
    {
        [JsonProperty("logged_in")]
        public bool LoggedIn { get; set; }

        [JsonProperty("server_url")]
        public string ServerUrl { get; set; }

        [JsonProperty("tenant_id")]
        public string TenantId { get; set; }

        [JsonProperty("org_id")]
        public string OrgId { get; set; }

        [JsonProperty("default_profile_id")]
        public string DefaultProfileId { get; set; }
    }

    public sealed class SessionLogoutResponse
    {
        [JsonProperty("logged_in")]
        public bool LoggedIn { get; set; }
    }

    public sealed class CurrentDrawingRequest
    {
        [JsonProperty("drawing")]
        public CurrentDrawingPayload Drawing { get; set; }

        [JsonProperty("cad_system")]
        public string CadSystem { get; set; }
    }

    public sealed class CurrentDrawingPayload
    {
        [JsonProperty("filename")]
        public string Filename { get; set; }

        [JsonProperty("filepath")]
        public string Filepath { get; set; }
    }

    public sealed class CurrentDrawingResponse
    {
        [JsonProperty("drawing")]
        public CurrentDrawingPayload Drawing { get; set; }

        [JsonProperty("cad_system")]
        public string CadSystem { get; set; }
    }

    public sealed class PlmLoginRequest
    {
        [JsonProperty("tenant_id")]
        public string TenantId { get; set; }

        [JsonProperty("org_id")]
        public string OrgId { get; set; }

        [JsonProperty("username")]
        public string Username { get; set; }

        [JsonProperty("password")]
        public string Password { get; set; }
    }

    public sealed class PlmLoginResponse
    {
        [JsonProperty("access_token")]
        public string AccessToken { get; set; }

        [JsonProperty("tenant_id")]
        public string TenantId { get; set; }

        [JsonProperty("user_id")]
        public string UserId { get; set; }
    }

    public sealed class HelperSessionSnapshot
    {
        public string ServerUrl { get; set; }
        public string TenantId { get; set; }
        public string OrgId { get; set; }
        public string DefaultProfileId { get; set; }
    }

    public interface IHelperSessionConfigStore
    {
        HelperSessionSnapshot Read();
        IReadOnlyList<string> ReadServerAllowlist();
        void SaveLogin(string serverUrl, string tenantId, string orgId, string defaultProfileId);
        void ClearLogin();
    }

    public sealed class JsonHelperSessionConfigStore : IHelperSessionConfigStore
    {
        private readonly string _configPath;

        public JsonHelperSessionConfigStore(string configPath)
        {
            _configPath = configPath;
        }

        public HelperSessionSnapshot Read()
        {
            var document = ReadDocument();
            return new HelperSessionSnapshot
            {
                ServerUrl = Value(document, "server_url"),
                TenantId = Value(document, "tenant_id"),
                OrgId = Value(document, "org_id"),
                DefaultProfileId = Value(document, "default_profile_id")
            };
        }

        public IReadOnlyList<string> ReadServerAllowlist()
        {
            var document = ReadDocument();
            var array = document["server_allowlist"] as JArray;
            if (array == null)
            {
                return new string[0];
            }
            return array
                .Select(item => item.Type == JTokenType.String ? item.Value<string>() : null)
                .Where(value => !string.IsNullOrWhiteSpace(value))
                .Select(value => value.Trim())
                .ToArray();
        }

        public void SaveLogin(string serverUrl, string tenantId, string orgId, string defaultProfileId)
        {
            Update(document =>
            {
                document["server_url"] = serverUrl;
                document["tenant_id"] = tenantId;
                SetOrRemove(document, "org_id", orgId);
                SetOrRemove(document, "default_profile_id", defaultProfileId);
            });
        }

        public void ClearLogin()
        {
            Update(document =>
            {
                document.Remove("tenant_id");
                document.Remove("org_id");
            });
        }

        private void Update(Action<JObject> mutate)
        {
            var document = ReadDocument();
            mutate(document);
            AtomicWrite(document);
        }

        private JObject ReadDocument()
        {
            try
            {
                if (string.IsNullOrWhiteSpace(_configPath) || !File.Exists(_configPath))
                {
                    return new JObject();
                }
                var raw = File.ReadAllText(_configPath, Encoding.UTF8);
                if (string.IsNullOrWhiteSpace(raw))
                {
                    return new JObject();
                }
                return JObject.Parse(raw);
            }
            catch (Exception)
            {
                return new JObject();
            }
        }

        private void AtomicWrite(JObject document)
        {
            var directory = Path.GetDirectoryName(_configPath);
            if (!string.IsNullOrWhiteSpace(directory))
            {
                Directory.CreateDirectory(directory);
            }

            var temp = _configPath + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                File.WriteAllText(temp, JsonConvert.SerializeObject(document, Formatting.Indented), Encoding.UTF8);
                if (File.Exists(_configPath))
                {
                    File.Replace(temp, _configPath, null);
                }
                else
                {
                    File.Move(temp, _configPath);
                }
            }
            finally
            {
                try
                {
                    if (File.Exists(temp))
                    {
                        File.Delete(temp);
                    }
                }
                catch (IOException)
                {
                }
                catch (UnauthorizedAccessException)
                {
                }
            }
        }

        private static string Value(JObject document, string name)
        {
            var token = document[name];
            return token == null || token.Type == JTokenType.Null ? null : token.Value<string>();
        }

        private static void SetOrRemove(JObject document, string name, string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                document.Remove(name);
            }
            else
            {
                document[name] = value;
            }
        }
    }

    public interface IPlmBearerTokenStore
    {
        string Read();
        void Write(string accessToken);
        void Clear();
    }

    public sealed class DpapiPlmBearerTokenStore : IPlmBearerTokenStore
    {
        public const string EntropyLiteral = "yuantus-cad-plm-bearer-v1";
        private static readonly byte[] Entropy = Encoding.UTF8.GetBytes(EntropyLiteral);
        private readonly string _tokenPath;

        public DpapiPlmBearerTokenStore(string tokenPath)
        {
            _tokenPath = tokenPath;
        }

        public string Read()
        {
            try
            {
                if (!File.Exists(_tokenPath))
                {
                    return null;
                }
                var encrypted = File.ReadAllBytes(_tokenPath);
                var plain = DpapiEnvelope.Unprotect(encrypted, Entropy);
                return Encoding.UTF8.GetString(plain);
            }
            catch (HelperException)
            {
                throw;
            }
            catch (Exception ex)
            {
                throw new HelperException(ErrorCodes.HelperDpapiUnavailable, "Unable to read PLM bearer token.", false, null, ex);
            }
        }

        public void Write(string accessToken)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(_tokenPath));
                var encrypted = DpapiEnvelope.Protect(Encoding.UTF8.GetBytes(accessToken ?? string.Empty), Entropy);
                File.WriteAllBytes(_tokenPath, encrypted);
            }
            catch (HelperException)
            {
                throw;
            }
            catch (Exception ex)
            {
                throw new HelperException(ErrorCodes.HelperDpapiUnavailable, "Unable to write PLM bearer token.", false, null, ex);
            }
        }

        public void Clear()
        {
            try
            {
                if (File.Exists(_tokenPath))
                {
                    File.Delete(_tokenPath);
                }
            }
            catch (Exception ex)
            {
                throw new HelperException(ErrorCodes.HelperDpapiUnavailable, "Unable to clear PLM bearer token.", false, null, ex);
            }
        }
    }

    public sealed class ServerAllowlist
    {
        private readonly string[] _patterns;

        public ServerAllowlist(IEnumerable<string> patterns)
        {
            _patterns = (patterns ?? new string[0])
                .Where(pattern => !string.IsNullOrWhiteSpace(pattern))
                .Select(pattern => pattern.Trim())
                .ToArray();
        }

        public bool Allows(Uri serverUri)
        {
            if (_patterns.Length == 0)
            {
                return true;
            }

            foreach (var pattern in _patterns)
            {
                if (Matches(pattern, serverUri))
                {
                    return true;
                }
            }
            return false;
        }

        private static bool Matches(string pattern, Uri serverUri)
        {
            if (pattern.IndexOf("://*.", StringComparison.Ordinal) > 0)
            {
                return MatchesWildcard(pattern, serverUri);
            }

            Uri patternUri;
            if (!Uri.TryCreate(pattern, UriKind.Absolute, out patternUri))
            {
                return false;
            }
            if (!string.Equals(patternUri.Scheme, serverUri.Scheme, StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            if (NormalizePort(patternUri) != NormalizePort(serverUri))
            {
                return false;
            }
            return HostMatches(patternUri.Host, serverUri.Host);
        }

        private static bool MatchesWildcard(string pattern, Uri serverUri)
        {
            var schemeSeparator = pattern.IndexOf("://", StringComparison.Ordinal);
            if (schemeSeparator <= 0)
            {
                return false;
            }

            var scheme = pattern.Substring(0, schemeSeparator);
            if (!string.Equals(scheme, serverUri.Scheme, StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }

            var hostAndPort = pattern.Substring(schemeSeparator + 3);
            var slashIndex = hostAndPort.IndexOf('/');
            if (slashIndex >= 0)
            {
                hostAndPort = hostAndPort.Substring(0, slashIndex);
            }

            var host = hostAndPort;
            int? port = null;
            var colonIndex = hostAndPort.LastIndexOf(':');
            if (colonIndex > 0)
            {
                int parsedPort;
                if (!int.TryParse(hostAndPort.Substring(colonIndex + 1), out parsedPort))
                {
                    return false;
                }
                host = hostAndPort.Substring(0, colonIndex);
                port = parsedPort;
            }

            if (!HostMatches(host, serverUri.Host))
            {
                return false;
            }
            return (port ?? DefaultPort(serverUri.Scheme)) == NormalizePort(serverUri);
        }

        private static bool HostMatches(string patternHost, string serverHost)
        {
            if (patternHost.StartsWith("*.", StringComparison.Ordinal))
            {
                var suffix = patternHost.Substring(1);
                return serverHost.EndsWith(suffix, StringComparison.OrdinalIgnoreCase) &&
                       serverHost.Length > suffix.Length;
            }
            return string.Equals(patternHost, serverHost, StringComparison.OrdinalIgnoreCase);
        }

        private static int NormalizePort(Uri uri)
        {
            if (!uri.IsDefaultPort)
            {
                return uri.Port;
            }
            return DefaultPort(uri.Scheme);
        }

        private static int DefaultPort(string scheme)
        {
            if (string.Equals(scheme, "https", StringComparison.OrdinalIgnoreCase))
            {
                return 443;
            }
            return 80;
        }
    }

    public interface IPlmLoginClient
    {
        Task<PlmLoginResponse> LoginAsync(Uri serverUri, PlmLoginRequest request, CancellationToken cancellationToken);
    }

    public sealed class PlmLoginRejectedException : Exception
    {
        public PlmLoginRejectedException(string message)
            : base(message)
        {
        }
    }

    public sealed class HttpPlmLoginClient : IPlmLoginClient
    {
        private readonly HttpClient _httpClient;

        public HttpPlmLoginClient(HttpClient httpClient)
        {
            _httpClient = httpClient;
        }

        public async Task<PlmLoginResponse> LoginAsync(Uri serverUri, PlmLoginRequest request, CancellationToken cancellationToken)
        {
            var endpoint = new Uri(serverUri.ToString().TrimEnd('/') + "/auth/login");
            var json = JsonConvert.SerializeObject(request);
            using (var content = new StringContent(json, Encoding.UTF8, "application/json"))
            using (var response = await _httpClient.PostAsync(endpoint, content, cancellationToken).ConfigureAwait(false))
            {
                var body = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                if (response.StatusCode == HttpStatusCode.Unauthorized || response.StatusCode == HttpStatusCode.Forbidden)
                {
                    throw new PlmLoginRejectedException(ExtractDetail(body));
                }
                if (!response.IsSuccessStatusCode)
                {
                    throw new HttpRequestException("PLM login validation failed with HTTP " + (int)response.StatusCode + ".");
                }
                return JsonConvert.DeserializeObject<PlmLoginResponse>(body);
            }
        }

        private static string ExtractDetail(string body)
        {
            try
            {
                var json = JObject.Parse(body ?? string.Empty);
                return json.Value<string>("detail") ?? "PLM login rejected.";
            }
            catch (Exception)
            {
                return "PLM login rejected.";
            }
        }
    }

    public sealed class CurrentDrawingStore
    {
        private CurrentDrawingResponse _current;

        public CurrentDrawingResponse Read()
        {
            return _current;
        }

        public CurrentDrawingResponse Write(CurrentDrawingRequest request)
        {
            var response = new CurrentDrawingResponse
            {
                Drawing = new CurrentDrawingPayload
                {
                    Filename = request.Drawing.Filename.Trim(),
                    Filepath = request.Drawing.Filepath
                },
                CadSystem = string.IsNullOrWhiteSpace(request.CadSystem) ? null : request.CadSystem.Trim().ToLowerInvariant()
            };
            _current = response;
            return response;
        }
    }

    public sealed class HelperSessionService
    {
        private readonly IHelperSessionConfigStore _config;
        private readonly IPlmBearerTokenStore _bearer;
        private readonly IPlmLoginClient _plm;
        private readonly CurrentDrawingStore _drawing;

        public HelperSessionService(
            IHelperSessionConfigStore config,
            IPlmBearerTokenStore bearer,
            IPlmLoginClient plm,
            CurrentDrawingStore drawing)
        {
            _config = config;
            _bearer = bearer;
            _plm = plm;
            _drawing = drawing;
        }

        public async Task<HelperRouteResult> LoginAsync(SessionLoginRequest request, CancellationToken cancellationToken)
        {
            Uri serverUri;
            var validation = ValidateLoginRequest(request, out serverUri);
            if (validation != null)
            {
                return validation;
            }

            if (!new ServerAllowlist(_config.ReadServerAllowlist()).Allows(serverUri))
            {
                return HelperRouteResult.Error(ErrorCodes.HelperInputValidationFailed, "server_url is not allowed.");
            }

            try
            {
                var response = await _plm.LoginAsync(serverUri, new PlmLoginRequest
                {
                    TenantId = request.TenantId.Trim(),
                    OrgId = TrimToNull(request.OrgId),
                    Username = request.Username.Trim(),
                    Password = request.Password
                }, cancellationToken).ConfigureAwait(false);

                if (response == null || string.IsNullOrWhiteSpace(response.AccessToken))
                {
                    return HelperRouteResult.Error(ErrorCodes.PlmValidationFailed, "PLM login did not return an access token.");
                }

                _bearer.Write(response.AccessToken);
                var normalized = NormalizeServerUrl(serverUri);
                _config.SaveLogin(normalized, request.TenantId.Trim(), TrimToNull(request.OrgId), TrimToNull(request.DefaultProfileId));
                return HelperRouteResult.Success(new SessionLoginResponse
                {
                    LoggedIn = true,
                    ServerUrl = normalized,
                    TenantId = request.TenantId.Trim(),
                    OrgId = TrimToNull(request.OrgId),
                    DefaultProfileId = TrimToNull(request.DefaultProfileId),
                    Username = request.Username.Trim()
                });
            }
            catch (PlmLoginRejectedException ex)
            {
                return HelperRouteResult.Error(ErrorCodes.AuthPlmNotLoggedIn, ex.Message);
            }
            catch (HelperException ex)
            {
                if (ex.Code == ErrorCodes.HelperDpapiUnavailable)
                {
                    return HelperRouteResult.Error(ErrorCodes.HelperDpapiUnavailable, ex.Message);
                }
                throw;
            }
            catch (Exception ex)
            {
                return HelperRouteResult.Error(ErrorCodes.PlmValidationFailed, ex.Message);
            }
        }

        public HelperRouteResult Logout()
        {
            _bearer.Clear();
            _config.ClearLogin();
            return HelperRouteResult.Success(new SessionLogoutResponse { LoggedIn = false });
        }

        public HelperRouteResult Status()
        {
            var snapshot = _config.Read();
            string token = null;
            try
            {
                token = _bearer.Read();
            }
            catch (HelperException ex)
            {
                if (ex.Code == ErrorCodes.HelperDpapiUnavailable)
                {
                    return HelperRouteResult.Error(ErrorCodes.HelperDpapiUnavailable, ex.Message);
                }
                throw;
            }

            return HelperRouteResult.Success(new SessionStatusResponse
            {
                LoggedIn = !string.IsNullOrWhiteSpace(token) && !string.IsNullOrWhiteSpace(snapshot.TenantId),
                ServerUrl = snapshot.ServerUrl,
                TenantId = snapshot.TenantId,
                OrgId = snapshot.OrgId,
                DefaultProfileId = snapshot.DefaultProfileId
            });
        }

        public HelperRouteResult SetCurrentDrawing(CurrentDrawingRequest request)
        {
            if (request == null || request.Drawing == null || string.IsNullOrWhiteSpace(request.Drawing.Filename))
            {
                return HelperRouteResult.Error(ErrorCodes.HelperInputValidationFailed, "drawing.filename is required.");
            }
            if (!string.IsNullOrWhiteSpace(request.CadSystem) &&
                !new[] { "autocad", "zwcad", "gstarcad" }.Contains(request.CadSystem.Trim().ToLowerInvariant()))
            {
                return HelperRouteResult.Error(ErrorCodes.HelperInputValidationFailed, "cad_system is invalid.");
            }
            return HelperRouteResult.Success(_drawing.Write(request));
        }

        private static HelperRouteResult ValidateLoginRequest(SessionLoginRequest request, out Uri serverUri)
        {
            serverUri = null;
            if (request == null ||
                string.IsNullOrWhiteSpace(request.ServerUrl) ||
                string.IsNullOrWhiteSpace(request.TenantId) ||
                string.IsNullOrWhiteSpace(request.Username) ||
                string.IsNullOrWhiteSpace(request.Password))
            {
                return HelperRouteResult.Error(ErrorCodes.HelperInputValidationFailed, "server_url, tenant_id, username, and password are required.");
            }
            if (!Uri.TryCreate(request.ServerUrl.Trim(), UriKind.Absolute, out serverUri) ||
                (!string.Equals(serverUri.Scheme, "http", StringComparison.OrdinalIgnoreCase) &&
                 !string.Equals(serverUri.Scheme, "https", StringComparison.OrdinalIgnoreCase)))
            {
                return HelperRouteResult.Error(ErrorCodes.HelperInputValidationFailed, "server_url must be an absolute http or https URL.");
            }
            return null;
        }

        private static string NormalizeServerUrl(Uri serverUri)
        {
            return serverUri.GetLeftPart(UriPartial.Path).TrimEnd('/');
        }

        private static string TrimToNull(string value)
        {
            return string.IsNullOrWhiteSpace(value) ? null : value.Trim();
        }
    }

    public sealed class PlmBusinessResponse
    {
        private PlmBusinessResponse(bool ok, JToken data, string code, string message)
        {
            Ok = ok;
            Data = data;
            Code = code;
            Message = message;
        }

        public bool Ok { get; private set; }
        public JToken Data { get; private set; }
        public string Code { get; private set; }
        public string Message { get; private set; }

        public static PlmBusinessResponse Success(JToken data)
        {
            return new PlmBusinessResponse(true, data, null, null);
        }

        public static PlmBusinessResponse Error(string code, string message)
        {
            return new PlmBusinessResponse(false, null, code, message);
        }
    }

    public interface IPlmBusinessClient
    {
        Task<PlmBusinessResponse> PostAsync(
            Uri serverUri,
            string endpointPath,
            string bearerToken,
            string traceId,
            JObject payload,
            CancellationToken cancellationToken);
    }

    public sealed class HttpPlmBusinessClient : IPlmBusinessClient
    {
        private readonly HttpClient _httpClient;

        public HttpPlmBusinessClient(HttpClient httpClient)
        {
            _httpClient = httpClient;
        }

        public async Task<PlmBusinessResponse> PostAsync(
            Uri serverUri,
            string endpointPath,
            string bearerToken,
            string traceId,
            JObject payload,
            CancellationToken cancellationToken)
        {
            var endpoint = new Uri(serverUri.ToString().TrimEnd('/') + endpointPath);
            using (var request = new HttpRequestMessage(HttpMethod.Post, endpoint))
            {
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", bearerToken);
                request.Headers.TryAddWithoutValidation("X-Yuantus-Protocol", Paths.ProtocolVersion);
                request.Headers.TryAddWithoutValidation("X-Yuantus-Trace-Id", traceId);
                request.Content = new StringContent(
                    JsonConvert.SerializeObject(payload ?? new JObject()),
                    Encoding.UTF8,
                    "application/json");

                using (var response = await _httpClient.SendAsync(request, cancellationToken).ConfigureAwait(false))
                {
                    var body = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                    if (response.StatusCode == HttpStatusCode.Unauthorized || response.StatusCode == HttpStatusCode.Forbidden)
                    {
                        return PlmBusinessResponse.Error(ErrorCodes.AuthPlmNotLoggedIn, "PLM session is not authorized.");
                    }
                    if (!response.IsSuccessStatusCode)
                    {
                        return PlmBusinessResponse.Error(ErrorCodes.PlmValidationFailed, "PLM request failed with HTTP " + (int)response.StatusCode + ".");
                    }
                    return ParsePlmBody(endpointPath, body);
                }
            }
        }

        private static PlmBusinessResponse ParsePlmBody(string endpointPath, string body)
        {
            JToken parsed;
            try
            {
                parsed = JToken.Parse(string.IsNullOrWhiteSpace(body) ? "{}" : body);
            }
            catch (JsonException)
            {
                return PlmBusinessResponse.Error(ErrorCodes.PlmValidationFailed, "PLM response was not valid JSON.");
            }

            var obj = parsed as JObject;
            if (obj == null)
            {
                return PlmBusinessResponse.Success(parsed);
            }

            var okToken = obj["ok"];
            if (okToken != null && okToken.Type == JTokenType.Boolean && !okToken.Value<bool>())
            {
                var envelopeError = obj["error"] as JObject;
                if (envelopeError != null && !string.IsNullOrWhiteSpace(envelopeError.Value<string>("code")))
                {
                    return PlmBusinessResponse.Error(
                        envelopeError.Value<string>("code"),
                        envelopeError.Value<string>("message") ?? "PLM request failed.");
                }

                if (string.Equals(obj.Value<string>("action"), "conflict", StringComparison.OrdinalIgnoreCase) ||
                    HasNonEmptyArray(obj["conflicts"]))
                {
                    return PlmBusinessResponse.Error(ErrorCodes.PlmInboundConflict, "PLM inbound sync reported conflicts.");
                }

                return PlmBusinessResponse.Error(ErrorCodes.PlmValidationFailed, "PLM request returned ok=false.");
            }

            return PlmBusinessResponse.Success(obj);
        }

        private static bool HasNonEmptyArray(JToken token)
        {
            var array = token as JArray;
            return array != null && array.Count > 0;
        }
    }

    public sealed class PullCacheEntry
    {
        public string PullId { get; set; }
        public string DrawingPath { get; set; }
        public JObject WriteCadFields { get; set; }
        public DateTimeOffset CreatedAt { get; set; }
        public bool Reported { get; set; }
    }

    public enum PullCacheLookupStatus
    {
        Ready,
        Unknown,
        Expired,
        AlreadyReported
    }

    public sealed class PullCacheLookup
    {
        private PullCacheLookup(PullCacheLookupStatus status, PullCacheEntry entry)
        {
            Status = status;
            Entry = entry;
        }

        public PullCacheLookupStatus Status { get; private set; }
        public PullCacheEntry Entry { get; private set; }

        public static PullCacheLookup Ready(PullCacheEntry entry)
        {
            return new PullCacheLookup(PullCacheLookupStatus.Ready, entry);
        }

        public static PullCacheLookup Unknown()
        {
            return new PullCacheLookup(PullCacheLookupStatus.Unknown, null);
        }

        public static PullCacheLookup Expired(PullCacheEntry entry)
        {
            return new PullCacheLookup(PullCacheLookupStatus.Expired, entry);
        }

        public static PullCacheLookup AlreadyReported(PullCacheEntry entry)
        {
            return new PullCacheLookup(PullCacheLookupStatus.AlreadyReported, entry);
        }
    }

    public sealed class PullCache
    {
        public static readonly TimeSpan Ttl = TimeSpan.FromMinutes(10);
        private readonly Dictionary<string, PullCacheEntry> _entries = new Dictionary<string, PullCacheEntry>(StringComparer.Ordinal);
        private readonly object _lock = new object();

        public PullCacheEntry Create(JObject request, JToken serverResponse, DateTimeOffset createdAt)
        {
            var entry = new PullCacheEntry
            {
                PullId = "PULL-" + Guid.NewGuid().ToString("N"),
                DrawingPath = ExtractDrawingPathForAudit(request),
                WriteCadFields = ExtractObject(ExtractServerObjectToken(serverResponse, "write_cad_fields")),
                CreatedAt = createdAt,
                Reported = false
            };
            lock (_lock)
            {
                _entries[entry.PullId] = entry;
            }
            return entry;
        }

        public PullCacheLookup Lookup(string pullId, DateTimeOffset now)
        {
            PullCacheEntry entry;
            lock (_lock)
            {
                if (!_entries.TryGetValue(pullId ?? string.Empty, out entry))
                {
                    return PullCacheLookup.Unknown();
                }
                if (entry.Reported)
                {
                    return PullCacheLookup.AlreadyReported(entry);
                }
                if (now - entry.CreatedAt >= Ttl)
                {
                    return PullCacheLookup.Expired(entry);
                }
                return PullCacheLookup.Ready(entry);
            }
        }

        public PullCacheLookup ClaimForReport(string pullId, DateTimeOffset now)
        {
            lock (_lock)
            {
                PullCacheEntry entry;
                if (!_entries.TryGetValue(pullId ?? string.Empty, out entry))
                {
                    return PullCacheLookup.Unknown();
                }
                if (entry.Reported)
                {
                    return PullCacheLookup.AlreadyReported(entry);
                }
                if (now - entry.CreatedAt >= Ttl)
                {
                    return PullCacheLookup.Expired(entry);
                }
                entry.Reported = true;
                return PullCacheLookup.Ready(entry);
            }
        }

        public void ReleaseReportClaim(string pullId)
        {
            lock (_lock)
            {
                PullCacheEntry entry;
                if (_entries.TryGetValue(pullId ?? string.Empty, out entry))
                {
                    entry.Reported = false;
                }
            }
        }

        public static string ExtractDrawingPathForAudit(JObject request)
        {
            if (request == null)
            {
                return null;
            }
            var nested = request.SelectToken("drawing.filepath");
            if (nested != null && nested.Type == JTokenType.String)
            {
                return nested.Value<string>();
            }
            return request.Value<string>("drawing_path");
        }

        private static JObject ExtractObject(JToken token)
        {
            var obj = token as JObject;
            return obj == null ? new JObject() : (JObject)obj.DeepClone();
        }

        private static JToken ExtractServerObjectToken(JToken serverResponse, string name)
        {
            var obj = serverResponse as JObject;
            return obj == null ? null : obj[name];
        }
    }

    public sealed class AuditEvent
    {
        public DateTimeOffset Timestamp { get; set; }
        public string Endpoint { get; set; }
        public string DrawingPath { get; set; }
        public string ProfileId { get; set; }
        public string ItemId { get; set; }
        public string PullId { get; set; }
        public string CadSystem { get; set; }
        public string Outcome { get; set; }
        public string ErrorCode { get; set; }
        public int DurationMs { get; set; }
        public string TraceId { get; set; }
        public string AppliedFieldsJson { get; set; }
        public string FailedFieldsJson { get; set; }
    }

    public interface IAuditEventStore
    {
        void Write(AuditEvent auditEvent);
    }

    public sealed class SqliteAuditEventStore : IAuditEventStore
    {
        private readonly string _databasePath;

        public SqliteAuditEventStore(string databasePath)
        {
            _databasePath = databasePath;
        }

        public void Write(AuditEvent auditEvent)
        {
            if (auditEvent == null)
            {
                throw new ArgumentNullException("auditEvent");
            }

            var directory = Path.GetDirectoryName(_databasePath);
            if (!string.IsNullOrWhiteSpace(directory))
            {
                Directory.CreateDirectory(directory);
            }

            var builder = new SqliteConnectionStringBuilder { DataSource = _databasePath };
            using (var connection = new SqliteConnection(builder.ToString()))
            {
                connection.Open();
                EnsureSchema(connection);
                using (var command = connection.CreateCommand())
                {
                    command.CommandText = @"
INSERT INTO audit_events (
  ts, endpoint, drawing_path, profile_id, item_id, pull_id, cad_system,
  outcome, error_code, duration_ms, trace_id, applied_fields_json,
  failed_fields_json
) VALUES (
  $ts, $endpoint, $drawing_path, $profile_id, $item_id, $pull_id, $cad_system,
  $outcome, $error_code, $duration_ms, $trace_id, $applied_fields_json,
  $failed_fields_json
)";
                    Add(command, "$ts", auditEvent.Timestamp.ToUniversalTime().ToString("o"));
                    Add(command, "$endpoint", auditEvent.Endpoint);
                    Add(command, "$drawing_path", auditEvent.DrawingPath);
                    Add(command, "$profile_id", auditEvent.ProfileId);
                    Add(command, "$item_id", auditEvent.ItemId);
                    Add(command, "$pull_id", auditEvent.PullId);
                    Add(command, "$cad_system", auditEvent.CadSystem);
                    Add(command, "$outcome", auditEvent.Outcome);
                    Add(command, "$error_code", auditEvent.ErrorCode);
                    Add(command, "$duration_ms", auditEvent.DurationMs);
                    Add(command, "$trace_id", auditEvent.TraceId);
                    Add(command, "$applied_fields_json", auditEvent.AppliedFieldsJson);
                    Add(command, "$failed_fields_json", auditEvent.FailedFieldsJson);
                    command.ExecuteNonQuery();
                }
            }
        }

        private static void EnsureSchema(SqliteConnection connection)
        {
            using (var command = connection.CreateCommand())
            {
                command.CommandText = @"
CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  drawing_path TEXT,
  profile_id TEXT,
  item_id TEXT,
  pull_id TEXT,
  cad_system TEXT,
  outcome TEXT NOT NULL,
  error_code TEXT,
  duration_ms INTEGER NOT NULL,
  trace_id TEXT NOT NULL,
  applied_fields_json TEXT,
  failed_fields_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts);
CREATE INDEX IF NOT EXISTS idx_audit_pull ON audit_events(pull_id);
";
                command.ExecuteNonQuery();
            }
        }

        private static void Add(SqliteCommand command, string name, object value)
        {
            command.Parameters.AddWithValue(name, value ?? DBNull.Value);
        }
    }

    public interface IAuditWarningWriter
    {
        void WriteAuditFailure(string endpoint, string traceId, string reason);
    }

    public sealed class ConsoleAuditWarningWriter : IAuditWarningWriter
    {
        public void WriteAuditFailure(string endpoint, string traceId, string reason)
        {
            Console.Error.WriteLine("[AUDIT_WRITE_FAILED] endpoint=" + Sanitize(endpoint) + " trace_id=" + Sanitize(traceId) + " reason=" + Sanitize(reason));
        }

        private static string Sanitize(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return "unknown";
            }
            return Regex.Replace(value, @"[^A-Za-z0-9_\-./]", "_");
        }
    }

    public sealed class HelperBusinessAuditService
    {
        private const string DiffPreviewEndpoint = "/plugins/cad-material-sync/diff/preview";
        private const string SyncInboundEndpoint = "/plugins/cad-material-sync/sync/inbound";
        private const string SyncOutboundEndpoint = "/plugins/cad-material-sync/sync/outbound";

        private static readonly HashSet<string> ApplyResultOutcomes = new HashSet<string>(StringComparer.Ordinal)
        {
            "ok",
            "partial",
            "failed",
            "not-applied-display-only"
        };

        private readonly IHelperSessionConfigStore _config;
        private readonly IPlmBearerTokenStore _bearer;
        private readonly IPlmBusinessClient _plm;
        private readonly PullCache _pullCache;
        private readonly IAuditEventStore _audit;
        private readonly IClock _clock;
        private readonly IAuditWarningWriter _warnings;

        public HelperBusinessAuditService(
            IHelperSessionConfigStore config,
            IPlmBearerTokenStore bearer,
            IPlmBusinessClient plm,
            PullCache pullCache,
            IAuditEventStore audit,
            IClock clock,
            IAuditWarningWriter warnings)
        {
            _config = config;
            _bearer = bearer;
            _plm = plm;
            _pullCache = pullCache;
            _audit = audit;
            _clock = clock;
            _warnings = warnings;
        }

        public async Task<HelperRouteResult> DiffPreviewAsync(JObject request, CancellationToken cancellationToken)
        {
            if (request == null || string.IsNullOrWhiteSpace(request.Value<string>("item_id")))
            {
                return HelperRouteResult.Error(ErrorCodes.HelperInputValidationFailed, "item_id is required.");
            }
            if (HasNonEmptyObject(request["values"]) ||
                HasNonEmptyObject(request["target_properties"]) ||
                HasNonEmptyObject(request["target_cad_fields"]))
            {
                return HelperRouteResult.Error(ErrorCodes.HelperInputValidationFailed, "S6 diff preview accepts item_id path only.");
            }

            Uri serverUri;
            string bearer;
            var sessionError = TryReadSession(out serverUri, out bearer);
            if (sessionError != null)
            {
                return sessionError;
            }

            var traceId = NewTraceId();
            var started = _clock.UtcNow;
            var response = await _plm.PostAsync(serverUri, DiffPreviewEndpoint, bearer, traceId, request, cancellationToken).ConfigureAwait(false);
            var result = ToRouteResult(response);
            PullCacheEntry pull = null;
            if (response.Ok)
            {
                pull = _pullCache.Create(request, response.Data, _clock.UtcNow);
                result = HelperRouteResult.Success(new JObject
                {
                    ["pull_id"] = pull.PullId,
                    ["server_response"] = response.Data == null ? new JObject() : response.Data.DeepClone()
                });
            }
            return WriteAuditAfterBusiness("/diff/preview", request, result, pull, traceId, started);
        }

        public Task<HelperRouteResult> SyncInboundAsync(JObject request, CancellationToken cancellationToken)
        {
            return ForwardBusinessAsync("/sync/inbound", SyncInboundEndpoint, request, cancellationToken);
        }

        public Task<HelperRouteResult> SyncOutboundAsync(JObject request, CancellationToken cancellationToken)
        {
            return ForwardBusinessAsync("/sync/outbound", SyncOutboundEndpoint, request, cancellationToken);
        }

        public HelperRouteResult ApplyResult(JObject request)
        {
            var traceId = NewTraceId();
            var started = _clock.UtcNow;
            var pullId = request == null ? null : request.Value<string>("pull_id");
            var outcome = request == null ? null : request.Value<string>("outcome");

            if (string.IsNullOrWhiteSpace(pullId) || string.IsNullOrWhiteSpace(outcome) || !ApplyResultOutcomes.Contains(outcome))
            {
                return HelperRouteResult.Error(ErrorCodes.HelperInputValidationFailed, "pull_id and a valid outcome are required.");
            }

            var lookup = _pullCache.ClaimForReport(pullId, _clock.UtcNow);
            if (lookup.Status == PullCacheLookupStatus.Unknown)
            {
                return HelperRouteResult.Error(ErrorCodes.AuditPullIdUnknown, "pull_id is unknown.");
            }
            if (lookup.Status == PullCacheLookupStatus.Expired)
            {
                return HelperRouteResult.Error(ErrorCodes.AuditPullIdExpired, "pull_id has expired.");
            }
            if (lookup.Status == PullCacheLookupStatus.AlreadyReported)
            {
                return HelperRouteResult.Error(ErrorCodes.AuditAlreadyReported, "pull_id has already been reported.");
            }

            try
            {
                _audit.Write(CreateAuditEvent(
                    "/audit/apply-result",
                    request,
                    lookup.Entry,
                    outcome,
                    null,
                    traceId,
                    started,
                    JsonObjectString(request["applied_fields"]),
                    JsonObjectString(request["failed_fields"])));
                return HelperRouteResult.Success(new JObject
                {
                    ["reported"] = true,
                    ["pull_id"] = pullId
                });
            }
            catch (Exception)
            {
                _pullCache.ReleaseReportClaim(pullId);
                return HelperRouteResult.Error(ErrorCodes.AuditWriteFailed, "Unable to write local audit row.");
            }
        }

        public void AuditSessionResult(string endpoint, HelperRouteResult result, DateTimeOffset? startedAt = null)
        {
            var traceId = NewTraceId();
            var now = _clock.UtcNow;
            try
            {
                _audit.Write(new AuditEvent
                {
                    Timestamp = now,
                    Endpoint = endpoint,
                    Outcome = result != null && result.Ok ? "ok" : "failed",
                    ErrorCode = result == null || result.Ok ? null : result.Code,
                    DurationMs = startedAt.HasValue ? Math.Max(0, (int)(now - startedAt.Value).TotalMilliseconds) : 0,
                    TraceId = traceId
                });
            }
            catch (Exception ex)
            {
                _warnings.WriteAuditFailure(endpoint, traceId, ShortReason(ex));
            }
        }

        private async Task<HelperRouteResult> ForwardBusinessAsync(
            string helperEndpoint,
            string plmEndpoint,
            JObject request,
            CancellationToken cancellationToken)
        {
            Uri serverUri;
            string bearer;
            var sessionError = TryReadSession(out serverUri, out bearer);
            if (sessionError != null)
            {
                return sessionError;
            }

            var traceId = NewTraceId();
            var started = _clock.UtcNow;
            var response = await _plm.PostAsync(serverUri, plmEndpoint, bearer, traceId, request ?? new JObject(), cancellationToken).ConfigureAwait(false);
            return WriteAuditAfterBusiness(helperEndpoint, request, ToRouteResult(response), null, traceId, started);
        }

        private HelperRouteResult TryReadSession(out Uri serverUri, out string bearer)
        {
            serverUri = null;
            bearer = null;

            var snapshot = _config.Read();
            if (snapshot == null || string.IsNullOrWhiteSpace(snapshot.TenantId) || string.IsNullOrWhiteSpace(snapshot.ServerUrl))
            {
                return HelperRouteResult.Error(ErrorCodes.AuthTenantMissing, "tenant_id and server_url are required.");
            }
            if (!Uri.TryCreate(snapshot.ServerUrl, UriKind.Absolute, out serverUri))
            {
                return HelperRouteResult.Error(ErrorCodes.PlmValidationFailed, "server_url is invalid.");
            }

            try
            {
                bearer = _bearer.Read();
            }
            catch (HelperException ex)
            {
                return HelperRouteResult.Error(ex.Code, ex.Message);
            }
            if (string.IsNullOrWhiteSpace(bearer))
            {
                return HelperRouteResult.Error(ErrorCodes.AuthPlmNotLoggedIn, "PLM bearer token is missing.");
            }
            return null;
        }

        private HelperRouteResult WriteAuditAfterBusiness(
            string endpoint,
            JObject request,
            HelperRouteResult result,
            PullCacheEntry entry,
            string traceId,
            DateTimeOffset started)
        {
            try
            {
                _audit.Write(CreateAuditEvent(
                    endpoint,
                    request,
                    entry,
                    result.Ok ? "ok" : "failed",
                    result.Ok ? null : result.Code,
                    traceId,
                    started,
                    null,
                    null));
            }
            catch (Exception ex)
            {
                if (result.Ok)
                {
                    _warnings.WriteAuditFailure(endpoint, traceId, ShortReason(ex));
                }
            }
            return result;
        }

        private AuditEvent CreateAuditEvent(
            string endpoint,
            JObject request,
            PullCacheEntry entry,
            string outcome,
            string errorCode,
            string traceId,
            DateTimeOffset started,
            string appliedFieldsJson,
            string failedFieldsJson)
        {
            var now = _clock.UtcNow;
            return new AuditEvent
            {
                Timestamp = now,
                Endpoint = endpoint,
                DrawingPath = entry == null ? PullCache.ExtractDrawingPathForAudit(request) : entry.DrawingPath,
                ProfileId = request == null ? null : request.Value<string>("profile_id"),
                ItemId = request == null ? null : request.Value<string>("item_id"),
                PullId = entry == null ? request == null ? null : request.Value<string>("pull_id") : entry.PullId,
                CadSystem = request == null ? null : request.Value<string>("cad_system"),
                Outcome = outcome,
                ErrorCode = errorCode,
                DurationMs = Math.Max(0, (int)(now - started).TotalMilliseconds),
                TraceId = traceId,
                AppliedFieldsJson = appliedFieldsJson,
                FailedFieldsJson = failedFieldsJson
            };
        }

        private static HelperRouteResult ToRouteResult(PlmBusinessResponse response)
        {
            if (response.Ok)
            {
                return HelperRouteResult.Success(response.Data == null ? new JObject() : response.Data.DeepClone());
            }
            return HelperRouteResult.Error(response.Code, response.Message);
        }

        private static bool HasNonEmptyObject(JToken token)
        {
            var obj = token as JObject;
            return obj != null && obj.Properties().Any();
        }

        private static string JsonObjectString(JToken token)
        {
            return token == null || token.Type == JTokenType.Null ? null : JsonConvert.SerializeObject(token);
        }

        private static string NewTraceId()
        {
            return Guid.NewGuid().ToString("N");
        }

        private static string ShortReason(Exception ex)
        {
            return ex == null ? "unknown" : ex.GetType().Name;
        }
    }

    public static class HelperConfig
    {
        public static readonly TimeSpan DefaultIdleTimeout = TimeSpan.FromMinutes(30);

        public static TimeSpan LoadIdleTimeout(string configPath)
        {
            try
            {
                if (string.IsNullOrWhiteSpace(configPath) || !File.Exists(configPath))
                {
                    return DefaultIdleTimeout;
                }

                var json = File.ReadAllText(configPath);
                var document = JObject.Parse(json);
                var token = document["idle_timeout_minutes"];
                if (token == null || token.Type != JTokenType.Integer)
                {
                    return DefaultIdleTimeout;
                }

                var minutes = token.Value<int>();
                if (minutes < 1 || minutes > 1440)
                {
                    return DefaultIdleTimeout;
                }

                return TimeSpan.FromMinutes(minutes);
            }
            catch (Exception)
            {
                return DefaultIdleTimeout;
            }
        }
    }

    public interface IErrorWriter
    {
        void Write(string code, string message);
    }

    public sealed class ConsoleErrorWriter : IErrorWriter
    {
        public void Write(string code, string message)
        {
            Console.Error.WriteLine(code + ": " + message);
        }
    }

    public interface IHelperHostRunner
    {
        Task RunAsync(
            int port,
            HelperSessionDocument session,
            TimeSpan idleTimeout,
            string localToken,
            HelperSecurityOptions securityOptions,
            IClock clock,
            IDelay delay,
            CancellationToken cancellationToken);
    }

    public sealed class KestrelHelperHostRunner : IHelperHostRunner
    {
        public async Task RunAsync(
            int port,
            HelperSessionDocument session,
            TimeSpan idleTimeout,
            string localToken,
            HelperSecurityOptions securityOptions,
            IClock clock,
            IDelay delay,
            CancellationToken cancellationToken)
        {
            var builder = WebApplication.CreateBuilder(new string[0]);
            builder.WebHost.ConfigureKestrel(options =>
            {
                options.Listen(IPAddress.Parse(PortAllocator.LoopbackHost), port);
            });

            var app = builder.Build();
            var idle = new IdleTracker(idleTimeout, clock.UtcNow);
            var security = new HelperSecurityGate(
                localToken,
                securityOptions,
                new WindowsTcpOriginProcessResolver(new DefaultProcessInspector()));
            var configStore = new JsonHelperSessionConfigStore(Path.Combine(Paths.RootDirectory, "config.json"));
            var bearerStore = new DpapiPlmBearerTokenStore(Path.Combine(Paths.RootDirectory, "plm-bearer-token.bin"));
            var httpClient = new HttpClient();
            var auditStore = new SqliteAuditEventStore(Path.Combine(Paths.RootDirectory, "audit.db"));
            var sessionService = new HelperSessionService(
                configStore,
                bearerStore,
                new HttpPlmLoginClient(httpClient),
                new CurrentDrawingStore());
            var businessAuditService = new HelperBusinessAuditService(
                configStore,
                bearerStore,
                new HttpPlmBusinessClient(httpClient),
                new PullCache(),
                auditStore,
                clock,
                new ConsoleAuditWarningWriter());

            app.Use(async (context, next) =>
            {
                idle.Touch(clock.UtcNow);
                var decision = security.Authorize(new HelperSecurityRequest
                {
                    Method = context.Request.Method,
                    Path = context.Request.Path.Value,
                    LocalTokenHeaders = context.Request.Headers["X-Yuantus-Local-Token"].ToArray(),
                    ProtocolHeaders = context.Request.Headers["X-Yuantus-Protocol"].ToArray(),
                    Connection = new OriginConnection
                    {
                        LocalAddress = context.Connection.LocalIpAddress,
                        LocalPort = context.Connection.LocalPort,
                        RemoteAddress = context.Connection.RemoteIpAddress,
                        RemotePort = context.Connection.RemotePort
                    }
                });
                if (!decision.Allowed)
                {
                    await HelperSecurityResponse.WriteAsync(context, decision, cancellationToken).ConfigureAwait(false);
                    return;
                }
                await next().ConfigureAwait(false);
            });

            app.MapGet("/healthz", async context =>
            {
                idle.Touch(clock.UtcNow);
                context.Response.ContentType = "application/json; charset=utf-8";
                await context.Response.WriteAsync("{\"ok\":true}", cancellationToken).ConfigureAwait(false);
            });

            app.MapGet("/version", async context =>
            {
                idle.Touch(clock.UtcNow);
                await HelperRouteResponse
                    .WriteAsync(context, HelperRouteResult.Success(HelperVersionResponse.Current()), cancellationToken)
                    .ConfigureAwait(false);
            });

            app.MapPost("/session/login", async context =>
            {
                idle.Touch(clock.UtcNow);
                var started = clock.UtcNow;
                var request = await ReadJsonAsync<SessionLoginRequest>(context).ConfigureAwait(false);
                var result = await sessionService.LoginAsync(request, cancellationToken).ConfigureAwait(false);
                businessAuditService.AuditSessionResult("/session/login", result, started);
                await HelperRouteResponse
                    .WriteAsync(context, result, cancellationToken)
                    .ConfigureAwait(false);
            });

            app.MapPost("/session/logout", async context =>
            {
                idle.Touch(clock.UtcNow);
                var started = clock.UtcNow;
                var result = sessionService.Logout();
                businessAuditService.AuditSessionResult("/session/logout", result, started);
                await HelperRouteResponse
                    .WriteAsync(context, result, cancellationToken)
                    .ConfigureAwait(false);
            });

            app.MapGet("/session/status", async context =>
            {
                idle.Touch(clock.UtcNow);
                await HelperRouteResponse
                    .WriteAsync(context, sessionService.Status(), cancellationToken)
                    .ConfigureAwait(false);
            });

            app.MapPost("/cad/current-drawing", async context =>
            {
                idle.Touch(clock.UtcNow);
                var request = await ReadJsonAsync<CurrentDrawingRequest>(context).ConfigureAwait(false);
                await HelperRouteResponse
                    .WriteAsync(context, sessionService.SetCurrentDrawing(request), cancellationToken)
                    .ConfigureAwait(false);
            });

            app.MapPost("/diff/preview", async context =>
            {
                idle.Touch(clock.UtcNow);
                var request = await ReadJsonAsync<JObject>(context).ConfigureAwait(false);
                await HelperRouteResponse
                    .WriteAsync(context, await businessAuditService.DiffPreviewAsync(request, cancellationToken).ConfigureAwait(false), cancellationToken)
                    .ConfigureAwait(false);
            });

            app.MapPost("/sync/inbound", async context =>
            {
                idle.Touch(clock.UtcNow);
                var request = await ReadJsonAsync<JObject>(context).ConfigureAwait(false);
                await HelperRouteResponse
                    .WriteAsync(context, await businessAuditService.SyncInboundAsync(request, cancellationToken).ConfigureAwait(false), cancellationToken)
                    .ConfigureAwait(false);
            });

            app.MapPost("/sync/outbound", async context =>
            {
                idle.Touch(clock.UtcNow);
                var request = await ReadJsonAsync<JObject>(context).ConfigureAwait(false);
                await HelperRouteResponse
                    .WriteAsync(context, await businessAuditService.SyncOutboundAsync(request, cancellationToken).ConfigureAwait(false), cancellationToken)
                    .ConfigureAwait(false);
            });

            app.MapPost("/audit/apply-result", async context =>
            {
                idle.Touch(clock.UtcNow);
                var request = await ReadJsonAsync<JObject>(context).ConfigureAwait(false);
                await HelperRouteResponse
                    .WriteAsync(context, businessAuditService.ApplyResult(request), cancellationToken)
                    .ConfigureAwait(false);
            });

            using (var idleCancellation = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken))
            {
                var idleTask = Task.Run(async () =>
                {
                    while (!idleCancellation.Token.IsCancellationRequested)
                    {
                        await delay.DelayAsync(TimeSpan.FromSeconds(1), idleCancellation.Token).ConfigureAwait(false);
                        if (idle.IsExpired(clock.UtcNow))
                        {
                            await app.StopAsync(CancellationToken.None).ConfigureAwait(false);
                            break;
                        }
                    }
                }, idleCancellation.Token);

                try
                {
                    await app.StartAsync(cancellationToken).ConfigureAwait(false);
                    await app.WaitForShutdownAsync(cancellationToken).ConfigureAwait(false);
                }
                catch (OperationCanceledException)
                {
                    if (!cancellationToken.IsCancellationRequested)
                    {
                        throw;
                    }
                    await app.StopAsync(CancellationToken.None).ConfigureAwait(false);
                }
                finally
                {
                    idleCancellation.Cancel();
                    try
                    {
                        await idleTask.ConfigureAwait(false);
                    }
                    catch (OperationCanceledException)
                    {
                    }
                }
            }
        }

        private static async Task<T> ReadJsonAsync<T>(HttpContext context)
        {
            using (var reader = new StreamReader(context.Request.Body, Encoding.UTF8))
            {
                var body = await reader.ReadToEndAsync().ConfigureAwait(false);
                if (string.IsNullOrWhiteSpace(body))
                {
                    return default(T);
                }
                return JsonConvert.DeserializeObject<T>(body);
            }
        }
    }
}
