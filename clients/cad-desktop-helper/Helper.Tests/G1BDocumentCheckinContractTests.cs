using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using Xunit;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Helper.Tests
{
    // G1-B document checkin (multipart upload bridge). Taskbook
    // DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_G1_B_CHECKIN_MULTIPART_20260526.
    // Pins the highest-risk boundaries (taskbook §6):
    //   - uniform PLM session gate: missing session -> AuthPlmNotLoggedIn, ZERO backend calls;
    //   - forwarding shape: the MULTIPART seam is invoked (not JSON PostAsync), to /cad/{id}/checkin;
    //   - quota: hard 429 maps to ok=false + ErrorCodes.QuotaExceeded + error.details.quota
    //     (NOT PLM_VALIDATION_FAILED); soft maps to ok=true + data.quota_warning.
    // The wire-shape tests call HelperRouteResponse.ToJson(...) and parse the envelope,
    // since that is the only place the error.details / data.quota_warning shape is realized.
    public sealed class G1BDocumentCheckinContractTests
    {
        private const string ServerUrl = "https://plm.example.com/api/v1";
        private const string Bearer = "bearer-secret";

        [Fact]
        public async Task test_g1b_checkin_requires_plm_session_before_backend_call()
        {
            // Valid config + valid file, but NO bearer -> gate fires on bearer, not validation.
            var plm = new RecordingMultipartClient();
            var service = CreateService(plm, bearerToken: null);

            var result = await service.DocumentCheckinAsync("item-1", new byte[] { 1, 2, 3 }, "part.dwg", CancellationToken.None);

            Assert.False(result.Ok);
            Assert.Equal(ErrorCodes.AuthPlmNotLoggedIn, result.Code);
            Assert.Empty(plm.Calls);
            Assert.Equal(0, plm.JsonPostCount);
            Assert.Equal(0, plm.GetCount);
        }

        [Fact]
        public async Task test_g1b_checkin_forwards_multipart_post_to_cad_checkin_with_bearer()
        {
            var plm = new RecordingMultipartClient();
            var service = CreateService(plm, bearerToken: Bearer);

            var result = await service.DocumentCheckinAsync("item-1", new byte[] { 9, 8, 7 }, "part.dwg", CancellationToken.None);

            Assert.True(result.Ok);
            var call = Assert.Single(plm.Calls);
            Assert.Equal("/cad/item-1/checkin", call.EndpointPath);
            Assert.Equal(Bearer, call.BearerToken);
            Assert.Equal("part.dwg", call.FileName);
            Assert.Equal(new byte[] { 9, 8, 7 }, call.FileContent);
            // Multipart seam, NOT the JSON PostAsync.
            Assert.Equal(0, plm.JsonPostCount);
        }

        [Fact]
        public async Task test_g1b_checkin_requires_item_id_and_file()
        {
            var plm = new RecordingMultipartClient();
            var service = CreateService(plm, bearerToken: Bearer);

            Assert.Equal(ErrorCodes.HelperInputValidationFailed,
                (await service.DocumentCheckinAsync("", new byte[] { 1 }, "f.dwg", CancellationToken.None)).Code);
            Assert.Equal(ErrorCodes.HelperInputValidationFailed,
                (await service.DocumentCheckinAsync("item-1", new byte[0], "f.dwg", CancellationToken.None)).Code);
            Assert.Equal(ErrorCodes.HelperInputValidationFailed,
                (await service.DocumentCheckinAsync("item-1", new byte[] { 1 }, "", CancellationToken.None)).Code);
            Assert.Empty(plm.Calls);
        }

        [Fact]
        public async Task test_g1b_checkin_maps_hard_quota_429_to_quota_envelope_not_validation_failed()
        {
            var quota = new JObject { ["code"] = "QUOTA_EXCEEDED", ["limit"] = "files", ["max"] = 100 };
            var plm = new RecordingMultipartClient
            {
                Response = PlmBusinessResponse.Error(ErrorCodes.QuotaExceeded, "PLM checkin denied by quota.", quota)
            };
            var service = CreateService(plm, bearerToken: Bearer);

            var result = await service.DocumentCheckinAsync("item-1", new byte[] { 1 }, "f.dwg", CancellationToken.None);

            Assert.False(result.Ok);
            Assert.Equal(ErrorCodes.QuotaExceeded, result.Code);
            Assert.NotEqual(ErrorCodes.PlmValidationFailed, result.Code);
            Assert.NotNull(result.Details);
            Assert.True(result.Details.ContainsKey("quota"));
        }

        [Fact]
        public async Task test_g1b_checkin_surfaces_soft_quota_warning_in_success_envelope()
        {
            var data = new JObject { ["version_id"] = "v2" };
            var plm = new RecordingMultipartClient
            {
                Response = PlmBusinessResponse.Success(data, "storage at 85%")
            };
            var service = CreateService(plm, bearerToken: Bearer);

            var result = await service.DocumentCheckinAsync("item-1", new byte[] { 1 }, "f.dwg", CancellationToken.None);

            Assert.True(result.Ok);
            var resultData = Assert.IsType<JObject>(result.Data);
            Assert.Equal("storage at 85%", resultData.Value<string>("quota_warning"));
        }

        // ----- wire-shape (envelope) tests via HelperRouteResponse.ToJson -----

        [Fact]
        public void test_g1b_hard_quota_envelope_contains_error_details_quota()
        {
            var quota = new JObject { ["limit"] = "files" };
            var result = HelperRouteResult.Error(
                ErrorCodes.QuotaExceeded, "denied", new Dictionary<string, object> { ["quota"] = quota });

            var parsed = JObject.Parse(HelperRouteResponse.ToJson(result));

            Assert.False(parsed.Value<bool>("ok"));
            Assert.Equal("QUOTA_EXCEEDED", parsed["error"].Value<string>("code"));
            Assert.NotNull(parsed["error"]["details"]["quota"]);
            Assert.Equal("files", parsed["error"]["details"]["quota"].Value<string>("limit"));
        }

        [Fact]
        public void test_g1b_soft_quota_envelope_contains_data_quota_warning()
        {
            var result = HelperRouteResult.Success(new JObject
            {
                ["version_id"] = "v2",
                ["quota_warning"] = "storage at 85%"
            });

            var parsed = JObject.Parse(HelperRouteResponse.ToJson(result));

            Assert.True(parsed.Value<bool>("ok"));
            Assert.Equal("storage at 85%", parsed["data"].Value<string>("quota_warning"));
        }

        [Fact]
        public void test_g1b_existing_error_responses_still_emit_empty_details_object()
        {
            // Backward compat: an Error built without details still serializes error.details == {}.
            var result = HelperRouteResult.Error(ErrorCodes.PlmValidationFailed, "boom");

            var parsed = JObject.Parse(HelperRouteResponse.ToJson(result));

            Assert.NotNull(parsed["error"]["details"]);
            Assert.Empty((JObject)parsed["error"]["details"]);
        }

        private static HelperBusinessAuditService CreateService(RecordingMultipartClient plm, string bearerToken)
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

        private sealed class MultipartCall
        {
            public string EndpointPath { get; set; }
            public string BearerToken { get; set; }
            public byte[] FileContent { get; set; }
            public string FileName { get; set; }
        }

        private sealed class RecordingMultipartClient : IPlmBusinessClient
        {
            public RecordingMultipartClient()
            {
                Response = PlmBusinessResponse.Success(new JObject { ["version_id"] = "v2", ["generation"] = 2 });
            }

            public PlmBusinessResponse Response { get; set; }
            public List<MultipartCall> Calls { get; private set; } = new List<MultipartCall>();
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
                Calls.Add(new MultipartCall
                {
                    EndpointPath = endpointPath,
                    BearerToken = bearerToken,
                    FileContent = fileContent,
                    FileName = fileName
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
