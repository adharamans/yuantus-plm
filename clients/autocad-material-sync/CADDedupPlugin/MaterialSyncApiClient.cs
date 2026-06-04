// MaterialSyncApiClient.cs - Yuantus CAD 物料同步 API 客户端
using System;
using System.Collections.Generic;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Yuantus.Cad.Shared.Discovery;
using Yuantus.Cad.Shared.Transport;

namespace CADDedupPlugin
{
    /// <summary>
    /// 调用 Yuantus PLM cad-material-sync 插件。
    /// </summary>
    public class MaterialSyncApiClient
    {
        private const string BasePath = "/api/v1/plugins/cad-material-sync";
        private const string CadSystem = "autocad";

        private readonly HttpClient _httpClient;
        private readonly IMaterialSyncHelperTransport _helperTransport;
        private DedupConfig _config;

        public MaterialSyncApiClient(DedupConfig config)
            : this(config, new SharedMaterialSyncHelperTransport())
        {
        }

        public MaterialSyncApiClient(DedupConfig config, IMaterialSyncHelperTransport helperTransport)
        {
            _config = config;
            _helperTransport = helperTransport ?? throw new ArgumentNullException(nameof(helperTransport));
            _httpClient = new HttpClient
            {
                BaseAddress = new Uri(_config.ServerUrl),
                Timeout = TimeSpan.FromSeconds(_config.TimeoutSeconds)
            };
            ConfigureHeaders();
        }

        public void UpdateConfig(DedupConfig config)
        {
            _config = config;
            _httpClient.BaseAddress = new Uri(_config.ServerUrl);
            _httpClient.Timeout = TimeSpan.FromSeconds(_config.TimeoutSeconds);
            ConfigureHeaders();
        }

        public async Task<MaterialProfilesResponse> GetProfilesAsync()
        {
            var request = CreateRequest(HttpMethod.Get, $"{BasePath}/profiles");
            var response = await _httpClient.SendAsync(request);
            return await ReadJsonAsync<MaterialProfilesResponse>(response);
        }

        public async Task<MaterialProfileResponse> GetProfileAsync(string profileId)
        {
            var safeProfileId = Uri.EscapeDataString(profileId ?? _config.MaterialProfileId ?? "sheet");
            var request = CreateRequest(HttpMethod.Get, $"{BasePath}/profiles/{safeProfileId}");
            var response = await _httpClient.SendAsync(request);
            return await ReadJsonAsync<MaterialProfileResponse>(response);
        }

        public async Task<MaterialSyncResponse> ComposeAsync(
            string profileId,
            Dictionary<string, object> values)
        {
            var payload = new
            {
                profile_id = profileId,
                values = values ?? new Dictionary<string, object>(),
                include_cad_fields = true,
                cad_system = CadSystem
            };
            var response = await _httpClient.SendAsync(
                CreateJsonRequest(HttpMethod.Post, $"{BasePath}/compose", payload));
            return await ReadJsonAsync<MaterialSyncResponse>(response);
        }

        public async Task<MaterialSyncResponse> ValidateAsync(
            string profileId,
            Dictionary<string, object> values,
            bool lookupExisting)
        {
            var payload = new
            {
                profile_id = profileId,
                values = values ?? new Dictionary<string, object>(),
                lookup_existing = lookupExisting,
                cad_system = CadSystem
            };
            var response = await _httpClient.SendAsync(
                CreateJsonRequest(HttpMethod.Post, $"{BasePath}/validate", payload));
            return await ReadJsonAsync<MaterialSyncResponse>(response);
        }

        public async Task<MaterialSyncResponse> SyncInboundAsync(
            string profileId,
            Dictionary<string, object> cadFields,
            bool dryRun,
            bool overwrite,
            bool createIfMissing)
        {
            var payload = new
            {
                profile_id = profileId,
                cad_fields = cadFields ?? new Dictionary<string, object>(),
                dry_run = dryRun,
                overwrite = overwrite,
                create_if_missing = createIfMissing,
                cad_system = CadSystem
            };
            return await _helperTransport.PostJsonAsync<MaterialSyncResponse>(
                "/sync/inbound",
                payload,
                CancellationToken.None);
        }

