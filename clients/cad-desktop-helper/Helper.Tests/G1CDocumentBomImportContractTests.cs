using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using Xunit;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Helper.Tests
{
    // G1-C BOM import (Path A: reuse the async /cad/import pipeline). Taskbook
    // DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_G1_C_BOM_IMPORT_20260526.
    // Pins (taskbook §6):
    //   - uniform session gate: missing session -> AuthPlmNotLoggedIn, ZERO backend calls;
    //   - multipart seam forwards to /cad/import with create_bom_job=true (not JSON PostAsync);
    //   - root-item policy (§4.A): item_id present -> forward item_id; absent -> auto_create_part=true;
    //   - response shaping: select the cad_bom job from CadImportResponse.jobs; error if absent.
    public sealed class G1CDocumentBomImportContractTests
    {
        private const string ServerUrl = "https://plm.example.com/api/v1";
        private const string Bearer = "bearer-secret";

        [Fact]
        public async Task test_g1c_bom_import_requires_plm_session_before_backend_call()
        {
            var plm = new RecordingImportClient();
            var service = CreateService(plm, bearerToken: null);

            var result = await service.DocumentBomImportAsync("item-1", new byte[] { 1, 2, 3 }, "asm.dwg", CancellationToken.None);

            Assert.False(result.Ok);
            Assert.Equal(ErrorCodes.AuthPlmNotLoggedIn, result.Code);
            Assert.Empty(plm.Calls);
            Assert.Equal(0, plm.JsonPostCount);
        }

        [Fact]
        public async Task test_g1c_bom_import_requires_file()
        {
            var plm = new RecordingImportClient();
            var service = CreateService(plm, bearerToken: Bearer);

            Assert.Equal(ErrorCodes.HelperInputValidationFailed,
                (await service.DocumentBomImportAsync("item-1", new byte[0], "asm.dwg", CancellationToken.None)).Code);
            Assert.Equal(ErrorCodes.HelperInputValidationFailed,
                (await service.DocumentBomImportAsync("item-1", new byte[] { 1 }, "", CancellationToken.None)).Code);
            Assert.Empty(plm.Calls);
        }

        [Fact]
        public async Task test_g1c_bom_import_forwards_multipart_to_cad_import_with_create_bom_job()
        {
            var plm = new RecordingImportClient();
            var service = CreateService(plm, bearerToken: Bearer);

            var result = await service.DocumentBomImportAsync("item-1", new byte[] { 9, 8, 7 }, "asm.dwg", CancellationToken.None);

            Assert.True(result.Ok);
            var call = Assert.Single(plm.Calls);
            Assert.Equal("/cad/import", call.EndpointPath);
            Assert.Equal(Bearer, call.BearerToken);
            Assert.Equal(new byte[] { 9, 8, 7 }, call.FileContent);
            Assert.NotNull(call.FormFields);
            Assert.Equal("true", call.FormFields["create_bom_job"]);
            // Multipart seam, not the JSON PostAsync.
            Assert.Equal(0, plm.JsonPostCount);
        }

        [Fact]
        public async Task test_g1c_bom_import_root_item_policy_item_id_or_auto_create()
        {
            // With item_id: forward item_id, do NOT auto-create.
            var withId = new RecordingImportClient();
            await CreateService(withId, Bearer).DocumentBomImportAsync("item-1", new byte[] { 1 }, "a.dwg", CancellationToken.None);
            var c1 = Assert.Single(withId.Calls);
            Assert.Equal("item-1", c1.FormFields["item_id"]);
            Assert.False(c1.FormFields.ContainsKey("auto_create_part"));

            // Without item_id: forward auto_create_part=true, no item_id.
            var noId = new RecordingImportClient();
            await CreateService(noId, Bearer).DocumentBomImportAsync("", new byte[] { 1 }, "a.dwg", CancellationToken.None);
            var c2 = Assert.Single(noId.Calls);
            Assert.Equal("true", c2.FormFields["auto_create_part"]);
            Assert.False(c2.FormFields.ContainsKey("item_id"));
        }

        [Fact]
        public async Task test_g1c_bom_import_returns_cad_bom_job_handle_and_file_id()
        {
            var plm = new RecordingImportClient
            {
                Response = PlmBusinessResponse.Success(new JObject
                {
                    ["file_id"] = "file-1",
                    ["jobs"] = new JArray
                    {
                        new JObject { ["id"] = "j-prev", ["task_type"] = "cad_preview", ["status"] = "pending" },
                        new JObject { ["id"] = "j-bom", ["task_type"] = "cad_bom", ["status"] = "pending" }
                    }
                })
            };
            var service = CreateService(plm, bearerToken: Bearer);

            var result = await service.DocumentBomImportAsync("item-1", new byte[] { 1 }, "a.dwg", CancellationToken.None);

            Assert.True(result.Ok);
            var data = Assert.IsType<JObject>(result.Data);
            Assert.Equal("file-1", data.Value<string>("file_id"));
            var job = (JObject)data["job"];
            Assert.Equal("j-bom", job.Value<string>("id"));
            Assert.Equal("cad_bom", job.Value<string>("task_type"));
        }

        [Fact]
        public async Task test_g1c_bom_import_errors_when_no_cad_bom_job_returned()
        {
            var plm = new RecordingImportClient
            {
                Response = PlmBusinessResponse.Success(new JObject
                {
                    ["file_id"] = "file-1",
                    ["jobs"] = new JArray
                    {
                        new JObject { ["id"] = "j-prev", ["task_type"] = "cad_preview", ["status"] = "pending" }
                    }
                })
            };
            var service = CreateService(plm, bearerToken: Bearer);

            var result = await service.DocumentBomImportAsync("item-1", new byte[] { 1 }, "a.dwg", CancellationToken.None);

            Assert.False(result.Ok);
            Assert.Equal(ErrorCodes.PlmValidationFailed, result.Code);
        }

        private static HelperBusinessAuditService CreateService(RecordingImportClient plm, string bearerToken)
        {
            return new HelperBusinessAuditService(
                new InMemoryConfigStore { ServerUrl = ServerUrl, TenantId = "tenant-a" },
                new InMemoryBearerStore { Token = bearerToken },
                plm,
                new PullCache(),
                new RecordingAuditStore(),
                new FakeClock(DateTimeOffset.Parse("2026-05-26T10:00:00Z")),
                new RecordingAuditWarnings());
        }

        private sealed class ImportCall
        {
            public string EndpointPath { get; set; }
            public string BearerToken { get; set; }
            public byte[] FileContent { get; set; }
            public string FileName { get; set; }
            public IDictionary<string, string> FormFields { get; set; }
        }

        private sealed class RecordingImportClient : IPlmBusinessClient
        {
            public RecordingImportClient()
            {
                Response = PlmBusinessResponse.Success(new JObject
                {
                    ["file_id"] = "file-1",
                    ["jobs"] = new JArray
                    {
                        new JObject { ["id"] = "j-bom", ["task_type"] = "cad_bom", ["status"] = "pending" }
                    }
                });
            }

            public PlmBusinessResponse Response { get; set; }
            public List<ImportCall> Calls { get; private set; } = new List<ImportCall>();
            public int JsonPostCount { get; private set; }
            public int GetCount { get; private set; }

            public Task<PlmBusinessResponse> PostAsync(Uri serverUri, string endpointPath, string bearerToken, string traceId, JObject payload, CancellationToken cancellationToken)
            {
                JsonPostCount++;
                return Task.FromResult(Response);
            }

            public Task<PlmBusinessResponse> GetAsync(Uri serverUri, string endpointPath, string bearerToken, string traceId, CancellationToken cancellationToken)
            {
                GetCount++;
                return Task.FromResult(Response);
            }

            public Task<PlmBusinessResponse> PostMultipartAsync(Uri serverUri, string endpointPath, string bearerToken, string traceId, byte[] fileContent, string fileName, IDictionary<string, string> formFields, CancellationToken cancellationToken)
            {
                Calls.Add(new ImportCall
                {
                    EndpointPath = endpointPath,
                    BearerToken = bearerToken,
                    FileContent = fileContent,
                    FileName = fileName,
                    FormFields = formFields
                });
                return Task.FromResult(Response);
            }
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

        private sealed class RecordingAuditStore : IAuditEventStore
        {
            public List<AuditEvent> Events { get; private set; } = new List<AuditEvent>();

            public void Write(AuditEvent auditEvent)
            {
                Events.Add(auditEvent);
            }
        }

        private sealed class RecordingAuditWarnings : IAuditWarningWriter
        {
            public List<string> Lines { get; private set; } = new List<string>();

            public void WriteAuditFailure(string endpoint, string traceId, string reason)
            {
                Lines.Add(endpoint + "|" + traceId + "|" + reason);
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
    }
}
