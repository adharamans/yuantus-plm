using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Xunit;
using Yuantus.Cad.Shared.Identity;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Helper.Tests
{
    public sealed class HelperSessionRoutesContractTests
    {
        [Fact]
        public void test_version_is_bare_and_reports_helper_protocol_without_session_data()
        {
            var gate = new HelperSecurityGate("token", HelperSecurityOptions.Default(), new ThrowingOriginResolver());
            var response = HelperRouteResponse.ToJson(HelperRouteResult.Success(HelperVersionResponse.Current()));
            var json = JObject.Parse(response);

            Assert.True(gate.Authorize(Request("GET", "/version", null, null)).Allowed);
            Assert.Equal("0.1.0", json["data"].Value<string>("helper_version"));
            Assert.Equal(Paths.ProtocolVersion, json["data"].Value<string>("protocol_version"));
            Assert.Equal(new[] { "session", "current_drawing" }, json["data"]["features"].Values<string>().ToArray());
            Assert.DoesNotContain("server_url", response);
            Assert.DoesNotContain("tenant_id", response);
            Assert.DoesNotContain("access_token", response);
            Assert.DoesNotContain("pid", response);
        }

        [Fact]
        public void test_session_routes_are_protected_by_s4_security_gate()
        {
            var gate = new HelperSecurityGate("token", HelperSecurityOptions.Default(), new ThrowingOriginResolver());

            Assert.Equal(ErrorCodes.AuthLocalTokenMissing, gate.Authorize(Request("GET", "/session/status", null, new[] { Paths.ProtocolVersion })).Code);
            Assert.Equal(ErrorCodes.AuthLocalTokenMissing, gate.Authorize(Request("POST", "/session/login", null, new[] { Paths.ProtocolVersion })).Code);
            Assert.True(gate.Authorize(Request("GET", "/version", null, null)).Allowed);
        }

        [Fact]
        public async Task test_session_login_requires_valid_server_url_tenant_username_password()
        {
            var service = CreateService();

            Assert.Equal(ErrorCodes.HelperInputValidationFailed, (await service.LoginAsync(null, CancellationToken.None)).Code);
            Assert.Equal(ErrorCodes.HelperInputValidationFailed, (await service.LoginAsync(new SessionLoginRequest
            {
                ServerUrl = "ftp://plm.example.com",
                TenantId = "tenant",
                Username = "admin",
                Password = "secret"
            }, CancellationToken.None)).Code);
        }

        [Fact]
        public async Task test_session_login_enforces_server_allowlist_before_plm_call()
        {
            var plm = new RecordingPlmLoginClient("token-1");
            var service = CreateService(new InMemoryConfigStore { ServerAllowlist = new[] { "https://plm.example.com" } }, null, plm, null);

            var result = await service.LoginAsync(Login("https://evil.example.com/api/v1"), CancellationToken.None);

            Assert.False(result.Ok);
            Assert.Equal(ErrorCodes.HelperInputValidationFailed, result.Code);
            Assert.Equal(0, plm.Calls);
        }

        [Fact]
        public void test_server_allowlist_uses_parsed_uri_host_and_port_not_string_prefix()
        {
            Assert.True(new ServerAllowlist(new[] { "https://plm.example.com" }).Allows(new Uri("https://PLM.example.com/api/v1")));
            Assert.False(new ServerAllowlist(new[] { "https://plm.example.com" }).Allows(new Uri("https://plm.example.com.evil.com/api/v1")));
            Assert.True(new ServerAllowlist(new[] { "https://plm.example.com:443" }).Allows(new Uri("https://plm.example.com/api/v1")));
            Assert.False(new ServerAllowlist(new[] { "https://plm.example.com:8443" }).Allows(new Uri("https://plm.example.com/api/v1")));
            Assert.True(new ServerAllowlist(new[] { "https://*.yuantus.internal" }).Allows(new Uri("https://qa.yuantus.internal/api/v1")));
            Assert.False(new ServerAllowlist(new[] { "https://*.yuantus.internal" }).Allows(new Uri("https://yuantus.internal/api/v1")));
        }

        [Fact]
        public async Task test_session_login_forwards_only_auth_payload_to_plm_login()
        {
            var plm = new RecordingPlmLoginClient("token-1");
            var service = CreateService(null, null, plm, null);

            await service.LoginAsync(Login("https://plm.example.com/api/v1"), CancellationToken.None);

            Assert.Equal("tenant-a", plm.LastRequest.TenantId);
            Assert.Equal("org-a", plm.LastRequest.OrgId);
            Assert.Equal("admin", plm.LastRequest.Username);
            Assert.Equal("secret", plm.LastRequest.Password);
            Assert.DoesNotContain("default_profile", JsonConvert.SerializeObject(plm.LastRequest));
            Assert.DoesNotContain("X-Yuantus-Local-Token", JsonConvert.SerializeObject(plm.LastRequest));
        }

        [Fact]
        public async Task test_session_login_stores_bearer_with_dpapi_not_config_json()
        {
            var config = new InMemoryConfigStore();
            var bearer = new InMemoryBearerStore();
            var service = CreateService(config, bearer, new RecordingPlmLoginClient("access-token-secret"), null);

            await service.LoginAsync(Login("https://plm.example.com/api/v1"), CancellationToken.None);

            Assert.Equal("access-token-secret", bearer.Token);
            Assert.DoesNotContain("access-token-secret", config.Serialized);
        }

        [Fact]
        public void test_plm_bearer_uses_ratified_dpapi_entropy()
        {
            Assert.Equal("yuantus-cad-plm-bearer-v1", DpapiPlmBearerTokenStore.EntropyLiteral);
            Assert.Contains("DpapiEnvelope.Protect", ReadHelperSources());
            Assert.Contains("DpapiEnvelope.Unprotect", ReadHelperSources());
        }

        [Fact]
        public async Task test_session_login_persists_server_tenant_org_and_default_profile()
        {
            var config = new InMemoryConfigStore();
            var service = CreateService(config, null, new RecordingPlmLoginClient("token"), null);

            var result = await service.LoginAsync(Login("https://plm.example.com/api/v1/"), CancellationToken.None);
            var response = (SessionLoginResponse)result.Data;

            Assert.Equal("https://plm.example.com/api/v1", config.ServerUrl);
            Assert.Equal("tenant-a", config.TenantId);
            Assert.Equal("org-a", config.OrgId);
            Assert.Equal("profile-a", config.DefaultProfileId);
            Assert.Equal(config.ServerUrl, response.ServerUrl);
        }

        [Fact]
        public void test_session_config_write_is_atomic_and_preserves_unknown_fields()
        {
            using (var temp = TempWorkspace.Create())
            {
                var path = Path.Combine(temp.Root, "config.json");
                File.WriteAllText(path, "{\"idle_timeout_minutes\":15,\"origin_whitelist\":[{\"image_name\":\"custom.exe\",\"path_pattern\":\"C:/custom.exe\"}],\"server_allowlist\":[\"https://plm.example.com\"],\"unknown\":true}");
                var store = new JsonHelperSessionConfigStore(path);

                store.SaveLogin("https://plm.example.com/api/v1", "tenant-a", "org-a", "profile-a");
                store.ClearLogin();

                var json = JObject.Parse(File.ReadAllText(path));
                Assert.Equal(15, json.Value<int>("idle_timeout_minutes"));
                Assert.NotNull(json["origin_whitelist"]);
                Assert.NotNull(json["server_allowlist"]);
                Assert.True(json.Value<bool>("unknown"));
                Assert.Equal("https://plm.example.com/api/v1", json.Value<string>("server_url"));
                Assert.Equal("profile-a", json.Value<string>("default_profile_id"));
                Assert.Null(json["tenant_id"]);
                Assert.Empty(Directory.GetFiles(temp.Root, "*.tmp"));
            }
        }

        [Fact]
        public async Task test_session_login_response_never_echoes_access_token_or_password()
        {
            var service = CreateService(null, null, new RecordingPlmLoginClient("access-token-secret"), null);

            var json = HelperRouteResponse.ToJson(await service.LoginAsync(Login("https://plm.example.com/api/v1"), CancellationToken.None));

            Assert.DoesNotContain("access-token-secret", json);
            Assert.DoesNotContain("secret", json);
            Assert.Contains("admin", json);
        }

        [Fact]
        public async Task test_session_login_failure_preserves_previous_session_and_token()
        {
            var config = new InMemoryConfigStore { ServerUrl = "https://old/api/v1", TenantId = "old-tenant" };
            var bearer = new InMemoryBearerStore { Token = "old-token" };
            var service = CreateService(config, bearer, new RecordingPlmLoginClient("new-token") { Reject = true }, null);

            var result = await service.LoginAsync(Login("https://plm.example.com/api/v1"), CancellationToken.None);

            Assert.False(result.Ok);
            Assert.Equal(ErrorCodes.AuthPlmNotLoggedIn, result.Code);
            Assert.Equal("https://old/api/v1", config.ServerUrl);
            Assert.Equal("old-tenant", config.TenantId);
            Assert.Equal("old-token", bearer.Token);
        }

        [Fact]
        public void test_session_status_missing_token_or_tenant_returns_logged_out_not_error()
        {
            var config = new InMemoryConfigStore { ServerUrl = "https://plm/api/v1", TenantId = "tenant-a" };
            var service = CreateService(config, new InMemoryBearerStore(), null, null);

            var withoutToken = (SessionStatusResponse)service.Status().Data;
            config.TenantId = null;
            var withoutTenant = (SessionStatusResponse)service.Status().Data;

            Assert.False(withoutToken.LoggedIn);
            Assert.False(withoutTenant.LoggedIn);
        }

        [Fact]
        public void test_session_status_never_calls_plm_and_never_returns_bearer()
        {
            var plm = new RecordingPlmLoginClient("token");
            var service = CreateService(new InMemoryConfigStore { TenantId = "tenant-a" }, new InMemoryBearerStore { Token = "secret-token" }, plm, null);

            var json = HelperRouteResponse.ToJson(service.Status());

            Assert.Equal(0, plm.Calls);
            Assert.DoesNotContain("secret-token", json);
            Assert.Contains("\"logged_in\":true", json);
        }

        [Fact]
        public void test_session_logout_clears_bearer_tenant_org_but_preserves_server_and_profile()
        {
            var config = new InMemoryConfigStore { ServerUrl = "https://plm/api/v1", TenantId = "tenant-a", OrgId = "org-a", DefaultProfileId = "profile-a" };
            var bearer = new InMemoryBearerStore { Token = "token" };
            var service = CreateService(config, bearer, null, null);

            service.Logout();

            Assert.Null(bearer.Token);
            Assert.Null(config.TenantId);
            Assert.Null(config.OrgId);
            Assert.Equal("https://plm/api/v1", config.ServerUrl);
            Assert.Equal("profile-a", config.DefaultProfileId);
        }

        [Fact]
        public void test_session_logout_is_idempotent_and_does_not_call_plm()
        {
            var plm = new RecordingPlmLoginClient("token");
            var service = CreateService(null, new InMemoryBearerStore(), plm, null);

            Assert.True(service.Logout().Ok);
            Assert.True(service.Logout().Ok);
            Assert.Equal(0, plm.Calls);
        }

        [Fact]
        public void test_current_drawing_accepts_caller_supplied_context_without_reading_dwg()
        {
            var service = CreateService();

            var response = (CurrentDrawingResponse)service.SetCurrentDrawing(new CurrentDrawingRequest
            {
                Drawing = new CurrentDrawingPayload { Filename = " J2824002-06.dwg ", Filepath = @"D:\projects\demo\" },
                CadSystem = "AutoCAD"
            }).Data;

            Assert.Equal("J2824002-06.dwg", response.Drawing.Filename);
            Assert.Equal(@"D:\projects\demo\", response.Drawing.Filepath);
            Assert.Equal("autocad", response.CadSystem);
        }

        [Fact]
        public void test_current_drawing_rejects_missing_filename_and_invalid_cad_system()
        {
            var service = CreateService();

            Assert.Equal(ErrorCodes.HelperInputValidationFailed, service.SetCurrentDrawing(new CurrentDrawingRequest { Drawing = new CurrentDrawingPayload() }).Code);
            Assert.Equal(ErrorCodes.HelperInputValidationFailed, service.SetCurrentDrawing(new CurrentDrawingRequest
            {
                Drawing = new CurrentDrawingPayload { Filename = "a.dwg" },
                CadSystem = "solidworks"
            }).Code);
        }

        [Fact]
        public void test_current_drawing_is_memory_only_not_config_or_sqlite()
        {
            var config = new InMemoryConfigStore { ServerUrl = "https://plm/api/v1" };
            var service = CreateService(config, null, null, new CurrentDrawingStore());

            service.SetCurrentDrawing(new CurrentDrawingRequest { Drawing = new CurrentDrawingPayload { Filename = "a.dwg" } });

            Assert.DoesNotContain("a.dwg", config.Serialized);
            Assert.DoesNotContain("audit_events", JsonConvert.SerializeObject(service.SetCurrentDrawing(new CurrentDrawingRequest { Drawing = new CurrentDrawingPayload { Filename = "b.dwg" } })));
        }

        [Fact]
        public void test_s6_adds_exactly_version_session_current_drawing_and_business_routes()
        {
            var sources = ReadHelperSources();

            Assert.Contains("MapGet(\"/healthz\"", sources);
            Assert.Contains("MapGet(\"/version\"", sources);
            Assert.Contains("MapPost(\"/session/login\"", sources);
            Assert.Contains("MapPost(\"/session/logout\"", sources);
            Assert.Contains("MapGet(\"/session/status\"", sources);
            Assert.Contains("MapPost(\"/cad/current-drawing\"", sources);
            Assert.Contains("MapPost(\"/diff/preview\"", sources);
            Assert.Contains("MapPost(\"/sync/inbound\"", sources);
            Assert.Contains("MapPost(\"/sync/outbound\"", sources);
            Assert.Contains("MapPost(\"/audit/apply-result\"", sources);
            Assert.Equal(10, CountOccurrences(sources, "MapGet(") + CountOccurrences(sources, "MapPost("));
            Assert.DoesNotContain("MapPut(", sources);
            Assert.DoesNotContain("MapDelete(", sources);
            Assert.DoesNotContain("MapPatch(", sources);
        }

        [Fact]
        public void test_s5_s6_do_not_add_s7_s8_routes_or_reset_token()
        {
            var sources = ReadHelperSources();

            Assert.Contains("/diff/preview", sources);
            Assert.Contains("/sync/inbound", sources);
            Assert.Contains("/sync/outbound", sources);
            Assert.Contains("/audit/apply-result", sources);
            Assert.DoesNotContain("/dedup/check", sources);
            Assert.DoesNotContain("/shell/notify", sources);
            Assert.DoesNotContain("/compose", sources);
            Assert.DoesNotContain("/validate", sources);
            Assert.DoesNotContain("/tasks", sources);
            Assert.DoesNotContain("/diagnostics/snapshot", sources);
            Assert.DoesNotContain("--reset-local-token", sources);
            Assert.DoesNotContain("UseCors", sources);
        }

        [Fact]
        public void test_s5_keeps_cad_helper_dotnet_workflow_covering_helper_tests()
        {
            var workflow = File.ReadAllText(Path.Combine(FindRepoRoot(), ".github", "workflows", "cad-helper-shared-dotnet.yml"));

            Assert.Contains("clients/cad-desktop-helper/Helper/**", workflow);
            Assert.Contains("clients/cad-desktop-helper/Helper.Tests/**", workflow);
            Assert.Contains("dotnet test clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj", workflow);
        }

        [Fact]
        public void test_s5_preserves_s4_auth_origin_contract_tests()
        {
            var sources = ReadHelperSources();

            Assert.Contains("HelperSecurityGate", sources);
            Assert.Contains("FixedTimeTokenEquals", sources);
            Assert.Contains("WindowsTcpOriginProcessResolver", sources);
            Assert.DoesNotContain("UseCors", sources);
            Assert.DoesNotContain("Access-Control-Allow-Origin", sources);
        }

        private static HelperSessionService CreateService()
        {
            return CreateService(null, null, null, null);
        }

        private static HelperSessionService CreateService(
            InMemoryConfigStore config,
            InMemoryBearerStore bearer,
            RecordingPlmLoginClient plm,
            CurrentDrawingStore drawing)
        {
            return new HelperSessionService(
                config ?? new InMemoryConfigStore(),
                bearer ?? new InMemoryBearerStore(),
                plm ?? new RecordingPlmLoginClient("token"),
                drawing ?? new CurrentDrawingStore());
        }

        private static SessionLoginRequest Login(string serverUrl)
        {
            return new SessionLoginRequest
            {
                ServerUrl = serverUrl,
                TenantId = "tenant-a",
                OrgId = "org-a",
                DefaultProfileId = "profile-a",
                Username = "admin",
                Password = "secret"
            };
        }

        private static HelperSecurityRequest Request(string method, string path, string[] tokenHeaders, string[] protocolHeaders)
        {
            return new HelperSecurityRequest
            {
                Method = method,
                Path = path,
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
            return string.Join("\n", Directory.GetFiles(helperDir, "*.cs", SearchOption.AllDirectories).Select(File.ReadAllText));
        }

        private static string FindRepoRoot()
        {
            var dir = AppContext.BaseDirectory;
            while (dir != null && !File.Exists(Path.Combine(dir, "pyproject.toml")))
            {
                dir = Directory.GetParent(dir) == null ? null : Directory.GetParent(dir).FullName;
            }
            if (dir == null)
            {
                throw new DirectoryNotFoundException("Unable to locate repository root.");
            }
            return dir;
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

        private sealed class InMemoryConfigStore : IHelperSessionConfigStore
        {
            public string ServerUrl;
            public string TenantId;
            public string OrgId;
            public string DefaultProfileId;
            public string[] ServerAllowlist = new string[0];

            public string Serialized
            {
                get
                {
                    return JsonConvert.SerializeObject(new
                    {
                        server_url = ServerUrl,
                        tenant_id = TenantId,
                        org_id = OrgId,
                        default_profile_id = DefaultProfileId,
                        server_allowlist = ServerAllowlist
                    });
                }
            }

            public HelperSessionSnapshot Read()
            {
                return new HelperSessionSnapshot
                {
                    ServerUrl = ServerUrl,
                    TenantId = TenantId,
                    OrgId = OrgId,
                    DefaultProfileId = DefaultProfileId
                };
            }

            public IReadOnlyList<string> ReadServerAllowlist()
            {
                return ServerAllowlist;
            }

            public void SaveLogin(string serverUrl, string tenantId, string orgId, string defaultProfileId)
            {
                ServerUrl = serverUrl;
                TenantId = tenantId;
                OrgId = orgId;
                DefaultProfileId = defaultProfileId;
            }

            public void ClearLogin()
            {
                TenantId = null;
                OrgId = null;
            }
        }

        private sealed class InMemoryBearerStore : IPlmBearerTokenStore
        {
            public string Token;

            public string Read()
            {
                return Token;
            }

            public void Write(string accessToken)
            {
                Token = accessToken;
            }

            public void Clear()
            {
                Token = null;
            }
        }

        private sealed class RecordingPlmLoginClient : IPlmLoginClient
        {
            private readonly string _token;

            public RecordingPlmLoginClient(string token)
            {
                _token = token;
            }

            public int Calls { get; private set; }
            public bool Reject { get; set; }
            public PlmLoginRequest LastRequest { get; private set; }
            public Uri LastServerUri { get; private set; }

            public Task<PlmLoginResponse> LoginAsync(Uri serverUri, PlmLoginRequest request, CancellationToken cancellationToken)
            {
                Calls++;
                LastServerUri = serverUri;
                LastRequest = request;
                if (Reject)
                {
                    throw new PlmLoginRejectedException("bad credentials");
                }
                return Task.FromResult(new PlmLoginResponse { AccessToken = _token, TenantId = request.TenantId, UserId = "user-1" });
            }
        }

        private sealed class ThrowingOriginResolver : IOriginProcessResolver
        {
            public OriginProcess Resolve(OriginConnection connection)
            {
                throw new InvalidOperationException("exempt route should not resolve origin");
            }
        }

        private sealed class TempWorkspace : IDisposable
        {
            public TempWorkspace()
            {
                Root = Path.Combine(Path.GetTempPath(), "yuantus-cad-helper-s5-tests-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(Root);
            }

            public string Root { get; private set; }

            public static TempWorkspace Create()
            {
                return new TempWorkspace();
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