        public async Task<MaterialSyncResponse> SyncOutboundAsync(
            string profileId,
            string itemId,
            bool includeEmpty)
        {
            var payload = new
            {
                profile_id = profileId,
                item_id = itemId,
                include_empty = includeEmpty,
                cad_system = CadSystem
            };
            return await _helperTransport.PostJsonAsync<MaterialSyncResponse>(
                "/sync/outbound",
                payload,
                CancellationToken.None);
        }

        public async Task<MaterialDiffPreviewResponse> DiffPreviewAsync(
            string profileId,
            string itemId,
            Dictionary<string, object> currentCadFields,
            bool includeEmpty)
        {
            var payload = new
            {
                profile_id = profileId,
                item_id = itemId,
                current_cad_fields = currentCadFields ?? new Dictionary<string, object>(),
                include_empty = includeEmpty,
                cad_system = CadSystem
            };
            var helperResponse = await _helperTransport.PostJsonAsync<HelperDiffPreviewResponse>(
                "/diff/preview",
                payload,
                CancellationToken.None);
            var preview = helperResponse == null || helperResponse.ServerResponse == null
                ? new MaterialDiffPreviewResponse()
                : helperResponse.ServerResponse.ToObject<MaterialDiffPreviewResponse>();
            if (preview == null)
            {
                preview = new MaterialDiffPreviewResponse();
            }
            preview.PullId = helperResponse == null ? null : helperResponse.PullId;
            return preview;
        }

        // Phase 3: material assistant. Helper-forwarded (not direct to PLM); the
        // helper proxies to /plugins/cad-material-sync/assistant/*. resolve is
        // read-only, create is confirm-gated by the caller (PLMMATASSIST).
        public async Task<MaterialAssistantResolveResponse> ResolveAsync(
            string profileId,
            Dictionary<string, object> cadFields,
            Dictionary<string, object> values)
        {
            var payload = new
            {
                profile_id = profileId,
                cad_fields = cadFields ?? new Dictionary<string, object>(),
                values = values ?? new Dictionary<string, object>(),
                cad_system = CadSystem
            };
            return await _helperTransport.PostJsonAsync<MaterialAssistantResolveResponse>(
                "/material/assistant/resolve",
                payload,
                CancellationToken.None);
        }

        public async Task<MaterialAssistantCreateResponse> CreateAsync(
            string profileId,
            Dictionary<string, object> properties,
            Dictionary<string, object> cadFields,
            Dictionary<string, object> values)
        {
            var payload = new
            {
                profile_id = profileId,
                properties = properties ?? new Dictionary<string, object>(),
                cad_fields = cadFields ?? new Dictionary<string, object>(),
                values = values ?? new Dictionary<string, object>()
            };
            return await _helperTransport.PostJsonAsync<MaterialAssistantCreateResponse>(
                "/material/assistant/create",
                payload,
                CancellationToken.None);
        }

        public Task<JObject> ReportApplyResultAsync(
            string pullId,
            string outcome,
            Dictionary<string, object> appliedFields,
            Dictionary<string, object> failedFields,
            string drawingFilename,
            string drawingFilepath,
            int durationMs)
        {
            var payload = new
            {
                pull_id = pullId,
                outcome = outcome,
                applied_fields = appliedFields ?? new Dictionary<string, object>(),
                failed_fields = failedFields ?? new Dictionary<string, object>(),
                drawing = new
                {
                    filename = drawingFilename,
                    filepath = drawingFilepath
                },
                cad_system = CadSystem,
                duration_ms = durationMs
            };
            return _helperTransport.PostJsonAsync<JObject>(
                "/audit/apply-result",
                payload,
                CancellationToken.None);
        }

        private void ConfigureHeaders()
        {
            _httpClient.DefaultRequestHeaders.Clear();
            if (!string.IsNullOrWhiteSpace(_config.ApiKey))
            {
                _httpClient.DefaultRequestHeaders.Authorization =
                    new AuthenticationHeaderValue("Bearer", _config.ApiKey);
            }
            if (!string.IsNullOrWhiteSpace(_config.TenantId))
            {
                _httpClient.DefaultRequestHeaders.Add("x-tenant-id", _config.TenantId);
            }
            if (!string.IsNullOrWhiteSpace(_config.OrgId))
            {
                _httpClient.DefaultRequestHeaders.Add("x-org-id", _config.OrgId);
            }
        }

        private HttpRequestMessage CreateRequest(HttpMethod method, string path)
        {
            return new HttpRequestMessage(method, path);
        }

