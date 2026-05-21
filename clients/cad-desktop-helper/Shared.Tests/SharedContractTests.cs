using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Win32;
using Newtonsoft.Json;
using Xunit;
using Yuantus.Cad.Shared.Discovery;
using Yuantus.Cad.Shared.Identity;
using Yuantus.Cad.Shared.Registry;
using Yuantus.Cad.Shared.Security;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Shared.Tests
{
    public sealed class SharedContractTests
    {
        [Fact]
        public async Task test_install_id_atomic_create_race()
        {
            using (UseTempAppDataRoot(out var root))
            {
                var writes = 0;
                using (InstallId.ObserveCreatedForTesting(() => Interlocked.Increment(ref writes)))
                {
                    var first = Task.Run(() => InstallId.GetOrCreate());
                    var second = Task.Run(() => InstallId.GetOrCreate());

                    var results = await Task.WhenAll(first, second);

                    Assert.Equal(results[0], results[1]);
                    Assert.Equal(1, writes);
                    Assert.True(File.Exists(Paths.InstallIdFile));
                }
            }
        }

        [Fact]
        public async Task test_install_id_high_concurrency_race_converges()
        {
            using (UseTempAppDataRoot(out var root))
            {
                var writes = 0;
                using (InstallId.ObserveCreatedForTesting(() => Interlocked.Increment(ref writes)))
                {
                    var tasks = Enumerable.Range(0, 8)
                        .Select(_ => Task.Run(() => InstallId.GetOrCreate()))
                        .ToArray();

                    var results = await Task.WhenAll(tasks);

                    Assert.Single(results.Distinct());
                    Assert.Equal(1, writes);
                    Assert.True(File.Exists(Paths.InstallIdFile));
                }
            }
        }

        [Fact]
        public void test_install_id_create_race_retries_until_existing_file_is_readable()
        {
            using (UseTempAppDataRoot(out var root))
            {
                Directory.CreateDirectory(Paths.RootDirectory);
                var expected = Guid.NewGuid();
                File.WriteAllText(
                    Paths.InstallIdFile,
                    "{\"schema_version\":\"1.0\",\"install_id\":\"" + expected.ToString("D") + "\"}");

                var access = new TransientEmptyInstallIdFileAccess();
                using (InstallId.UseFileAccessForTesting(access))
                {
                    var actual = InstallId.GetOrCreate();

                    Assert.Equal(expected, actual);
                    Assert.True(access.LengthCalls >= 3);
                }
            }
        }

        [Fact]
        public void test_install_id_existing_file_is_read_not_overwritten()
        {
            using (UseTempAppDataRoot(out var root))
            {
                Directory.CreateDirectory(Paths.RootDirectory);
                var expected = Guid.NewGuid();
                var createdAt = "2026-05-20T00:00:00.0000000Z";
                File.WriteAllText(
                    Paths.InstallIdFile,
                    "{\"schema_version\":\"1.0\",\"install_id\":\"" + expected.ToString("D") +
                    "\",\"created_at\":\"" + createdAt + "\"}");

                var actual = InstallId.GetOrCreate();
                var raw = File.ReadAllText(Paths.InstallIdFile);

                Assert.Equal(expected, actual);
                Assert.Contains(createdAt, raw);
            }
        }

        [Fact]
        public void test_install_id_non_io_exception_throws_helper_install_id_unavailable()
        {
            using (UseTempAppDataRoot(out var root))
            using (InstallId.UseFileAccessForTesting(new ThrowingInstallIdFileAccess()))
            {
                var ex = Assert.Throws<HelperException>(() => InstallId.GetOrCreate());
                Assert.Equal(ErrorCodes.HelperInstallIdUnavailable, ex.Code);
            }
        }

        [Fact]
        public void test_install_id_parent_dir_auto_created()
        {
            using (UseTempAppDataRoot(out var root))
            {
                Assert.False(Directory.Exists(Paths.RootDirectory));

                var first = InstallId.GetOrCreate();
                var second = InstallId.GetOrCreate();

                Assert.Equal(first, second);
                Assert.True(Directory.Exists(Paths.RootDirectory));
                Assert.True(File.Exists(Paths.InstallIdFile));
            }
        }

        [Fact]
        public void test_install_id_empty_file_throws_helper_install_id_unavailable()
        {
            using (UseTempAppDataRoot(out var root))
            {
                Directory.CreateDirectory(Paths.RootDirectory);
                File.WriteAllBytes(Paths.InstallIdFile, new byte[0]);

                var ex = Assert.Throws<HelperException>(() => InstallId.GetOrCreate());

                Assert.Equal(ErrorCodes.HelperInstallIdUnavailable, ex.Code);
                Assert.Equal("empty", ex.Details["reason"]);
                Assert.Equal(0L, new FileInfo(Paths.InstallIdFile).Length);
            }
        }

        [Fact]
        public void test_install_id_malformed_json_throws_helper_install_id_unavailable()
        {
            using (UseTempAppDataRoot(out var root))
            {
                Directory.CreateDirectory(Paths.RootDirectory);
                File.WriteAllText(Paths.InstallIdFile, "not json at all");

                var ex = Assert.Throws<HelperException>(() => InstallId.GetOrCreate());

                Assert.Equal(ErrorCodes.HelperInstallIdUnavailable, ex.Code);
                Assert.Equal("malformed_json", ex.Details["reason"]);
                Assert.Equal("not json at all", File.ReadAllText(Paths.InstallIdFile));
            }
        }

        [Fact]
        public void test_install_id_missing_field_throws_helper_install_id_unavailable()
        {
            using (UseTempAppDataRoot(out var root))
            {
                Directory.CreateDirectory(Paths.RootDirectory);
                File.WriteAllText(Paths.InstallIdFile, "{\"schema_version\":\"1.0\"}");

                var ex = Assert.Throws<HelperException>(() => InstallId.GetOrCreate());

                Assert.Equal(ErrorCodes.HelperInstallIdUnavailable, ex.Code);
                Assert.Equal("missing_field", ex.Details["reason"]);
            }
        }

        [Fact]
        public void test_install_id_invalid_guid_throws_helper_install_id_unavailable()
        {
            using (UseTempAppDataRoot(out var root))
            {
                Directory.CreateDirectory(Paths.RootDirectory);
                File.WriteAllText(
                    Paths.InstallIdFile,
                    "{\"schema_version\":\"1.0\",\"install_id\":\"not-a-guid\"}");

                var ex = Assert.Throws<HelperException>(() => InstallId.GetOrCreate());

                Assert.Equal(ErrorCodes.HelperInstallIdUnavailable, ex.Code);
                Assert.Equal("invalid_guid", ex.Details["reason"]);
            }
        }

        [Fact]
        public void test_dpapi_local_token_round_trip()
        {
            using (UseTempAppDataRoot(out var root))
            {
                LocalTokenStore.WriteLocalToken("0123456789abcdef");

                Assert.Equal("0123456789abcdef", LocalTokenStore.ReadLocalToken());
            }
        }

        [Fact]
        public void test_dpapi_unavailable_throws_helper_dpapi_unavailable()
        {
            using (DpapiEnvelope.UseProtectorForTesting(new ThrowingDpapiProtector()))
            {
                var ex = Assert.Throws<HelperException>(() =>
                    DpapiEnvelope.Protect(new byte[] { 1 }, new byte[] { 2 }));

                Assert.Equal(ErrorCodes.HelperDpapiUnavailable, ex.Code);
            }
        }

        [Fact]
        public async Task test_helper_probe_bare_get_no_token_header_injected()
        {
            var handler = new RecordingHandler(_ => OkEnvelope(new { status = "ok" }));
            using (var probe = new HelperProbe(handler))
            {
                var result = await probe.HealthAsync("127.0.0.1", 7959, TimeSpan.FromMilliseconds(500), CancellationToken.None);

                Assert.True(result.IsHealthy);
                Assert.False(handler.LastRequest.Headers.Contains("X-Yuantus-Local-Token"));
            }
        }

        [Fact]
        public async Task test_helper_probe_rejects_plain_200_without_expected_health_body()
        {
            var handler = new RecordingHandler(_ => new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new StringContent("<html>not yuantus helper</html>", Encoding.UTF8, "text/html")
            });
            using (var probe = new HelperProbe(handler))
            {
                var result = await probe.HealthAsync("127.0.0.1", 7959, TimeSpan.FromMilliseconds(500), CancellationToken.None);

                Assert.False(result.IsHealthy);
                Assert.Equal(HttpStatusCode.OK, result.StatusCode);
                Assert.False(result.BodyAccepted);
            }
        }

        [Fact]
        public async Task test_helper_transport_injects_local_token_and_protocol_header()
        {
            var handler = new RecordingHandler(_ => OkEnvelope(new SamplePayload { Value = "ok" }));
            var transport = new HelperTransport(
                new Uri("http://127.0.0.1:7959"),
                new HttpClient(handler),
                () => "token-1");

            await transport.PostJsonAsync<SamplePayload>("/version", new { ping = true }, CancellationToken.None);

            Assert.True(handler.LastRequest.Headers.Contains("X-Yuantus-Local-Token"));
            Assert.Equal("token-1", handler.LastRequest.Headers.GetValues("X-Yuantus-Local-Token").Single());
            Assert.Equal(Paths.ProtocolVersion, handler.LastRequest.Headers.GetValues("X-Yuantus-Protocol").Single());
        }

        [Fact]
        public async Task test_helper_transport_unwraps_ok_false_envelope_to_typed_exception()
        {
            var handler = new RecordingHandler(_ => new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = JsonContent("{\"ok\":false,\"error\":{\"code\":\"PLM_VALIDATION_FAILED\",\"message\":\"bad\",\"retryable\":true}}")
            });
            var transport = new HelperTransport(
                new Uri("http://127.0.0.1:7959"),
                new HttpClient(handler),
                () => "token-1");

            var ex = await Assert.ThrowsAsync<HelperException>(() =>
                transport.PostJsonAsync<SamplePayload>("/diff/preview", new { }, CancellationToken.None));

            Assert.Equal(ErrorCodes.PlmValidationFailed, ex.Code);
            Assert.True(ex.Retryable);
        }

        [Fact]
        public async Task test_helper_transport_426_throws_proto_version_unsupported()
        {
            var handler = new RecordingHandler(_ => new HttpResponseMessage(HttpStatusCode.UpgradeRequired)
            {
                Content = JsonContent("{}")
            });
            var transport = new HelperTransport(
                new Uri("http://127.0.0.1:7959"),
                new HttpClient(handler),
                () => "token-1");

            var ex = await Assert.ThrowsAsync<HelperException>(() =>
                transport.GetAsync<SamplePayload>("/version", CancellationToken.None));

            Assert.Equal(ErrorCodes.ProtoVersionUnsupported, ex.Code);
        }

        [Fact]
        public async Task test_helper_transport_401_invalid_token_rereads_dpapi_once_and_retries()
        {
            var calls = 0;
            var handler = new QueueHandler(
                _ => new HttpResponseMessage(HttpStatusCode.Unauthorized)
                {
                    Content = JsonContent("{\"error\":{\"code\":\"AUTH_LOCAL_TOKEN_INVALID\",\"message\":\"bad\",\"retryable\":false}}")
                },
                _ => OkEnvelope(new SamplePayload { Value = "ok" }));
            var transport = new HelperTransport(
                new Uri("http://127.0.0.1:7959"),
                new HttpClient(handler),
                () =>
                {
                    calls++;
                    return "token-" + calls;
                });

            var result = await transport.GetAsync<SamplePayload>("/version", CancellationToken.None);

            Assert.Equal("ok", result.Value);
            Assert.Equal(2, calls);
            Assert.Equal(2, handler.SendCount);
        }

        [Fact]
        public void test_helper_spawner_uses_deterministic_path_no_args_service_mode()
        {
            ProcessStartInfo captured = null;
            using (HelperSpawner.UseStartProcessForTesting(startInfo =>
            {
                captured = startInfo;
                return Process.GetCurrentProcess();
            }))
            using (UseTempAppDataRoot(out var root))
            {
                HelperSpawner.Spawn();

                Assert.Equal(Paths.HelperExePath, captured.FileName);
                Assert.True(string.IsNullOrWhiteSpace(captured.Arguments));
                Assert.DoesNotContain("--reset-local-token", File.ReadAllText(FindSourceFile("HelperSpawner.cs")));
            }
        }

        [Fact]
        public async Task test_helper_locator_waits_up_to_5s_then_throws_on_unresponsive()
        {
            var locator = new HelperLocator(
                new HelperProbe(new RecordingHandler(_ => new HttpResponseMessage(HttpStatusCode.ServiceUnavailable))),
                () => null,
                () => Process.GetCurrentProcess(),
                TimeSpan.FromMilliseconds(80),
                TimeSpan.FromMilliseconds(10));
            var sw = Stopwatch.StartNew();

            var ex = await Assert.ThrowsAsync<HelperException>(() =>
                locator.EnsureHelperRunningAsync(CancellationToken.None));

            sw.Stop();
            Assert.Equal(ErrorCodes.HelperPortBusy, ex.Code);
            Assert.True(sw.ElapsedMilliseconds >= 60);
        }

        [Fact]
        public async Task test_helper_locator_returns_uri_when_probe_succeeds_first_try()
        {
            var spawnCalls = 0;
            var session = new HelperSessionFile
            {
                Port = 7959,
                EndpointsBase = "http://127.0.0.1:7959"
            };
            var locator = new HelperLocator(
                new HelperProbe(new RecordingHandler(_ => OkEnvelope(new { status = "ok" }))),
                () => session,
                () =>
                {
                    spawnCalls++;
                    return Process.GetCurrentProcess();
                },
                TimeSpan.FromMilliseconds(80),
                TimeSpan.FromMilliseconds(10));

            var uri = await locator.EnsureHelperRunningAsync(CancellationToken.None);

            Assert.Equal("http://127.0.0.1:7959/", uri.ToString());
            Assert.Equal(0, spawnCalls);
        }

        [Fact]
        public void test_helper_session_file_parses_full_schema()
        {
            using (UseTempAppDataRoot(out var root))
            {
                Directory.CreateDirectory(Paths.RootDirectory);
                File.WriteAllText(Paths.HelperSessionFilePath, @"{
  ""schema_version"": ""1.0"",
  ""session_id"": 2,
  ""port"": 7959,
  ""pid"": 1234,
  ""image_path"": ""C:\\Users\\frank\\AppData\\Roaming\\YuantusPLM\\helper\\yuantus-cad-helper.exe"",
  ""started_at"": ""2026-05-19T11:30:00+08:00"",
  ""protocol_version"": ""1.0"",
  ""helper_version"": ""0.1.0"",
  ""endpoints_base"": ""http://127.0.0.1:7959""
}");

                var parsed = HelperSessionFile.Read();

                Assert.Equal("1.0", parsed.SchemaVersion);
                Assert.Equal(2, parsed.SessionId);
                Assert.Equal(7959, parsed.Port);
                Assert.Equal(1234, parsed.Pid);
                Assert.Contains("yuantus-cad-helper.exe", parsed.ImagePath);
                Assert.Equal("1.0", parsed.ProtocolVersion);
                Assert.Equal("0.1.0", parsed.HelperVersion);
                Assert.Equal("http://127.0.0.1:7959", parsed.EndpointsBase);
                Assert.NotEqual(default(DateTimeOffset), parsed.StartedAt);
            }
        }

        [Fact]
        public void test_helper_session_file_partial_write_returns_null()
        {
            using (UseTempAppDataRoot(out var root))
            {
                Directory.CreateDirectory(Paths.RootDirectory);
                File.WriteAllText(Paths.HelperSessionFilePath, "{\"schema_version\":\"1.0\",");

                Assert.Null(HelperSessionFile.Read());
            }
        }

        [Fact]
        public void test_helper_session_file_path_includes_current_session_id()
        {
            using (UseTempAppDataRoot(out var root))
            {
                Assert.EndsWith(
                    string.Format("helper-session-{0}.json", SessionContext.CurrentSessionId),
                    HelperSessionFile.Path);
            }
        }

        [Fact]
        public void test_response_envelope_deserializes_ok_true_payload_data()
        {
            var envelope = JsonConvert.DeserializeObject<ResponseEnvelope<SamplePayload>>(
                "{\"ok\":true,\"data\":{\"value\":\"hello\"}}");

            Assert.True(envelope.Ok);
            Assert.Equal("hello", envelope.Data.Value);
        }

        [Fact]
        public void test_registry_abstraction_is_read_only_no_set_or_create_methods()
        {
            var publicMethods = typeof(HkcuRegistry).GetMethods()
                .Where(method => method.DeclaringType == typeof(HkcuRegistry))
                .Select(method => method.Name)
                .Concat(typeof(IRegistryKey).GetMethods().Select(method => method.Name))
                .ToArray();

            Assert.DoesNotContain(publicMethods, name => name.StartsWith("Set", StringComparison.OrdinalIgnoreCase));
            Assert.DoesNotContain(publicMethods, name => name.StartsWith("Create", StringComparison.OrdinalIgnoreCase));
            Assert.DoesNotContain(publicMethods, name => name.StartsWith("Delete", StringComparison.OrdinalIgnoreCase));
        }

        [Fact]
        public void test_registry_abstraction_defaults_to_registry64_view()
        {
            var backend = new RecordingRegistryBackend();
            using (HkcuRegistry.UseBackendForTesting(backend))
            {
                HkcuRegistry.OpenHklm(@"SOFTWARE\Autodesk\AutoCAD");

                Assert.Equal(RegistryHive.LocalMachine, backend.LastHive);
                Assert.Equal(RegistryView.Registry64, backend.LastView);
            }
        }

        [Fact]
        public void test_registry_abstraction_accepts_explicit_view_argument()
        {
            var backend = new RecordingRegistryBackend();
            using (HkcuRegistry.UseBackendForTesting(backend))
            {
                HkcuRegistry.OpenHklm(@"SOFTWARE\Autodesk\AutoCAD", RegistryView.Registry32);

                Assert.Equal(RegistryHive.LocalMachine, backend.LastHive);
                Assert.Equal(RegistryView.Registry32, backend.LastView);
            }
        }

        private static IDisposable UseTempAppDataRoot(out string root)
        {
            root = Path.Combine(Path.GetTempPath(), "yuantus-cad-shared-tests-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(root);
            return Paths.UseAppDataRootForTesting(root);
        }

        private static StringContent JsonContent(string json)
        {
            return new StringContent(json, Encoding.UTF8, "application/json");
        }

        private static HttpResponseMessage OkEnvelope(object data)
        {
            var json = JsonConvert.SerializeObject(new { ok = true, data = data });
            return new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = JsonContent(json)
            };
        }

        private static string FindSourceFile(string fileName)
        {
            var dir = new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory);
            while (dir != null)
            {
                var candidate = Path.Combine(dir.FullName, "Discovery", fileName);
                if (File.Exists(candidate))
                {
                    return candidate;
                }
                candidate = Path.Combine(dir.FullName, "clients", "cad-desktop-helper", "Shared", "Discovery", fileName);
                if (File.Exists(candidate))
                {
                    return candidate;
                }
                dir = dir.Parent;
            }
            throw new FileNotFoundException(fileName);
        }

        private sealed class SamplePayload
        {
            [JsonProperty("value")]
            public string Value { get; set; }
        }

        private sealed class ThrowingInstallIdFileAccess : InstallIdFileAccess
        {
            public override void CreateDirectory(string path)
            {
            }

            public override Stream CreateNew(string path)
            {
                throw new UnauthorizedAccessException("denied");
            }
        }

        private sealed class TransientEmptyInstallIdFileAccess : InstallIdFileAccess
        {
            public int LengthCalls { get; private set; }

            public override Stream CreateNew(string path)
            {
                throw new IOException("already exists");
            }

            public override long Length(string path)
            {
                LengthCalls++;
                if (LengthCalls <= 2)
                {
                    return 0;
                }
                return base.Length(path);
            }
        }

        private sealed class ThrowingDpapiProtector : IDpapiProtector
        {
            public byte[] Protect(byte[] data, byte[] entropy)
            {
                throw new InvalidOperationException("dpapi unavailable");
            }

            public byte[] Unprotect(byte[] encrypted, byte[] entropy)
            {
                throw new InvalidOperationException("dpapi unavailable");
            }
        }

        private sealed class RecordingHandler : HttpMessageHandler
        {
            private readonly Func<HttpRequestMessage, HttpResponseMessage> _handler;

            public RecordingHandler(Func<HttpRequestMessage, HttpResponseMessage> handler)
            {
                _handler = handler;
            }

            public HttpRequestMessage LastRequest { get; private set; }

            protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
            {
                LastRequest = request;
                return Task.FromResult(_handler(request));
            }
        }

        private sealed class QueueHandler : HttpMessageHandler
        {
            private readonly Queue<Func<HttpRequestMessage, HttpResponseMessage>> _responses;

            public QueueHandler(params Func<HttpRequestMessage, HttpResponseMessage>[] responses)
            {
                _responses = new Queue<Func<HttpRequestMessage, HttpResponseMessage>>(responses);
            }

            public int SendCount { get; private set; }

            protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
            {
                SendCount++;
                return Task.FromResult(_responses.Dequeue()(request));
            }
        }

        private sealed class RecordingRegistryBackend : IRegistryBackend
        {
            public RegistryHive LastHive { get; private set; }
            public RegistryView LastView { get; private set; }

            public IRegistryKey OpenSubKey(RegistryHive hive, string subKey, RegistryView view)
            {
                LastHive = hive;
                LastView = view;
                return new FakeRegistryKey();
            }
        }

        private sealed class FakeRegistryKey : IRegistryKey
        {
            public string GetStringValue(string name)
            {
                return null;
            }

            public IEnumerable<string> GetSubKeyNames()
            {
                return new string[0];
            }

            public IRegistryKey OpenSubKey(string name)
            {
                return null;
            }

            public void Dispose()
            {
            }
        }
    }
}
