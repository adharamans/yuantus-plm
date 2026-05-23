using System;
using System.IO;
using System.Linq;
using System.Net;
using Newtonsoft.Json.Linq;
using Xunit;
using Yuantus.Cad.Shared.Identity;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Helper.Tests
{
    public sealed class HelperAuthOriginContractTests
    {
        [Fact]
        public void test_healthz_remains_bare_no_token_no_origin_no_protocol()
        {
            var decision = Gate(new ThrowingOriginResolver()).Authorize(Request("GET", "/healthz", null, null));

            Assert.True(decision.Allowed);
            Assert.Contains("{\\\"ok\\\":true}", ReadHelperSources());
            Assert.DoesNotContain("Access-Control-Allow-Origin", ReadHelperSources());
        }

        [Fact]
        public void test_version_path_remains_bare_exempt_after_s5_implementation()
        {
            var gate = Gate(new ThrowingOriginResolver());

            Assert.True(gate.Authorize(Request("GET", "/Version", null, null)).Allowed);
            Assert.True(gate.Authorize(Request("GET", "/version?ignored=true", null, null)).Allowed);
            Assert.False(gate.Authorize(Request("GET", "/version/", null, null)).Allowed);
            Assert.Contains("MapGet(\"/version\"", ReadHelperSources());
        }

        [Fact]
        public void test_protected_path_missing_local_token_returns_401_auth_local_token_missing()
        {
            var decision = Gate(AllowedOrigin()).Authorize(Request("GET", "/session/status", null, new[] { Paths.ProtocolVersion }));

            Assert.False(decision.Allowed);
            Assert.Equal(401, decision.StatusCode);
            Assert.Equal(ErrorCodes.AuthLocalTokenMissing, decision.Code);
        }

        [Fact]
        public void test_protected_path_invalid_local_token_returns_401_auth_local_token_invalid()
        {
            var decision = Gate(AllowedOrigin()).Authorize(Request("GET", "/session/status", new[] { "wrong" }, new[] { Paths.ProtocolVersion }));

            Assert.False(decision.Allowed);
            Assert.Equal(401, decision.StatusCode);
            Assert.Equal(ErrorCodes.AuthLocalTokenInvalid, decision.Code);
        }

        [Fact]
        public void test_protected_path_valid_token_uses_bootstrapped_in_memory_token()
        {
            var resolver = AllowedOrigin();
            var gate = Gate("bootstrapped-token", resolver);

            var decision = gate.Authorize(Request("GET", "/session/status", new[] { "bootstrapped-token" }, new[] { Paths.ProtocolVersion }));

            Assert.True(decision.Allowed);
            Assert.Equal(1, resolver.ResolveCalls);
        }

        [Fact]
        public void test_local_token_compare_is_fixed_time_and_exact()
        {
            Assert.True(HelperSecurityGate.FixedTimeTokenEquals("abc123", "abc123"));
            Assert.False(HelperSecurityGate.FixedTimeTokenEquals("abc123", "ABC123"));
            Assert.False(HelperSecurityGate.FixedTimeTokenEquals("abc123", "abc123 "));

            var sources = ReadHelperSources();
            Assert.Contains("FixedTimeTokenEquals", sources);
            Assert.DoesNotContain("string.Equals(_localToken", sources);
            Assert.DoesNotContain("_localToken == token", sources);
        }

        [Fact]
        public void test_protected_path_missing_protocol_returns_426_proto_version_unsupported()
        {
            var decision = Gate(AllowedOrigin()).Authorize(Request("GET", "/session/status", new[] { ValidToken }, null));

            Assert.False(decision.Allowed);
            Assert.Equal(426, decision.StatusCode);
            Assert.Equal(ErrorCodes.ProtoVersionUnsupported, decision.Code);
        }

        [Fact]
        public void test_protected_path_wrong_protocol_returns_426_proto_version_unsupported()
        {
            var decision = Gate(AllowedOrigin()).Authorize(Request("GET", "/session/status", new[] { ValidToken }, new[] { "2.0" }));

            Assert.False(decision.Allowed);
            Assert.Equal(426, decision.StatusCode);
            Assert.Equal(ErrorCodes.ProtoVersionUnsupported, decision.Code);
        }

        [Fact]
        public void test_origin_resolver_uses_tcp_peer_not_http_headers()
        {
            var resolver = AllowedOrigin();
            var request = Request("GET", "/session/status", new[] { ValidToken }, new[] { Paths.ProtocolVersion });
            request.Connection = new OriginConnection
            {
                LocalAddress = IPAddress.Parse("127.0.0.1"),
                LocalPort = 7959,
                RemoteAddress = IPAddress.Parse("127.0.0.1"),
                RemotePort = 50001
            };

            var decision = Gate(resolver).Authorize(request);

            Assert.True(decision.Allowed);
            Assert.Equal(7959, resolver.LastConnection.LocalPort);
            Assert.Equal(50001, resolver.LastConnection.RemotePort);
            Assert.DoesNotContain("X-Forwarded-For", ReadHelperSources());
            Assert.DoesNotContain("Referer", ReadHelperSources());
        }

        [Fact]
        public void test_origin_unresolvable_returns_403_origin_process_not_allowed()
        {
            var decision = Gate(new FakeOriginResolver(null)).Authorize(Request("GET", "/session/status", new[] { ValidToken }, new[] { Paths.ProtocolVersion }));

            Assert.False(decision.Allowed);
            Assert.Equal(403, decision.StatusCode);
            Assert.Equal(ErrorCodes.OriginProcessNotAllowed, decision.Code);
        }

        [Fact]
        public void test_origin_process_name_and_path_must_both_match()
        {
            var gate = Gate(new FakeOriginResolver(new OriginProcess(true, "acad.exe", @"C:\tmp\acad.exe")));
            Assert.Equal(ErrorCodes.OriginProcessNotAllowed, gate.Authorize(Request("GET", "/session/status", new[] { ValidToken }, new[] { Paths.ProtocolVersion })).Code);

            gate = Gate(new FakeOriginResolver(new OriginProcess(true, "not-acad.exe", @"C:\Program Files\Autodesk\AutoCAD 2024\acad.exe")));
            Assert.Equal(ErrorCodes.OriginProcessNotAllowed, gate.Authorize(Request("GET", "/session/status", new[] { ValidToken }, new[] { Paths.ProtocolVersion })).Code);

            gate = Gate(AllowedOrigin());
            Assert.True(gate.Authorize(Request("GET", "/session/status", new[] { ValidToken }, new[] { Paths.ProtocolVersion })).Allowed);
        }

        [Fact]
        public void test_origin_path_glob_is_case_insensitive_full_path_match()
        {
            var gate = Gate(new FakeOriginResolver(new OriginProcess(true, "ACAD.EXE", @"C:\PROGRAM FILES\AUTODESK\AUTOCAD 2024\ACAD.EXE")));
            Assert.True(gate.Authorize(Request("GET", "/session/status", new[] { ValidToken }, new[] { Paths.ProtocolVersion })).Allowed);

            gate = Gate(new FakeOriginResolver(new OriginProcess(true, "acad.exe", @"C:\Program Files\Autodesk\AutoCAD 2024\acad.exe.bak")));
            Assert.Equal(ErrorCodes.OriginProcessNotAllowed, gate.Authorize(Request("GET", "/session/status", new[] { ValidToken }, new[] { Paths.ProtocolVersion })).Code);
        }

        [Fact]
        public void test_default_origin_allowlist_contains_autocad_zwcad_gstarcad_and_companion()
        {
            var entries = HelperSecurityOptions.Default().OriginAllowlist;

            Assert.Contains(entries, entry => entry.ImageName == "acad.exe");
            Assert.Contains(entries, entry => entry.ImageName == "ZWCAD.exe");
            Assert.Contains(entries, entry => entry.ImageName == "gscad.exe");
            Assert.Contains(entries, entry => entry.ImageName == "yuantus-tauri-companion.exe");
        }

        [Fact]
        public void test_config_origin_whitelist_extends_defaults_without_replacing_them()
        {
            using (var temp = TempWorkspace.Create())
            {
                var path = Path.Combine(temp.Root, "config.json");
                File.WriteAllText(path, "{\"origin_whitelist\":[{\"image_name\":\"custom.exe\",\"path_pattern\":\"C:\\\\\\\\Tools\\\\\\\\custom.exe\"}]}");

                var entries = HelperSecurityOptions.Load(path).OriginAllowlist;

                Assert.Contains(entries, entry => entry.ImageName == "acad.exe");
                Assert.Contains(entries, entry => entry.ImageName == "custom.exe");
            }
        }

        [Fact]
        public void test_malformed_origin_whitelist_falls_back_to_defaults()
        {
            using (var temp = TempWorkspace.Create())
            {
                var path = Path.Combine(temp.Root, "config.json");
                File.WriteAllText(path, "{\"origin_whitelist\":[{\"image_name\":\"custom.exe\"}]}");

                var entries = HelperSecurityOptions.Load(path).OriginAllowlist;

                Assert.Contains(entries, entry => entry.ImageName == "acad.exe");
                Assert.DoesNotContain(entries, entry => entry.ImageName == "custom.exe");
            }
        }

        [Fact]
        public void test_auth_errors_use_http_status_and_json_error_envelope()
        {
            var decision = Gate(AllowedOrigin()).Authorize(Request("GET", "/session/status", null, new[] { Paths.ProtocolVersion }));
            var json = JObject.Parse(HelperSecurityResponse.ToJson(decision));

            Assert.Equal(401, decision.StatusCode);
            Assert.False(json.Value<bool>("ok"));
            Assert.Equal(ErrorCodes.AuthLocalTokenMissing, json["error"].Value<string>("code"));
            Assert.False(json["error"].Value<bool>("retryable"));
            Assert.Equal(JTokenType.Object, json["error"]["details"].Type);
        }

        [Fact]
        public void test_auth_error_responses_and_logs_do_not_leak_local_token()
        {
            var suppliedToken = "supplied-token-should-not-appear";
            var decision = Gate(AllowedOrigin()).Authorize(Request("GET", "/session/status", new[] { suppliedToken }, new[] { Paths.ProtocolVersion }));
            var json = HelperSecurityResponse.ToJson(decision);

            Assert.DoesNotContain(suppliedToken, json);
            Assert.DoesNotContain(ValidToken, json);
        }

        [Fact]
        public void test_s4_does_not_enable_cors_headers()
        {
            var sources = ReadHelperSources();

            Assert.DoesNotContain("UseCors", sources);
            Assert.DoesNotContain("Access-Control-Allow-Origin", sources);
            Assert.DoesNotContain("Access-Control-Allow-Headers", sources);
            Assert.DoesNotContain("Access-Control-Allow-Credentials", sources);
        }

        [Fact]
        public void test_s4_auth_gate_remains_in_front_of_s5_routes()
        {
            var sources = ReadHelperSources();

            Assert.Contains("MapGet(\"/healthz\"", sources);
            Assert.Contains("MapGet(\"/version\"", sources);
            Assert.Contains("MapPost(\"/session/login\"", sources);
            Assert.DoesNotContain("MapPut(", sources);
            Assert.DoesNotContain("MapDelete(", sources);
            Assert.Contains("security.Authorize", sources);
        }

        [Fact]
        public void test_s4_still_does_not_forward_browser_authorization_header()
        {
            var sources = ReadHelperSources();

            Assert.DoesNotContain("context.Request.Headers[\"Authorization\"]", sources);
            Assert.Contains("request.Headers.Authorization = new AuthenticationHeaderValue(\"Bearer\", bearerToken)", sources);
            Assert.Contains("ErrorCodes.AuthPlmNotLoggedIn", sources);
            Assert.Contains("/session/login", sources);
        }

        [Fact]
        public void test_no_s8_scope_leak_after_s7_reset_token()
        {
            var sources = ReadHelperSources();

            Assert.Contains("/diff/preview", sources);
            Assert.Contains("/sync/inbound", sources);
            Assert.Contains("/sync/outbound", sources);
            Assert.Contains("/audit/apply-result", sources);
            Assert.Contains("--reset-local-token", sources);
            Assert.DoesNotContain("/dedup/check", sources);
            Assert.DoesNotContain("/shell/notify", sources);
            Assert.DoesNotContain("CADDedupPlugin", sources);
        }

        [Fact]
        public void test_dotnet_workflow_still_covers_helper_tests()
        {
            var workflow = File.ReadAllText(Path.Combine(FindRepoRoot(), ".github", "workflows", "cad-helper-shared-dotnet.yml"));

            Assert.Contains("clients/cad-desktop-helper/Helper/**", workflow);
            Assert.Contains("clients/cad-desktop-helper/Helper.Tests/**", workflow);
            Assert.Contains("dotnet test clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj", workflow);
        }

        private const string ValidToken = "valid-local-token";

        private static HelperSecurityGate Gate(IOriginProcessResolver resolver)
        {
            return Gate(ValidToken, resolver);
        }

        private static HelperSecurityGate Gate(string localToken, IOriginProcessResolver resolver)
        {
            return new HelperSecurityGate(localToken, HelperSecurityOptions.Default(), resolver);
        }

        private static RecordingOriginResolver AllowedOrigin()
        {
            return new RecordingOriginResolver(new OriginProcess(true, "acad.exe", @"C:\Program Files\Autodesk\AutoCAD 2024\acad.exe"));
        }

        private static HelperSecurityRequest Request(string method, string path, string[] tokenHeaders, string[] protocolHeaders)
        {
            var pathOnly = path;
            var queryIndex = pathOnly == null ? -1 : pathOnly.IndexOf('?');
            if (queryIndex >= 0)
            {
                pathOnly = pathOnly.Substring(0, queryIndex);
            }
            return new HelperSecurityRequest
            {
                Method = method,
                Path = pathOnly,
                LocalTokenHeaders = tokenHeaders,
                ProtocolHeaders = protocolHeaders,
                Connection = new OriginConnection
                {
                    LocalAddress = IPAddress.Parse("127.0.0.1"),
                    LocalPort = 7959,
                    RemoteAddress = IPAddress.Parse("127.0.0.1"),
                    RemotePort = 50000
                }
            };
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

        private static int CountOccurrences(string text, string value)
        {
            var count = 0;
            var index = 0;
            while ((index = text.IndexOf(value, index, StringComparison.Ordinal)) >= 0)
            {
                count++;
                index += value.Length;
            }
            return count;
        }

        private sealed class FakeOriginResolver : IOriginProcessResolver
        {
            private readonly OriginProcess _process;

            public FakeOriginResolver(OriginProcess process)
            {
                _process = process;
            }

            public OriginProcess Resolve(OriginConnection connection)
            {
                return _process;
            }
        }

        private sealed class RecordingOriginResolver : IOriginProcessResolver
        {
            private readonly OriginProcess _process;

            public RecordingOriginResolver(OriginProcess process)
            {
                _process = process;
            }

            public int ResolveCalls { get; private set; }
            public OriginConnection LastConnection { get; private set; }

            public OriginProcess Resolve(OriginConnection connection)
            {
                ResolveCalls++;
                LastConnection = connection;
                return _process;
            }
        }

        private sealed class ThrowingOriginResolver : IOriginProcessResolver
        {
            public OriginProcess Resolve(OriginConnection connection)
            {
                throw new InvalidOperationException("Origin resolver should not be called for exempt paths.");
            }
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
    }
}
