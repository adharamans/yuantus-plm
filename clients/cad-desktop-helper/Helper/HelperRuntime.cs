using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
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
                bootstrapper.EnsureToken();

                var port = runtime.PortAllocator.Allocate().Port;
                var session = HelperSessionDocument.Create(runtime.Paths, port, runtime.Clock.UtcNow);
                runtime.SessionFiles.Publish(session);

                var idleTimeout = HelperConfig.LoadIdleTimeout(runtime.Paths.ConfigFilePath);
                try
                {
                    await runtime.HostRunner
                        .RunAsync(port, session, idleTimeout, runtime.Clock, runtime.Delay, cancellationToken)
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

            app.Use(async (context, next) =>
            {
                idle.Touch(clock.UtcNow);
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