        private HttpRequestMessage CreateJsonRequest(HttpMethod method, string path, object payload)
        {
            var request = CreateRequest(method, path);
            var json = JsonConvert.SerializeObject(payload);
            request.Content = new StringContent(json, Encoding.UTF8, "application/json");
            return request;
        }

        private static async Task<T> ReadJsonAsync<T>(HttpResponseMessage response)
        {
            var body = await response.Content.ReadAsStringAsync();
            if (!response.IsSuccessStatusCode)
            {
                throw new Exception($"PLM 物料同步 API 失败: {(int)response.StatusCode} {body}");
            }
            return JsonConvert.DeserializeObject<T>(body);
        }
    }

    public class MaterialProfilesResponse
    {
        [JsonProperty("ok")]
        public bool Ok { get; set; }

        [JsonProperty("profiles")]
        public List<MaterialProfile> Profiles { get; set; } = new List<MaterialProfile>();
    }

    public class MaterialProfileResponse
    {
        [JsonProperty("ok")]
        public bool Ok { get; set; }

        [JsonProperty("profile")]
        public MaterialProfile Profile { get; set; }
    }

    public class MaterialProfile
    {
        [JsonProperty("profile_id")]
        public string ProfileId { get; set; }

        [JsonProperty("label")]
        public string Label { get; set; }

        [JsonProperty("fields")]
        public List<MaterialFieldDefinition> Fields { get; set; } = new List<MaterialFieldDefinition>();
    }

    public class MaterialFieldDefinition
    {
        [JsonProperty("name")]
        public string Name { get; set; }

        [JsonProperty("label")]
        public string Label { get; set; }

        [JsonProperty("type")]
        public string Type { get; set; }

        [JsonProperty("required")]
        public bool Required { get; set; }

        [JsonProperty("default")]
        public object DefaultValue { get; set; }

        [JsonProperty("unit")]
        public string Unit { get; set; }

        [JsonProperty("cad_key")]
        public string CadKey { get; set; }
    }

    public class MaterialSyncResponse
    {
        [JsonProperty("ok")]
        public bool Ok { get; set; }

        [JsonProperty("valid")]
        public bool Valid { get; set; }

        [JsonProperty("action")]
        public string Action { get; set; }

        [JsonProperty("profile_id")]
        public string ProfileId { get; set; }

        [JsonProperty("item_id")]
        public string ItemId { get; set; }

        [JsonProperty("dry_run")]
        public bool DryRun { get; set; }

        [JsonProperty("properties")]
        public Dictionary<string, object> Properties { get; set; } = new Dictionary<string, object>();

        [JsonProperty("cad_fields")]
        public Dictionary<string, object> CadFields { get; set; } = new Dictionary<string, object>();

        [JsonProperty("matched_items")]
        public List<Dictionary<string, object>> MatchedItems { get; set; } = new List<Dictionary<string, object>>();

        [JsonProperty("errors")]
        public List<MaterialSyncIssue> Errors { get; set; } = new List<MaterialSyncIssue>();

        [JsonProperty("warnings")]
        public List<string> Warnings { get; set; } = new List<string>();

        [JsonProperty("conflicts")]
        public List<MaterialSyncIssue> Conflicts { get; set; } = new List<MaterialSyncIssue>();
    }

    public class MaterialAssistantResolveResponse
    {
        [JsonProperty("ok")]
        public bool Ok { get; set; }

        [JsonProperty("profile_id")]
        public string ProfileId { get; set; }

        [JsonProperty("composed_properties")]
        public Dictionary<string, object> ComposedProperties { get; set; } = new Dictionary<string, object>();

        [JsonProperty("cad_fields")]
        public Dictionary<string, object> CadFields { get; set; } = new Dictionary<string, object>();

        [JsonProperty("exact_matches")]
        public List<Dictionary<string, object>> ExactMatches { get; set; } = new List<Dictionary<string, object>>();

        [JsonProperty("similar_candidates")]
        public List<Dictionary<string, object>> SimilarCandidates { get; set; } = new List<Dictionary<string, object>>();

        [JsonProperty("draft_suggested")]
        public bool DraftSuggested { get; set; }

        [JsonProperty("errors")]
        public List<MaterialSyncIssue> Errors { get; set; } = new List<MaterialSyncIssue>();

        [JsonProperty("warnings")]
        public List<string> Warnings { get; set; } = new List<string>();
    }

