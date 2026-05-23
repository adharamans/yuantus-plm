using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Xunit;
using Yuantus.Cad.Shared.Discovery;
using Yuantus.Cad.Shared.Identity;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Helper.Tests
{
    public sealed class HelperStartupContractTests
    {
        [Fact]
        public void test_helper_binds_loopback_only_and_allocates_first_free_port()
        {
            var binder = new RecordingPortBinder(new[] { 7959 });
            var allocator = new PortAllocator(binder);

            var binding = allocator.Allocate();

            Assert.Equal("127.0.0.1", binding.Host);
            Assert.Equal(7960, binding.Port);
            Assert.Equal(new[] { "127.0.0.1:7959", "127.0.0.1:7960" }, binder.Attempts.ToArray());
        }

        [Fact]
        public void test_helper_never_binds_wildcard_or_random_port()
        {
            var sources = ReadHelperSources();

            Assert.DoesNotContain("0.0.0.0", sources);
            Assert.DoesNotContain("localhost", sources);
            Assert.DoesNotContain("::1", sources);
            Assert.DoesNotContain("ListenAnyIP", sources);
            Assert.DoesNotContain(":0", sources);
            Assert.Contains("IPAddress.Parse(PortAllocator.LoopbackHost)", sources);
        }

        [Fact]
        public void test_healthz_is_bare_and_returns_expected_json_body()
        {
            var sources = ReadHelperSources();

            Assert.Contains("MapGet(\"/healthz\"", sources);
            Assert.Contains("{\\\"ok\\\":true}", sources);
            Assert.DoesNotContain("UseCors", sources);
        }

        [Fact]
        public async Task test_healthz_is_not_healthy_before_token_bootstrap_and_session_publish()
        {
            var sessionStore = new FakeSessionFileStore();
            var host = new RecordingHostRunner();
            var tokenStore = new FakeTokenStore { ThrowOnWrite = true };
            var runtime = CreateRuntime(sessionStore, host, tokenStore);

            var exitCode = await HelperCommand.RunAsync(new string[0], runtime, CancellationToken.None);

            Assert.Equal(1, exitCode);
            Assert.Null(sessionStore.Current);
            Assert.Equal(0, host.RunCalls);
        }

        [Fact]
        public void test_session_file_schema_matches_shared_helper_session_file()
        {
            using (var temp = TempWorkspace.Create())
            {
                var paths = new TestHelperPaths(temp.Root);
                var store = new JsonSessionFileStore(paths);
                var session = HelperSessionDocument.Create(paths, 7959, DateTimeOffset.Parse("2026-05-21T10:00:00-07:00"));

                store.Publish(session);

                var raw = File.ReadAllText(paths.HelperSessionFilePath);
                var json = JObject.Parse(raw);
                Assert.Equal("1.0", json.Value<string>("schema_version"));
                Assert.Equal(SessionContext.CurrentSessionId, json.Value<int>("session_id"));
                Assert.Equal(7959, json.Value<int>("port"));
                Assert.True(json.Value<int>("pid") > 0);
                Assert.Equal(paths.HelperExePath, json.Value<string>("image_path"));
                Assert.Equal(Paths.ProtocolVersion, json.Value<string>("protocol_version"));
                Assert.Equal("0.1.0", json.Value<string>("helper_version"));
                Assert.Equal("http://127.0.0.1:7959", json.Value<string>("endpoints_base"));

                var shared = JsonConvert.DeserializeObject<HelperSessionFile>(raw);
                Assert.Equal(session.Port, shared.Port);
                Assert.Equal(session.ImagePath, shared.ImagePath);
            }
        }

        [Fact]
        public void test_session_file_uses_current_session_id_filename()
        {
            using (var temp = TempWorkspace.Create())
            {
                var paths = new TestHelperPaths(temp.Root);

                Assert.EndsWith(
                    string.Format("helper-session-{0}.json", SessionContext.CurrentSessionId),
                    paths.HelperSessionFilePath);
            }
        }

        [Fact]
        public void test_session_file_publish_is_atomic_no_partial_final_file()
        {
            using (var temp = TempWorkspace.Create())
            {
                var paths = new TestHelperPaths(temp.Root);
                var store = new JsonSessionFileStore(paths);

                store.Publish(HelperSessionDocument.Create(paths, 7961, DateTimeOffset.UtcNow));

                Assert.True(File.Exists(paths.HelperSessionFilePath));
                Assert.NotNull(JObject.Parse(File.ReadAllText(paths.HelperSessionFilePath)));
                Assert.Empty(Directory.GetFiles(paths.RootDirectory, "*.tmp"));
            }
        }

        [Fact]
        public void test_normal_shutdown_deletes_current_session_file()
        {
            using (var temp = TempWorkspace.Create())
            {
                var paths = new TestHelperPaths(temp.Root);
                var store = new JsonSessionFileStore(paths);
                store.Publish(HelperSessionDocument.Create(paths, 7962, DateTimeOffset.UtcNow));

                store.DeleteCurrent();

                Assert.False(File.Exists(paths.HelperSessionFilePath));
            }
        }

        [Fact]
        public void test_bootstrap_creates_64_char_lowercase_hex_local_token()
        {
            var store = new FakeTokenStore();
            var bootstrapper = new LocalTokenBootstrapper(store, new DeterministicRandomBytes());

            var token = bootstrapper.EnsureToken();

            Assert.Equal("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f", token);
            Assert.Equal(64, token.Length);
            Assert.Equal(token.ToLowerInvariant(), token);
            Assert.Equal(token, store.WrittenToken);
        }

        [Fact]
        public void test_existing_local_token_is_reused_not_overwritten()
        {
            var store = new FakeTokenStore { ExistingToken = "abc123" };
            var bootstrapper = new LocalTokenBootstrapper(store, new DeterministicRandomBytes());

            var token = bootstrapper.EnsureToken();

            Assert.Equal("abc123", token);
            Assert.Equal(0, store.WriteCalls);
        }

        [Fact]
        public async Task test_bootstrap_failure_exits_without_publishing_session_file()
        {
            var sessionStore = new FakeSessionFileStore();
            var host = new RecordingHostRunner();
            var tokenStore = new FakeTokenStore { ThrowOnWrite = true };
            var runtime = CreateRuntime(sessionStore, host, tokenStore);

            var exitCode = await HelperCommand.RunAsync(new string[0], runtime, CancellationToken.None);

            Assert.Equal(1, exitCode);
            Assert.Null(sessionStore.Current);
            Assert.Equal(0, host.RunCalls);
            Assert.Contains(ErrorCodes.HelperLocalTokenBootstrapFailed, runtimeError(runtime).Messages.Single());
        }

        [Fact]
        public async Task test_pre_mutex_startup_failure_does_not_delete_existing_session_file()
        {
            var sessionStore = new FakeSessionFileStore
            {
                Current = new HelperSessionDocument { Port = 7959, Pid = 123, ImagePath = "existing-helper.exe" }
            };
            var host = new RecordingHostRunner();
            var runtime = new HelperRuntime(
                new TestHelperPaths(Path.Combine(Path.GetTempPath(), "yuantus-cad-helper-tests-" + Guid.NewGuid().ToString("N"))),
                new ThrowingInstallIdProvider(),
                new FakeTokenStore(),
                new DeterministicRandomBytes(),
                new FakePortAllocator(7959),
                sessionStore,
                new FakeMutexFactory(true),
                new FakeHealthProbe(false),
                new FakeProcessInspector(false, null),
                new RecordingDelay(),
                new FakeClock(DateTimeOffset.Parse("2026-05-21T10:00:00Z")),
                new RecordingErrorWriter(),
                host);

            var exitCode = await HelperCommand.RunAsync(new string[0], runtime, CancellationToken.None);

            Assert.Equal(1, exitCode);
            Assert.NotNull(sessionStore.Current);
            Assert.Equal(0, sessionStore.DeleteCalls);
            Assert.Equal(0, host.RunCalls);
        }

        [Fact]
        public void test_mutex_name_uses_shared_install_id_and_local_namespace()
        {
            var installId = Guid.Parse("550e8400-e29b-41d4-a716-446655440000");

            Assert.Equal(
                "Local\\YuantusCadHelper-550e8400-e29b-41d4-a716-446655440000",
                SingleInstanceCoordinator.MutexName(installId));
        }

        [Fact]
        public async Task test_second_instance_healthy_helper_exits_zero()
        {
            var sessions = new FakeSessionFileStore
            {
                Current = new HelperSessionDocument { Port = 7959, Pid = 123, ImagePath = "helper.exe" }
            };
            var coordinator = new SingleInstanceCoordinator(
                new FakeMutexFactory(false),
                sessions,
                new FakeHealthProbe(true),
                new FakeProcessInspector(false, null),
                new RecordingDelay());

            var decision = await coordinator.ResolveAsync("Local\\YuantusCadHelper-x", CancellationToken.None);

            Assert.Equal(SingleInstanceDecisionKind.ExistingHealthy, decision.Kind);
            Assert.Equal(0, sessions.DeleteCalls);
        }

        [Fact]
        public async Task test_missing_session_file_retries_then_singleton_lost()
        {
            var delay = new RecordingDelay();
            var coordinator = new SingleInstanceCoordinator(
                new FakeMutexFactory(false, false, false),
                new FakeSessionFileStore(),
                new FakeHealthProbe(false),
                new FakeProcessInspector(false, null),
                delay);

            var decision = await coordinator.ResolveAsync("Local\\YuantusCadHelper-x", CancellationToken.None);

            Assert.Equal(SingleInstanceDecisionKind.Failed, decision.Kind);
            Assert.Equal(ErrorCodes.HelperSingletonLost, decision.ErrorCode);
            Assert.Equal(2, delay.Calls);
        }

        [Fact]
        public async Task test_dead_pid_session_file_is_deleted_and_startup_retries()
        {
            var sessions = new FakeSessionFileStore
            {
                Current = new HelperSessionDocument { Port = 7959, Pid = 123, ImagePath = "helper.exe" }
            };
            var coordinator = new SingleInstanceCoordinator(
                new FakeMutexFactory(false, true),
                sessions,
                new FakeHealthProbe(false),
                new FakeProcessInspector(false, null),
                new RecordingDelay());

            var decision = await coordinator.ResolveAsync("Local\\YuantusCadHelper-x", CancellationToken.None);

            Assert.Equal(SingleInstanceDecisionKind.Start, decision.Kind);
            Assert.Equal(1, sessions.DeleteCalls);
        }

        [Fact]
        public async Task test_pid_reuse_image_path_mismatch_is_deleted_and_startup_retries()
        {
            var sessions = new FakeSessionFileStore
            {
                Current = new HelperSessionDocument { Port = 7959, Pid = 123, ImagePath = @"C:\helper.exe" }
            };
            var coordinator = new SingleInstanceCoordinator(
                new FakeMutexFactory(false, true),
                sessions,
                new FakeHealthProbe(false),
                new FakeProcessInspector(true, @"C:\other.exe"),
                new RecordingDelay());

            var decision = await coordinator.ResolveAsync("Local\\YuantusCadHelper-x", CancellationToken.None);

            Assert.Equal(SingleInstanceDecisionKind.Start, decision.Kind);
            Assert.Equal(1, sessions.DeleteCalls);
        }

        [Fact]
        public async Task test_unhealthy_matching_process_does_not_delete_session_file()
        {
            var sessions = new FakeSessionFileStore
            {
                Current = new HelperSessionDocument { Port = 7959, Pid = 123, ImagePath = @"C:\helper.exe" }
            };
            var coordinator = new SingleInstanceCoordinator(
                new FakeMutexFactory(false),
                sessions,
                new FakeHealthProbe(false),
                new FakeProcessInspector(true, @"C:\helper.exe"),
                new RecordingDelay());

            var decision = await coordinator.ResolveAsync("Local\\YuantusCadHelper-x", CancellationToken.None);

            Assert.Equal(SingleInstanceDecisionKind.Failed, decision.Kind);
            Assert.Equal(ErrorCodes.HelperUnhealthy, decision.ErrorCode);
            Assert.Equal(0, sessions.DeleteCalls);
        }

        [Fact]
        public void test_idle_timeout_stops_helper_and_deletes_session_file()
        {
            using (var temp = TempWorkspace.Create())
            {
                var paths = new TestHelperPaths(temp.Root);
                var store = new JsonSessionFileStore(paths);
                var tracker = new IdleTracker(TimeSpan.FromMinutes(30), DateTimeOffset.Parse("2026-05-21T10:00:00Z"));
                store.Publish(HelperSessionDocument.Create(paths, 7963, DateTimeOffset.UtcNow));

                if (tracker.IsExpired(DateTimeOffset.Parse("2026-05-21T10:30:00Z")))
                {
                    store.DeleteCurrent();
                }

                Assert.False(File.Exists(paths.HelperSessionFilePath));
            }
        }

        [Fact]
        public void test_config_idle_timeout_override_accepts_only_1_to_1440_minutes()
        {
            using (var temp = TempWorkspace.Create())
            {
                var paths = new TestHelperPaths(temp.Root);
                Directory.CreateDirectory(paths.RootDirectory);
                File.WriteAllText(paths.ConfigFilePath, "{\"idle_timeout_minutes\":5}");
                Assert.Equal(TimeSpan.FromMinutes(5), HelperConfig.LoadIdleTimeout(paths.ConfigFilePath));

                File.WriteAllText(paths.ConfigFilePath, "{\"idle_timeout_minutes\":0}");
                Assert.Equal(TimeSpan.FromMinutes(30), HelperConfig.LoadIdleTimeout(paths.ConfigFilePath));

                File.WriteAllText(paths.ConfigFilePath, "{\"idle_timeout_minutes\":1441}");
                Assert.Equal(TimeSpan.FromMinutes(30), HelperConfig.LoadIdleTimeout(paths.ConfigFilePath));

                File.WriteAllText(paths.ConfigFilePath, "{\"idle_timeout_minutes\":\"5\"}");
                Assert.Equal(TimeSpan.FromMinutes(30), HelperConfig.LoadIdleTimeout(paths.ConfigFilePath));
            }
        }

        [Fact]
        public void test_no_s7_s8_scope_leak_after_s6_business_audit_routes()
        {
            var sources = ReadHelperSources();

            Assert.Contains("/diff/preview", sources);
            Assert.Contains("/sync/inbound", sources);
            Assert.Contains("/sync/outbound", sources);
            Assert.Contains("/audit/apply-result", sources);
            Assert.DoesNotContain("/dedup/check", sources);
            Assert.DoesNotContain("/shell/notify", sources);
            Assert.DoesNotContain("--reset-local-token", sources);
            Assert.DoesNotContain("CADDedupPlugin", sources);
        }

        [Fact]
        public void test_dotnet_workflow_covers_helper_paths_build_and_tests()
        {
            var workflow = File.ReadAllText(Path.Combine(FindRepoRoot(), ".github", "workflows", "cad-helper-shared-dotnet.yml"));

            Assert.Contains("clients/cad-desktop-helper/Helper/**", workflow);
            Assert.Contains("clients/cad-desktop-helper/Helper.Tests/**", workflow);
            Assert.Contains("dotnet restore clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj", workflow);
            Assert.Contains("dotnet build clients/cad-desktop-helper/Helper/Yuantus.Cad.Helper.csproj", workflow);
            Assert.Contains("dotnet test clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj", workflow);
        }

        private static HelperRuntime CreateRuntime(
            FakeSessionFileStore sessions,
            RecordingHostRunner host,
            FakeTokenStore tokenStore)
        {
            var paths = new TestHelperPaths(Path.Combine(Path.GetTempPath(), "yuantus-cad-helper-tests-" + Guid.NewGuid().ToString("N")));
            var error = new RecordingErrorWriter();
            return new HelperRuntime(
                paths,
                new FakeInstallIdProvider(Guid.Parse("550e8400-e29b-41d4-a716-446655440000")),
                tokenStore,
                new DeterministicRandomBytes(),
                new FakePortAllocator(7959),
                sessions,
                new FakeMutexFactory(true),
                new FakeHealthProbe(false),
                new FakeProcessInspector(false, null),
                new RecordingDelay(),
                new FakeClock(DateTimeOffset.Parse("2026-05-21T10:00:00Z")),
                error,
                host);
        }

        private static RecordingErrorWriter runtimeError(HelperRuntime runtime)
        {
            return (RecordingErrorWriter)runtime.ErrorWriter;
        }

        private static string ReadHelperSources()
        {
            var helperDir = Path.Combine(FindRepoRoot(), "clients", "cad-desktop-helper", "Helper");
            return string.Join(
                "\n",
                Directory.GetFiles(helperDir, "*.cs", SearchOption.AllDirectories)
                    .Where(path => !path.Contains(Path.DirectorySeparatorChar + "bin" + Path.DirectorySeparatorChar.ToString()))
                    .Where(path => !path.Contains(Path.DirectorySeparatorChar + "obj" + Path.DirectorySeparatorChar.ToString()))
                    .OrderBy(path => path)
                    .Select(File.ReadAllText));
        }

        private static string FindRepoRoot()
        {
            var directory = new DirectoryInfo(AppContext.BaseDirectory);
            while (directory != null)
            {
                if (Directory.Exists(Path.Combine(directory.FullName, ".git")) &&
                    Directory.Exists(Path.Combine(directory.FullName, "clients", "cad-desktop-helper")))
                {
                    return directory.FullName;
                }
                directory = directory.Parent;
            }
            throw new DirectoryNotFoundException("Unable to locate repository root from " + AppContext.BaseDirectory);
        }

        private sealed class TempWorkspace : IDisposable
        {
            private TempWorkspace(string root)
            {
                Root = root;
                Directory.CreateDirectory(root);
            }

            public string Root { get; private set; }

            public static TempWorkspace Create()
            {
                return new TempWorkspace(Path.Combine(Path.GetTempPath(), "yuantus-cad-helper-tests-" + Guid.NewGuid().ToString("N")));
            }

            public void Dispose()
            {
                try
                {
                    if (Directory.Exists(Root))
                    {
                        Directory.Delete(Root, true);
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

        private sealed class TestHelperPaths : IHelperPaths
        {
            public TestHelperPaths(string root)
            {
                RootDirectory = root;
            }

            public string RootDirectory { get; private set; }

            public string HelperSessionFilePath
            {
                get { return Path.Combine(RootDirectory, string.Format("helper-session-{0}.json", SessionContext.CurrentSessionId)); }
            }

            public string HelperExePath
            {
                get { return Path.Combine(RootDirectory, "helper", "yuantus-cad-helper.exe"); }
            }

            public string ConfigFilePath
            {
                get { return Path.Combine(RootDirectory, "config.json"); }
            }
        }

        private sealed class RecordingPortBinder : IPortBinder
        {
            private readonly HashSet<int> _busyPorts;

            public RecordingPortBinder(IEnumerable<int> busyPorts)
            {
                _busyPorts = new HashSet<int>(busyPorts);
                Attempts = new List<string>();
            }

            public List<string> Attempts { get; private set; }

            public bool CanBind(string host, int port)
            {
                Attempts.Add(host + ":" + port);
                return !_busyPorts.Contains(port);
            }
        }

        private sealed class FakeInstallIdProvider : IInstallIdProvider
        {
            private readonly Guid _installId;

            public FakeInstallIdProvider(Guid installId)
            {
                _installId = installId;
            }

            public Guid GetOrCreate()
            {
                return _installId;
            }
        }

        private sealed class ThrowingInstallIdProvider : IInstallIdProvider
        {
            public Guid GetOrCreate()
            {
                throw new HelperException(
                    ErrorCodes.HelperInstallIdUnavailable,
                    "Unable to read install id.",
                    false);
            }
        }

        private sealed class FakeTokenStore : ILocalTokenStore
        {
            public string ExistingToken { get; set; }
            public bool ThrowOnWrite { get; set; }
            public int WriteCalls { get; private set; }
            public string WrittenToken { get; private set; }

            public string Read()
            {
                return ExistingToken;
            }

            public void Write(string hexToken)
            {
                WriteCalls++;
                if (ThrowOnWrite)
                {
                    throw new HelperException(
                        ErrorCodes.HelperLocalTokenBootstrapFailed,
                        "Unable to write local helper token.",
                        false);
                }
                WrittenToken = hexToken;
            }
        }

        private sealed class DeterministicRandomBytes : IRandomBytes
        {
            public byte[] GetBytes(int count)
            {
                var bytes = new byte[count];
                for (var i = 0; i < count; i++)
                {
                    bytes[i] = (byte)i;
                }
                return bytes;
            }
        }

        private sealed class FakePortAllocator : IPortAllocator
        {
            private readonly int _port;

            public FakePortAllocator(int port)
            {
                _port = port;
            }

            public PortBinding Allocate()
            {
                return new PortBinding("127.0.0.1", _port);
            }
        }

        private sealed class FakeSessionFileStore : ISessionFileStore
        {
            public HelperSessionDocument Current { get; set; }
            public int DeleteCalls { get; private set; }
            public int PublishCalls { get; private set; }

            public HelperSessionDocument Read()
            {
                return Current;
            }

            public void Publish(HelperSessionDocument session)
            {
                PublishCalls++;
                Current = session;
            }

            public void DeleteCurrent()
            {
                DeleteCalls++;
                Current = null;
            }
        }

        private sealed class FakeMutexFactory : INamedMutexFactory
        {
            private readonly Queue<bool> _acquired;

            public FakeMutexFactory(params bool[] acquired)
            {
                _acquired = new Queue<bool>(acquired);
            }

            public INamedMutexLease TryAcquire(string name)
            {
                if (_acquired.Count == 0)
                {
                    return new FakeMutexLease(false);
                }
                return new FakeMutexLease(_acquired.Dequeue());
            }
        }

        private sealed class FakeMutexLease : INamedMutexLease
        {
            public FakeMutexLease(bool acquired)
            {
                Acquired = acquired;
            }

            public bool Acquired { get; private set; }

            public void Dispose()
            {
            }
        }

        private sealed class FakeHealthProbe : IHealthProbe
        {
            private readonly bool _healthy;

            public FakeHealthProbe(bool healthy)
            {
                _healthy = healthy;
            }

            public Task<bool> IsHealthyAsync(int port, CancellationToken cancellationToken)
            {
                return Task.FromResult(_healthy);
            }
        }

        private sealed class FakeProcessInspector : IProcessInspector
        {
            private readonly bool _exists;
            private readonly string _imagePath;

            public FakeProcessInspector(bool exists, string imagePath)
            {
                _exists = exists;
                _imagePath = imagePath;
            }

            public ProcessSnapshot Inspect(int pid)
            {
                return new ProcessSnapshot(_exists, _imagePath);
            }
        }

        private sealed class RecordingDelay : IDelay
        {
            public int Calls { get; private set; }

            public Task DelayAsync(TimeSpan delay, CancellationToken cancellationToken)
            {
                Calls++;
                return Task.CompletedTask;
            }
        }

        private sealed class FakeClock : IClock
        {
            public FakeClock(DateTimeOffset utcNow)
            {
                UtcNow = utcNow;
            }

            public DateTimeOffset UtcNow { get; private set; }
        }

        private sealed class RecordingErrorWriter : IErrorWriter
        {
            public RecordingErrorWriter()
            {
                Messages = new List<string>();
            }

            public List<string> Messages { get; private set; }

            public void Write(string code, string message)
            {
                Messages.Add(code + ": " + message);
            }
        }

        private sealed class RecordingHostRunner : IHelperHostRunner
        {
            public int RunCalls { get; private set; }

            public Task RunAsync(
                int port,
                HelperSessionDocument session,
                TimeSpan idleTimeout,
                string localToken,
                HelperSecurityOptions securityOptions,
                IClock clock,
                IDelay delay,
                CancellationToken cancellationToken)
            {
                RunCalls++;
                LocalToken = localToken;
                SecurityOptions = securityOptions;
                return Task.CompletedTask;
            }

            public string LocalToken { get; private set; }
            public HelperSecurityOptions SecurityOptions { get; private set; }
        }
    }
}
