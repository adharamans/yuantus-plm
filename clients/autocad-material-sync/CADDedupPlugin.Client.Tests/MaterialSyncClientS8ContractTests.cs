using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Xml.Linq;
using CADDedupPlugin;
using Newtonsoft.Json.Linq;
using Xunit;

namespace CADDedupPlugin.Client.Tests
{
    public sealed class MaterialSyncClientS8ContractTests
    {
        [Fact]
        public void test_s8_preserves_no_dedup_route_while_helper_has_g1a_document_routes()
        {
            var helper = ReadRepoFile("clients/cad-desktop-helper/Helper/HelperRuntime.cs");

            Assert.Equal(15, Count(helper, "MapGet(") + Count(helper, "MapPost("));
            Assert.Contains("MapPost(\"/document/checkout\"", helper);
            Assert.Contains("MapPost(\"/document/undo-checkout\"", helper);
            Assert.Contains("MapPost(\"/document/status\"", helper);
            Assert.Contains("MapPost(\"/document/checkin\"", helper);
            Assert.Contains("MapPost(\"/document/bom-import\"", helper);
            Assert.DoesNotContain("MapPost(\"/dedup/check\"", helper);
        }

        [Fact]
        public async Task test_material_sync_client_migrates_s6_supported_methods_to_helper_transport()
        {
            var transport = new RecordingTransport();
            var client = CreateClient(transport);

            await client.DiffPreviewAsync("sheet", "item-1", new Dictionary<string, object>(), false);
            await client.SyncInboundAsync("sheet", new Dictionary<string, object>(), true, false, true);
            await client.SyncOutboundAsync("sheet", "item-1", false);

            Assert.Equal(new[] { "/diff/preview", "/sync/inbound", "/sync/outbound" }, transport.Calls.Select(c => c.Path).ToArray());
        }

        [Fact]
        public async Task test_material_sync_client_unwraps_helper_diff_preview_pull_id_and_server_response()
        {
            var transport = new RecordingTransport();
            transport.DiffResponse = new HelperDiffPreviewResponse
            {
                PullId = "PULL-0123456789abcdef0123456789abcdef",
                ServerResponse = JObject.Parse("{\"ok\":true,\"write_cad_fields\":{\"MAT\":\"AL6061\"},\"summary\":{\"changed\":1}}")
            };

            var preview = await CreateClient(transport).DiffPreviewAsync("sheet", "item-1", new Dictionary<string, object>(), false);

            Assert.Equal("PULL-0123456789abcdef0123456789abcdef", preview.PullId);
            Assert.True(preview.Ok);
            Assert.Equal("AL6061", preview.WriteCadFields["MAT"]);
            Assert.Equal(1, preview.Summary["changed"]);
        }

        [Fact]
        public async Task test_material_sync_client_sync_inbound_uses_helper_not_direct_plm()
        {
            var transport = new RecordingTransport();
            var client = CreateClient(transport);

            await client.SyncInboundAsync("sheet", new Dictionary<string, object> { ["MAT"] = "AL6061" }, false, true, true);

            var call = Assert.Single(transport.Calls);
            Assert.Equal("/sync/inbound", call.Path);
            Assert.Equal("AL6061", call.Payload["cad_fields"]["MAT"].Value<string>());
            Assert.Equal("autocad", call.Payload.Value<string>("cad_system"));
            Assert.DoesNotContain("{BasePath}/sync/inbound", ReadRepoFile("clients/autocad-material-sync/CADDedupPlugin/MaterialSyncApiClient.cs"));
        }

        [Fact]
        public async Task test_material_sync_client_sync_outbound_uses_helper_not_direct_plm()
        {
            var transport = new RecordingTransport();
            var client = CreateClient(transport);

            await client.SyncOutboundAsync("sheet", "item-1", true);

            var call = Assert.Single(transport.Calls);
            Assert.Equal("/sync/outbound", call.Path);
            Assert.Equal("item-1", call.Payload.Value<string>("item_id"));
            Assert.Equal("autocad", call.Payload.Value<string>("cad_system"));
            Assert.DoesNotContain("{BasePath}/sync/outbound", ReadRepoFile("clients/autocad-material-sync/CADDedupPlugin/MaterialSyncApiClient.cs"));
        }

        [Fact]
        public void test_material_sync_auxiliary_methods_remain_explicit_legacy_direct_calls()
        {
            var source = ReadRepoFile("clients/autocad-material-sync/CADDedupPlugin/MaterialSyncApiClient.cs");

            Assert.Contains("{BasePath}/profiles", source);
            Assert.Contains("{BasePath}/profiles/{safeProfileId}", source);
            Assert.Contains("{BasePath}/compose", source);
            Assert.Contains("{BasePath}/validate", source);
            Assert.Contains("CreateJsonRequest(HttpMethod.Post, $\"{BasePath}/compose\", payload)", source);
            Assert.Contains("CreateJsonRequest(HttpMethod.Post, $\"{BasePath}/validate\", payload)", source);
        }

