using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using Xunit;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Helper.Tests
{
    // G1-A document lock routes (taskbook
    // DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_G1_A_CHECKOUT_STATUS_ROUTES_20260525).
    // Pins the two highest-risk boundaries (taskbook 6.A / 6.B):
    //   - uniform PLM session gate: a missing session short-circuits with
    //     AuthPlmNotLoggedIn and makes ZERO backend calls for all three routes;
    //   - forwarding shape: checkout/undo -> backend POST, status -> backend GET
    //     with no body, bearer forwarded.
    // No audit is asserted: G1-A is a pure proxy (taskbook 3.B).
    public sealed class G1ADocumentLockRoutesContractTests
    {
        private const string ServerUrl = "https://plm.example.com/api/v1";
        private const string Bearer = "bearer-secret";

        [Fact]
        public async Task test_g1a_document_routes_require_plm_session_before_backend_call()
        {
            // Valid tenant/server config but NO bearer -> the gate fires on the
            // bearer check (AuthPlmNotLoggedIn), before any backend call.
            var plm = new RecordingDocumentClient();
            var service = CreateService(plm, bearerToken: null);
            var request = new JObject { ["item_id"] = "item-1" };

            var checkout = await service.DocumentCheckoutAsync(request, CancellationToken.None);
            var undo = await service.DocumentUndoCheckoutAsync(request, CancellationToken.None);
            var status = await service.DocumentStatusAsync(request, CancellationToken.None);

            foreach (var result in new[] { checkout, undo, status })
            {
                Assert.False(result.Ok);
                Assert.Equal(ErrorCodes.AuthPlmNotLoggedIn, result.Code);
            }
            // The short-circuit is the point.
            Assert.Empty(plm.Calls);
        }

        [Fact]
        public async Task test_g1a_checkout_forwards_post_to_cad_checkout_with_bearer()
        {
            var plm = new RecordingDocumentClient();
            var service = CreateService(plm, bearerToken: Bearer);

            var result = await service.DocumentCheckoutAsync(new JObject { ["item_id"] = "item-1" }, CancellationToken.None);

            Assert.True(result.Ok);
            var call = Assert.Single(plm.Calls);
            Assert.Equal("POST", call.Verb);
            Assert.Equal("/cad/item-1/checkout", call.EndpointPath);
            Assert.Equal(Bearer, call.BearerToken);
        }

        [Fact]
        public async Task test_g1a_undo_checkout_forwards_post_to_cad_undo_checkout_with_bearer()
        {
            var plm = new RecordingDocumentClient();
            var service = CreateService(plm, bearerToken: Bearer);

            var result = await service.DocumentUndoCheckoutAsync(new JObject { ["item_id"] = "item-1" }, CancellationToken.None);

            Assert.True(result.Ok);
            var call = Assert.Single(plm.Calls);
            Assert.Equal("POST", call.Verb);
            Assert.Equal("/cad/item-1/undo-checkout", call.EndpointPath);
            Assert.Equal(Bearer, call.BearerToken);
        }

        [Fact]
        public async Task test_g1a_status_forwards_get_to_cad_checkin_status_without_body()
        {
            var plm = new RecordingDocumentClient();
            var service = CreateService(plm, bearerToken: Bearer);

            var result = await service.DocumentStatusAsync(new JObject { ["item_id"] = "item-1" }, CancellationToken.None);

            Assert.True(result.Ok);
            var call = Assert.Single(plm.Calls);
            Assert.Equal("GET", call.Verb);
            Assert.Equal("/cad/item-1/checkin-status", call.EndpointPath);
            Assert.Equal(Bearer, call.BearerToken);
            // GET carries no request body (guards against hard-casting POST-only).
            Assert.Null(call.Payload);
        }

        [Fact]
        public async Task test_g1a_document_routes_require_item_id()
        {
            var plm = new RecordingDocumentClient();
            var service = CreateService(plm, bearerToken: Bearer);
            var empty = new JObject();

            var checkout = await service.DocumentCheckoutAsync(empty, CancellationToken.None);
            var undo = await service.DocumentUndoCheckoutAsync(empty, CancellationToken.None);
            var status = await service.DocumentStatusAsync(empty, CancellationToken.None);

            foreach (var result in new[] { checkout, undo, status })
            {
                Assert.False(result.Ok);
                Assert.Equal(ErrorCodes.HelperInputValidationFailed, result.Code);
            }
            Assert.Empty(plm.Calls);
        }

        private static HelperBusinessAuditService CreateService(RecordingDocumentClient plm, string bearerToken)
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

        private sealed class DocumentCall
        {
            public string Verb { get; set; }
            public Uri ServerUri { get; set; }
            public string EndpointPath { get; set; }
            public string BearerToken { get; set; }
            public JObject Payload { get; set; }
        }

        private sealed class RecordingDocumentClient : IPlmBusinessClient
        {
            public RecordingDocumentClient()
            {
                Response = PlmBusinessResponse.Success(new JObject { ["ok"] = true });
            }

            public PlmBusinessResponse Response { get; set; }
            public List<DocumentCall> Calls { get; private set; } = new List<DocumentCall>();

            public Task<PlmBusinessResponse> PostAsync(Uri serverUri, string endpointPath, string bearerToken, string traceId, JObject payload, CancellationToken cancellationToken)
            {
                Calls.Add(new DocumentCall
                {
                    Verb = "POST",
                    ServerUri = serverUri,
                    EndpointPath = endpointPath,
                    BearerToken = bearerToken,
                    Payload = payload
                });
                return Task.FromResult(Response);
            }

            public Task<PlmBusinessResponse> GetAsync(Uri serverUri, string endpointPath, string bearerToken, string traceId, CancellationToken cancellationToken)
            {
                Calls.Add(new DocumentCall
                {
                    Verb = "GET",
                    ServerUri = serverUri,
                    EndpointPath = endpointPath,
                    BearerToken = bearerToken,
                    Payload = null
                });
                return Task.FromResult(Response);
            }

            public Task<PlmBusinessResponse> PostMultipartAsync(Uri serverUri, string endpointPath, string bearerToken, string traceId, byte[] fileContent, string fileName, IDictionary<string, string> formFields, CancellationToken cancellationToken)
            {
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