    public class MaterialAssistantCreateResponse
    {
        [JsonProperty("ok")]
        public bool Ok { get; set; }

        [JsonProperty("profile_id")]
        public string ProfileId { get; set; }

        [JsonProperty("item_id")]
        public string ItemId { get; set; }

        [JsonProperty("item_number")]
        public string ItemNumber { get; set; }

        [JsonProperty("state")]
        public string State { get; set; }

        [JsonProperty("current_state")]
        public string CurrentState { get; set; }

        [JsonProperty("draft_check")]
        public Dictionary<string, object> DraftCheck { get; set; } = new Dictionary<string, object>();

        [JsonProperty("errors")]
        public List<MaterialSyncIssue> Errors { get; set; } = new List<MaterialSyncIssue>();

        [JsonProperty("warnings")]
        public List<string> Warnings { get; set; } = new List<string>();
    }

    public class MaterialSyncIssue
    {
        [JsonProperty("field")]
        public string Field { get; set; }

        [JsonProperty("code")]
        public string Code { get; set; }

        [JsonProperty("message")]
        public string Message { get; set; }

        [JsonProperty("current")]
        public object Current { get; set; }

        [JsonProperty("incoming")]
        public object Incoming { get; set; }
    }

    public class MaterialDiffPreviewResponse
    {
        [JsonIgnore]
        public string PullId { get; set; }

        [JsonProperty("ok")]
        public bool Ok { get; set; }

        [JsonProperty("profile_id")]
        public string ProfileId { get; set; }

        [JsonProperty("item_id")]
        public string ItemId { get; set; }

        [JsonProperty("properties")]
        public Dictionary<string, object> Properties { get; set; } = new Dictionary<string, object>();

        [JsonProperty("current_cad_fields")]
        public Dictionary<string, object> CurrentCadFields { get; set; } = new Dictionary<string, object>();

        [JsonProperty("target_cad_fields")]
        public Dictionary<string, object> TargetCadFields { get; set; } = new Dictionary<string, object>();

        [JsonProperty("write_cad_fields")]
        public Dictionary<string, object> WriteCadFields { get; set; } = new Dictionary<string, object>();

        [JsonProperty("requires_confirmation")]
        public bool RequiresConfirmation { get; set; }

        [JsonProperty("diffs")]
        public List<MaterialCadFieldDiff> Diffs { get; set; } = new List<MaterialCadFieldDiff>();

        [JsonProperty("summary")]
        public Dictionary<string, int> Summary { get; set; } = new Dictionary<string, int>();

        [JsonProperty("errors")]
        public List<MaterialSyncIssue> Errors { get; set; } = new List<MaterialSyncIssue>();

        [JsonProperty("warnings")]
        public List<string> Warnings { get; set; } = new List<string>();
    }

    public class MaterialCadFieldDiff
    {
        [JsonProperty("cad_key")]
        public string CadKey { get; set; }

        [JsonProperty("property")]
        public string Property { get; set; }

        [JsonProperty("current")]
        public object Current { get; set; }

        [JsonProperty("target")]
        public object Target { get; set; }

        [JsonProperty("status")]
        public string Status { get; set; }
    }

    public interface IMaterialSyncHelperTransport
    {
        Task<T> PostJsonAsync<T>(string path, object payload, CancellationToken cancellationToken);
    }

    public sealed class SharedMaterialSyncHelperTransport : IMaterialSyncHelperTransport, IDisposable
    {
        private readonly HelperLocator _locator;

        public SharedMaterialSyncHelperTransport()
            : this(new HelperLocator())
        {
        }

        public SharedMaterialSyncHelperTransport(HelperLocator locator)
        {
            _locator = locator ?? throw new ArgumentNullException(nameof(locator));
        }

        public async Task<T> PostJsonAsync<T>(string path, object payload, CancellationToken cancellationToken)
        {
            var baseUri = await _locator.EnsureHelperRunningAsync(cancellationToken).ConfigureAwait(false);
            using (var transport = new HelperTransport(baseUri))
            {
                return await transport.PostJsonAsync<T>(path, payload, cancellationToken).ConfigureAwait(false);
            }
        }

        public void Dispose()
        {
            _locator.Dispose();
        }
    }

    public sealed class HelperDiffPreviewResponse
    {
        [JsonProperty("pull_id")]
        public string PullId { get; set; }

        [JsonProperty("server_response")]
        public JObject ServerResponse { get; set; }
    }
}