        [Fact]
        public void test_dedup_api_client_remains_legacy_direct_until_upstream_is_ratified()
        {
            var dedup = ReadRepoFile("clients/autocad-material-sync/CADDedupPlugin/DedupApiClient.cs");
            var material = ReadRepoFile("clients/autocad-material-sync/CADDedupPlugin/MaterialSyncApiClient.cs");

            Assert.Contains("/api/dedup/check", dedup);
            Assert.DoesNotContain("HelperTransport", dedup);
            Assert.Contains("DedupApiClient.CheckDuplicateAsync", ReadRepoFile("docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_S8_DEDUP_PLUGIN_MIGRATION_20260523.md"));
            Assert.DoesNotContain("/dedup/check", material);
        }

        [Fact]
        public void test_plmmatpull_reports_audit_apply_result_after_successful_write_attempt()
        {
            var plugin = ReadRepoFile("clients/autocad-material-sync/CADDedupPlugin/DedupPlugin.cs");

            Assert.Contains("preview.PullId", plugin);
            Assert.Contains("ReportApplyResultSafely", plugin);
            Assert.Contains("\"ok\"", plugin);
            Assert.Contains("ReportApplyResultAsync", ReadRepoFile("clients/autocad-material-sync/CADDedupPlugin/MaterialSyncApiClient.cs"));
            Assert.Contains("/audit/apply-result", ReadRepoFile("clients/autocad-material-sync/CADDedupPlugin/MaterialSyncApiClient.cs"));
        }

        [Fact]
        public void test_plmmatpull_write_failure_reports_failed_apply_result_without_swallowing_cad_error()
        {
            var plugin = ReadRepoFile("clients/autocad-material-sync/CADDedupPlugin/DedupPlugin.cs");

            Assert.Contains("catch (System.Exception applyEx)", plugin);
            Assert.Contains("\"failed\"", plugin);
            Assert.Contains("CreateFailedFields(writeFields, applyEx)", plugin);
            Assert.Contains("throw;", plugin);
        }

        [Fact]
        public void test_plmmatpull_cancel_does_not_report_apply_result()
        {
            var plugin = ReadRepoFile("clients/autocad-material-sync/CADDedupPlugin/DedupPlugin.cs");
            var cancelIndex = plugin.IndexOf("if (!confirmed)", StringComparison.Ordinal);
            var reportIndex = plugin.IndexOf("ReportApplyResultSafely", cancelIndex, StringComparison.Ordinal);

            Assert.True(cancelIndex >= 0);
            Assert.True(reportIndex > cancelIndex);
            Assert.Contains("已取消 PLM 字段写回 CAD", plugin.Substring(cancelIndex, reportIndex - cancelIndex));
        }

        [Fact]
        public void test_caddedup_plugin_references_shared_net46_without_changing_autocad_targets()
        {
            var projectPath = RepoPath("clients/autocad-material-sync/CADDedupPlugin/CADDedupPlugin.csproj");
            var project = File.ReadAllText(projectPath);
            XDocument.Load(projectPath);

            Assert.Contains(@"..\..\cad-desktop-helper\Shared\Yuantus.Cad.Shared.csproj", project);
            Assert.Contains("<TargetFrameworkVersion Condition=\"'$(TargetFrameworkVersion)' == '' and '$(AutoCADVersion)' == '2018'\">v4.6</TargetFrameworkVersion>", project);
            Assert.Contains("<TargetFrameworkVersion Condition=\"'$(TargetFrameworkVersion)' == ''\">v4.8</TargetFrameworkVersion>", project);
            Assert.Contains("<PlatformTarget>x64</PlatformTarget>", project);
            Assert.Contains("Yuantus.Cad.Shared.dll", project);
        }

        [Fact]
        public void test_material_sync_migration_does_not_change_public_method_signatures()
        {
            AssertSignature(nameof(MaterialSyncApiClient.DiffPreviewAsync), typeof(Task<MaterialDiffPreviewResponse>), typeof(string), typeof(string), typeof(Dictionary<string, object>), typeof(bool));
            AssertSignature(nameof(MaterialSyncApiClient.SyncInboundAsync), typeof(Task<MaterialSyncResponse>), typeof(string), typeof(Dictionary<string, object>), typeof(bool), typeof(bool), typeof(bool));
            AssertSignature(nameof(MaterialSyncApiClient.SyncOutboundAsync), typeof(Task<MaterialSyncResponse>), typeof(string), typeof(string), typeof(bool));
            AssertSignature(nameof(MaterialSyncApiClient.GetProfilesAsync), typeof(Task<MaterialProfilesResponse>));
            AssertSignature(nameof(MaterialSyncApiClient.GetProfileAsync), typeof(Task<MaterialProfileResponse>), typeof(string));
            AssertSignature(nameof(MaterialSyncApiClient.ComposeAsync), typeof(Task<MaterialSyncResponse>), typeof(string), typeof(Dictionary<string, object>));
            AssertSignature(nameof(MaterialSyncApiClient.ValidateAsync), typeof(Task<MaterialSyncResponse>), typeof(string), typeof(Dictionary<string, object>), typeof(bool));
        }

