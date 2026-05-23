using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Data.Sqlite;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Xunit;
using Yuantus.Cad.Shared.Identity;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Helper.Tests
{
    public sealed class HelperBusinessAuditContractTests
    {
        [Fact]
        public void test_s6_adds_exactly_diff_sync_and_audit_routes()
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
        public void test_s6_routes_are_protected_by_s4_security_gate()
        {
            var gate = new HelperSecurityGate("token", HelperSecurityOptions.Default(), AllowedOrigin());

            Assert.Equal(ErrorCodes.AuthLocalTokenMissing, gate.Authorize(Request("POST", "/diff/preview", null, new[] { Paths.ProtocolVersion })).Code);
            Assert.Equal(ErrorCodes.AuthLocalTokenMissing, gate.Authorize(Request("POST", "/sync/inbound", null, new[] { Paths.ProtocolVersion })).Code);
            Assert.Equal(ErrorCodes.AuthLocalTokenMissing, gate.Authorize(Request("POST", "/sync/outbound", null, new[] { Paths.ProtocolVersion })).Code);
            Assert.Equal(ErrorCodes.AuthLocalTokenMissing, gate.Authorize(Request("POST", "/audit/apply-result", null, new[] { Paths.ProtocolVersion })).Code);
            Assert.True(gate.Authorize(Request("POST", "/diff/preview", new[] { "token" }, new[] { Paths.ProtocolVersion })).Allowed);
        }

        [Fact]
        public async Task test_s6_requires_logged_in_session_before_plm_forwarding()
        {
            var plm = new RecordingBusinessClient();
            var service = CreateService(new InMemoryConfigStore(), new InMemoryBearerStore(), null, null, null, null, plm);

            var result = await service.SyncInboundAsync(new JObject(), CancellationToken.None);

            Assert.False(result.Ok);
            Assert.Equal(ErrorCodes.AuthTenantMissing, result.Code);
            Assert.Empty(plm.Calls);

            service = CreateService(new InMemoryConfigStore { ServerUrl = "https://plm.example.com/api/v1", TenantId = "tenant-a" }, new InMemoryBearerStore(), null, null, null, null, plm);
            result = await service.SyncInboundAsync(new JObject(), CancellationToken.None);

            Assert.False(result.Ok);
            Assert.Equal(ErrorCodes.AuthPlmNotLoggedIn, result.Code);
            Assert.Empty(plm.Calls);
        }

        [Fact]
        public async Task test_diff_preview_requires_item_id_and_does_not_forward_other_request_modes()
        {
            var plm = new RecordingBusinessClient();
            var service = CreateService(plm);

            Assert.Equal(ErrorCodes.HelperInputValidationFailed, (await service.DiffPreviewAsync(new JObject(), CancellationToken.None)).Code);
            Assert.Equal(ErrorCodes.HelperInputValidationFailed, (await service.DiffPreviewAsync(new JObject { ["item_id"] = "item-1", ["values"] = new JObject { ["name"] = "x" } }, CancellationToken.None)).Code);
            Assert.Equal(ErrorCodes.HelperInputValidationFailed, (await service.DiffPreviewAsync(new JObject { ["item_id"] = "item-1", ["target_properties"] = new JObject { ["name"] = "x" } }, CancellationToken.None)).Code);
            Assert.Equal(ErrorCodes.HelperInputValidationFailed, (await service.DiffPreviewAsync(new JObject { ["item_id"] = "item-1", ["target_cad_fields"] = new JObject { ["MAT"] = "x" } }, CancellationToken.None)).Code);
            Assert.Empty(plm.Calls);
        }

        [Fact]
        public async Task test_diff_preview_forwards_to_configured_plm_endpoint_with_bearer_only()
        {
            var plm = new RecordingBusinessClient();
            var service = CreateService(plm);

            await service.DiffPreviewAsync(new JObject { ["item_id"] = "item-1", ["cad_system"] = "autocad" }, CancellationToken.None);

            var call = plm.Calls.Single();
            Assert.Equal("https://plm.example.com/api/v1", call.ServerUri.ToString());
            Assert.Equal("/plugins/cad-material-sync/diff/preview", call.EndpointPath);
            Assert.Equal("bearer-secret", call.BearerToken);
            Assert.Equal(Paths.ProtocolVersion, call.ProtocolVersion);
            Assert.Equal("item-1", call.Payload.Value<string>("item_id"));
            Assert.DoesNotContain("X-Yuantus-Local-Token", JsonConvert.SerializeObject(call.Payload));
            Assert.DoesNotContain("Authorization", JsonConvert.SerializeObject(call.Payload));
        }

        [Fact]
        public async Task test_diff_preview_wraps_server_response_and_generates_pull_id()
        {
            var service = CreateService(new RecordingBusinessClient
            {
                Response = PlmBusinessResponse.Success(new JObject
                {
                    ["ok"] = true,
                    ["write_cad_fields"] = new JObject { ["MAT"] = "AL6061" }
                })
            });

            var result = await service.DiffPreviewAsync(new JObject { ["item_id"] = "item-1" }, CancellationToken.None);
            var data = (JObject)result.Data;

            Assert.True(result.Ok);
            Assert.Matches("^PULL-[0-9a-f]{32}$", data.Value<string>("pull_id"));
            Assert.Equal("AL6061", data["server_response"]["write_cad_fields"].Value<string>("MAT"));
        }

        [Fact]
        public async Task test_pull_cache_expires_after_ten_minutes()
        {
            var clock = new FakeClock(DateTimeOffset.Parse("2026-05-22T10:00:00Z"));
            var service = CreateService(null, null, null, null, clock, null);

            var pullId = await CreatePullAsync(service);
            clock.Advance(TimeSpan.FromMinutes(10));

            var result = service.ApplyResult(new JObject { ["pull_id"] = pullId, ["outcome"] = "ok" });

            Assert.False(result.Ok);
            Assert.Equal(ErrorCodes.AuditPullIdExpired, result.Code);
        }

        [Fact]
        public async Task test_audit_apply_result_rejects_unknown_expired_and_duplicate_pull_id()
        {
            var clock = new FakeClock(DateTimeOffset.Parse("2026-05-22T10:00:00Z"));
            var service = CreateService(null, null, null, new RecordingAuditStore(), clock, null);

            Assert.Equal(ErrorCodes.AuditPullIdUnknown, service.ApplyResult(new JObject { ["pull_id"] = "PULL-unknown", ["outcome"] = "ok" }).Code);

            var pullId = await CreatePullAsync(service);
            Assert.True(service.ApplyResult(new JObject { ["pull_id"] = pullId, ["outcome"] = "ok" }).Ok);
            Assert.Equal(ErrorCodes.AuditAlreadyReported, service.ApplyResult(new JObject { ["pull_id"] = pullId, ["outcome"] = "ok" }).Code);

            var expiredService = CreateService(null, null, null, new RecordingAuditStore(), clock, null);
            var expiredPull = await CreatePullAsync(expiredService);
            clock.Advance(TimeSpan.FromMinutes(10));
            Assert.Equal(ErrorCodes.AuditPullIdExpired, expiredService.ApplyResult(new JObject { ["pull_id"] = expiredPull, ["outcome"] = "ok" }).Code);
        }

        [Fact]
        public async Task test_audit_apply_result_persists_successful_apply_row()
        {
            var audit = new RecordingAuditStore();
            var service = CreateService(null, null, null, audit, null, null);
            var pullId = await CreatePullAsync(service);

            var result = service.ApplyResult(new JObject
            {
                ["pull_id"] = pullId,
                ["outcome"] = "partial",
                ["applied_fields"] = new JObject { ["MAT"] = "AL6061" },
                ["failed_fields"] = new JObject { ["REV"] = "locked" }
            });

            Assert.True(result.Ok);
            var row = audit.Events.Last();
            Assert.Equal("/audit/apply-result", row.Endpoint);
            Assert.Equal("partial", row.Outcome);
            Assert.Equal(pullId, row.PullId);
            Assert.Contains("AL6061", row.AppliedFieldsJson);
            Assert.Contains("locked", row.FailedFieldsJson);
        }

        [Fact]
        public async Task test_sync_inbound_forwards_payload_and_preserves_plm_conflict_code()
        {
            var service = CreateService(new RecordingBusinessClient
            {
                Response = PlmBusinessResponse.Error(ErrorCodes.PlmInboundConflict, "conflict")
            });

            var result = await service.SyncInboundAsync(new JObject { ["item_id"] = "item-1", ["overwrite"] = false }, CancellationToken.None);

            Assert.False(result.Ok);
            Assert.Equal(ErrorCodes.PlmInboundConflict, result.Code);
        }

        [Fact]
        public async Task test_sync_outbound_forwards_payload_and_returns_server_cad_fields()
        {
            var plm = new RecordingBusinessClient
            {
                Response = PlmBusinessResponse.Success(new JObject
                {
                    ["ok"] = true,
                    ["cad_fields"] = new JObject { ["MAT"] = "AL6061" }
                })
            };
            var service = CreateService(plm);

            var result = await service.SyncOutboundAsync(new JObject { ["item_id"] = "item-1" }, CancellationToken.None);

            Assert.True(result.Ok);
            Assert.Equal("/plugins/cad-material-sync/sync/outbound", plm.Calls.Single().EndpointPath);
            Assert.Equal("AL6061", ((JObject)result.Data)["cad_fields"].Value<string>("MAT"));
        }

        [Fact]
        public void test_sqlite_audit_schema_matches_r3_contract()
        {
            using (var temp = TempWorkspace.Create())
            {
                var dbPath = Path.Combine(temp.Root, "audit.db");
                var store = new SqliteAuditEventStore(dbPath);
                store.Write(new AuditEvent
                {
                    Timestamp = DateTimeOffset.Parse("2026-05-22T10:00:00Z"),
                    Endpoint = "/diff/preview",
                    Outcome = "ok",
                    DurationMs = 12,
                    TraceId = "trace-1"
                });

                using (var connection = new SqliteConnection("Data Source=" + dbPath))
                {
                    connection.Open();
                    var columns = ReadColumnNames(connection, "audit_events");
                    Assert.Equal(new[]
                    {
                        "id", "ts", "endpoint", "drawing_path", "profile_id", "item_id", "pull_id", "cad_system",
                        "outcome", "error_code", "duration_ms", "trace_id", "applied_fields_json", "failed_fields_json"
                    }, columns);
                    Assert.Contains("idx_audit_ts", ReadIndexNames(connection));
                    Assert.Contains("idx_audit_pull", ReadIndexNames(connection));
                }
            }
        }

        [Fact]
        public void test_session_login_and_logout_are_audited_without_changing_s5_contract()
        {
            var audit = new RecordingAuditStore();
            var service = CreateService(null, null, null, audit, null, null);

            service.AuditSessionResult("/session/login", HelperRouteResult.Success(new SessionLoginResponse { LoggedIn = true }));
            service.AuditSessionResult("/session/logout", HelperRouteResult.Success(new SessionLogoutResponse { LoggedIn = false }));

            Assert.Equal(new[] { "/session/login", "/session/logout" }, audit.Events.Select(item => item.Endpoint).ToArray());
            Assert.All(audit.Events, item => Assert.Equal("ok", item.Outcome));
            Assert.Contains("businessAuditService.AuditSessionResult(\"/session/login\"", ReadHelperSources());
            Assert.Contains("businessAuditService.AuditSessionResult(\"/session/logout\"", ReadHelperSources());
        }

        [Fact]
        public void test_healthz_version_and_session_status_are_not_audited()
        {
            var sources = ReadHelperSources();

            Assert.Contains("MapGet(\"/healthz\"", sources);
            Assert.Contains("MapGet(\"/version\"", sources);
            Assert.Contains("MapGet(\"/session/status\"", sources);
            Assert.DoesNotContain("AuditSessionResult(\"/healthz\"", sources);
            Assert.DoesNotContain("AuditSessionResult(\"/version\"", sources);
            Assert.DoesNotContain("AuditSessionResult(\"/session/status\"", sources);
        }

        [Fact]
        public async Task test_audit_write_failure_policy_matches_ratified_h_boundary()
        {
            var pullCache = new PullCache();
            var firstAudit = new RecordingAuditStore();
            var service = CreateService(null, null, pullCache, firstAudit, null, null);
            var pullId = await CreatePullAsync(service);

            var failClosed = CreateService(null, null, pullCache, new RecordingAuditStore { ThrowOnWrite = true }, null, null);
            var apply = failClosed.ApplyResult(new JObject { ["pull_id"] = pullId, ["outcome"] = "ok" });
            Assert.False(apply.Ok);
            Assert.Equal(ErrorCodes.AuditWriteFailed, apply.Code);

            var warnings = new RecordingAuditWarnings();
            var failOpen = CreateService(null, null, null, new RecordingAuditStore { ThrowOnWrite = true }, null, warnings);

            Assert.True((await failOpen.DiffPreviewAsync(new JObject { ["item_id"] = "item-1" }, CancellationToken.None)).Ok);
            Assert.True((await failOpen.SyncInboundAsync(new JObject { ["item_id"] = "item-1" }, CancellationToken.None)).Ok);
            Assert.True((await failOpen.SyncOutboundAsync(new JObject { ["item_id"] = "item-1" }, CancellationToken.None)).Ok);

            Assert.Equal(3, warnings.Lines.Count);
            Assert.All(warnings.Lines, line =>
            {
                Assert.Contains("[AUDIT_WRITE_FAILED]", line);
                Assert.DoesNotContain("bearer-secret", line);
                Assert.DoesNotContain("item-1", line);
            });
        }

        [Fact]
        public void test_s6_does_not_add_dedup_shell_reset_or_later_routes()
        {
            var sources = ReadHelperSources();

            Assert.DoesNotContain("/dedup/check", sources);
            Assert.DoesNotContain("/shell/notify", sources);
            Assert.DoesNotContain("/compose", sources);
            Assert.DoesNotContain("/validate", sources);
            Assert.DoesNotContain("/tasks", sources);
            Assert.DoesNotContain("/diagnostics/snapshot", sources);
            Assert.DoesNotContain("--reset-local-token", sources);
            Assert.DoesNotContain("CADDedupPlugin", sources);
            Assert.DoesNotContain("UseCors", sources);
            Assert.DoesNotContain("context.Request.Headers[\"Authorization\"]", sources);
        }

        [Fact]
        public void test_s6_keeps_cad_helper_dotnet_workflow_covering_helper_tests()
        {
            var workflow = File.ReadAllText(Path.Combine(FindRepoRoot(), ".github", "workflows", "cad-helper-shared-dotnet.yml"));

            Assert.Contains("clients/cad-desktop-helper/Helper/**", workflow);
            Assert.Contains("clients/cad-desktop-helper/Helper.Tests/**", workflow);
            Assert.Contains("dotnet test clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj", workflow);
        }

        private static HelperBusinessAuditService CreateService(RecordingBusinessClient plm)
        {
            return CreateService(null, null, null, null, null, null, plm);
        }

        private static HelperBusinessAuditService CreateService(
            InMemoryConfigStore config,
            InMemoryBearerStore bearer,
            PullCache pullCache,
            RecordingAuditStore audit,
            FakeClock clock,
            RecordingAuditWarnings warnings)
        {
            return CreateService(config, bearer, pullCache, audit, clock, warnings, null);
        }

        private static HelperBusinessAuditService CreateService(
            InMemoryConfigStore config,
            InMemoryBearerStore bearer,
            PullCache pullCache,
            RecordingAuditStore audit,
            FakeClock clock,
            RecordingAuditWarnings warnings,
            RecordingBusinessClient plm)
        {
            return new HelperBusinessAuditService(
                config ?? new InMemoryConfigStore { ServerUrl = "https://plm.example.com/api/v1", TenantId = "tenant-a" },
                bearer ?? new InMemoryBearerStore { Token = "bearer-secret" },
                plm ?? new RecordingBusinessClient(),
                pullCache ?? new PullCache(),
                audit ?? new RecordingAuditStore(),
                clock ?? new FakeClock(DateTimeOffset.Parse("2026-05-22T10:00:00Z")),
                warnings ?? new RecordingAuditWarnings());
        }

        private static async Task<string> CreatePullAsync(HelperBusinessAuditService service)
        {
            var result = await service.DiffPreviewAsync(new JObject { ["item_id"] = "item-1" }, CancellationToken.None);
            Assert.True(result.Ok);
            return ((JObject)result.Data).Value<string>("pull_id");
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

        private static IOriginProcessResolver AllowedOrigin()
        {
            return new FakeOriginResolver(new OriginProcess(true, "acad.exe", @"C:\Program Files\Autodesk\AutoCAD 2024\acad.exe"));
        }

        private static string[] ReadColumnNames(SqliteConnection connection, string tableName)
        {
            using (var command = connection.CreateCommand())
            {
                command.CommandText = "PRAGMA table_info(" + tableName + ")";
                using (var reader = command.ExecuteReader())
                {
                    var names = new List<string>();
                    while (reader.Read())
                    {
                        names.Add(reader.GetString(1));
                    }
                    return names.ToArray();
                }
            }
        }

        private static string[] ReadIndexNames(SqliteConnection connection)
        {
            using (var command = connection.CreateCommand())
            {
                command.CommandText = "PRAGMA index_list(audit_events)";
                using (var reader = command.ExecuteReader())
                {
                    var names = new List<string>();
                    while (reader.Read())
                    {
                        names.Add(reader.GetString(1));
                    }
                    return names.ToArray();
                }
            }
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

        private sealed class InMemoryConfigStore : IHelperSessionConfigStore
        {
            public string ServerUrl;
            public string TenantId;
            public string OrgId;
            public string DefaultProfileId;

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
                return new string[0];
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

        private sealed class RecordingBusinessClient : IPlmBusinessClient
        {
            public RecordingBusinessClient()
            {
                Response = PlmBusinessResponse.Success(new JObject
                {
                    ["ok"] = true,
                    ["write_cad_fields"] = new JObject { ["MAT"] = "AL6061" },
                    ["cad_fields"] = new JObject { ["MAT"] = "AL6061" }
                });
            }

            public PlmBusinessResponse Response { get; set; }
            public List<BusinessCall> Calls { get; private set; } = new List<BusinessCall>();

            public Task<PlmBusinessResponse> PostAsync(Uri serverUri, string endpointPath, string bearerToken, string traceId, JObject payload, CancellationToken cancellationToken)
            {
                Calls.Add(new BusinessCall
                {
                    ServerUri = serverUri,
                    EndpointPath = endpointPath,
                    BearerToken = bearerToken,
                    TraceId = traceId,
                    ProtocolVersion = Paths.ProtocolVersion,
                    Payload = payload == null ? new JObject() : (JObject)payload.DeepClone()
                });
                return Task.FromResult(Response);
            }
        }

        private sealed class BusinessCall
        {
            public Uri ServerUri { get; set; }
            public string EndpointPath { get; set; }
            public string BearerToken { get; set; }
            public string TraceId { get; set; }
            public string ProtocolVersion { get; set; }
            public JObject Payload { get; set; }
        }

        private sealed class RecordingAuditStore : IAuditEventStore
        {
            public bool ThrowOnWrite { get; set; }
            public List<AuditEvent> Events { get; private set; } = new List<AuditEvent>();

            public void Write(AuditEvent auditEvent)
            {
                if (ThrowOnWrite)
                {
                    throw new IOException("audit failed");
                }
                Events.Add(auditEvent);
            }
        }

        private sealed class RecordingAuditWarnings : IAuditWarningWriter
        {
            public List<string> Lines { get; private set; } = new List<string>();

            public void WriteAuditFailure(string endpoint, string traceId, string reason)
            {
                Lines.Add("[AUDIT_WRITE_FAILED] endpoint=" + endpoint + " trace_id=" + traceId + " reason=" + reason);
            }
        }

        private sealed class FakeClock : IClock
        {
            public FakeClock(DateTimeOffset utcNow)
            {
                UtcNow = utcNow;
            }

            public DateTimeOffset UtcNow { get; private set; }

            public void Advance(TimeSpan delta)
            {
                UtcNow = UtcNow.Add(delta);
            }
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
                return new TempWorkspace(Path.Combine(Path.GetTempPath(), "yuantus-cad-helper-s6-tests-" + Guid.NewGuid().ToString("N")));
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
