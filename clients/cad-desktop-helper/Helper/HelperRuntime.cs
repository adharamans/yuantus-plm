using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
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
    }
}