        [Fact]
        public void test_s8_static_contract_documents_deferred_dedup_check_upstream_question()
        {
            var taskbook = ReadRepoFile("docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_S8_DEDUP_PLUGIN_MIGRATION_20260523.md");
            var dev = ReadRepoFile("docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S8_MATERIAL_SYNC_MIGRATION_R1_20260523.md");

            Assert.Contains("defer `/dedup/check`", taskbook);
            Assert.Contains("dedup-vision", taskbook);
            Assert.Contains("future dedup-check slice", dev);
        }

        [Fact]
        public void test_s8_keeps_no_lisp_shell_compose_validate_tasks_or_cors_scope()
        {
            var helper = ReadRepoFile("clients/cad-desktop-helper/Helper/HelperRuntime.cs");
            var material = ReadRepoFile("clients/autocad-material-sync/CADDedupPlugin/MaterialSyncApiClient.cs");

            Assert.DoesNotContain("/shell/notify", helper);
            Assert.DoesNotContain("MapPost(\"/compose\"", helper);
            Assert.DoesNotContain("MapPost(\"/validate\"", helper);
            Assert.DoesNotContain("MapPost(\"/tasks", helper);
            Assert.DoesNotContain("UseCors", helper);
            Assert.Contains("{BasePath}/compose", material);
            Assert.Contains("{BasePath}/validate", material);
        }

        [Fact]
        public void test_s8_workflow_runs_autocad_static_contracts_without_autocad_sdk()
        {
            var workflow = ReadRepoFile(".github/workflows/cad-helper-shared-dotnet.yml");

            Assert.Contains("clients/autocad-material-sync/**", workflow);
            Assert.Contains("verify_material_sync_static.py", workflow);
            Assert.Contains("CADDedupPlugin.Client.Tests/CADDedupPlugin.Client.Tests.csproj", workflow);
            Assert.DoesNotContain("CADDedupPlugin/CADDedupPlugin.csproj --configuration Release", workflow);
        }

        [Fact]
        public void test_s8_dev_verification_records_deferred_windows_autocad_signoff()
        {
            var dev = ReadRepoFile("docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S8_MATERIAL_SYNC_MIGRATION_R1_20260523.md");

            Assert.Contains("Windows AutoCAD build/load/smoke was not run locally", dev);
            Assert.Contains("deferred operational signoff", dev);
            Assert.Contains("PLMMATPUSH", dev);
            Assert.Contains("PLMMATPULL", dev);
        }

        private static MaterialSyncApiClient CreateClient(RecordingTransport transport)
        {
            return new MaterialSyncApiClient(
                new DedupConfig { ServerUrl = "https://plm.example.com", TimeoutSeconds = 5 },
                transport);
        }

        private static void AssertSignature(string name, Type returnType, params Type[] parameters)
        {
            var method = typeof(MaterialSyncApiClient).GetMethod(name, parameters);
            Assert.NotNull(method);
            Assert.Equal(returnType, method.ReturnType);
        }

        private static int Count(string haystack, string needle)
        {
            var count = 0;
            var index = 0;
            while ((index = haystack.IndexOf(needle, index, StringComparison.Ordinal)) >= 0)
            {
                count++;
                index += needle.Length;
            }
            return count;
        }

        private static string ReadRepoFile(string relativePath)
        {
            return File.ReadAllText(RepoPath(relativePath));
        }

        private static string RepoPath(string relativePath)
        {
            return Path.Combine(RepoRoot(), relativePath.Replace('/', Path.DirectorySeparatorChar));
        }

        private static string RepoRoot()
        {
            var dir = new DirectoryInfo(AppContext.BaseDirectory);
            while (dir != null)
            {
                if (Directory.Exists(Path.Combine(dir.FullName, "clients")) &&
                    Directory.Exists(Path.Combine(dir.FullName, "docs")))
                {
                    return dir.FullName;
                }
                dir = dir.Parent;
            }
            throw new DirectoryNotFoundException("Unable to locate repository root from " + AppContext.BaseDirectory);
        }

        private sealed class RecordingTransport : IMaterialSyncHelperTransport
        {
            public readonly List<RecordedCall> Calls = new List<RecordedCall>();
            public HelperDiffPreviewResponse DiffResponse = new HelperDiffPreviewResponse
            {
                PullId = "PULL-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                ServerResponse = JObject.Parse("{\"ok\":true}")
            };

            public Task<T> PostJsonAsync<T>(string path, object payload, CancellationToken cancellationToken)
            {
                Calls.Add(new RecordedCall(path, JObject.FromObject(payload)));
                object response;
                if (path == "/diff/preview")
                {
                    response = DiffResponse;
                }
                else if (typeof(T) == typeof(MaterialSyncResponse))
                {
                    response = new MaterialSyncResponse { Ok = true, Valid = true };
                }
                else
                {
                    response = new JObject { ["reported"] = true };
                }
                return Task.FromResult((T)response);
            }
        }

        private sealed class RecordedCall
        {
            public RecordedCall(string path, JObject payload)
            {
                Path = path;
                Payload = payload;
            }

            public string Path { get; }
            public JObject Payload { get; }
        }
    }
}
