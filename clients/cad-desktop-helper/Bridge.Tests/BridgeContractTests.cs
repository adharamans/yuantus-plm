using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Xml.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Xunit;
using Yuantus.Cad.Bridge;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Bridge.Tests
{
    public sealed class BridgeContractTests
    {
        [Fact]
        public void test_s9_bridge_project_targets_net46_and_references_shared_net46()
        {
            var path = Path.Combine(FindRepoRoot(), "clients", "cad-desktop-helper", "Bridge", "YuantusCadHelperBridge.csproj");
            var doc = XDocument.Load(path);

            var targetFramework = doc.Descendants("TargetFramework").Select(e => e.Value).FirstOrDefault();
            var targetFrameworks = doc.Descendants("TargetFrameworks").Select(e => e.Value).FirstOrDefault();
            Assert.True(
                targetFramework == "net46" || (targetFrameworks != null && targetFrameworks.Contains("net46")),
                "Bridge project must target net46 (got TargetFramework=" + targetFramework + " TargetFrameworks=" + targetFrameworks + ").");

            var sharedRef = doc.Descendants("ProjectReference")
                .Select(e => (string)e.Attribute("Include"))
                .FirstOrDefault(s => s != null && s.Replace('\\', '/').EndsWith("Shared/Yuantus.Cad.Shared.csproj", StringComparison.OrdinalIgnoreCase));
            Assert.NotNull(sharedRef);

            var assemblyName = doc.Descendants("AssemblyName").Select(e => e.Value).FirstOrDefault();
            Assert.Equal("YuantusCadHelperBridge", assemblyName);
        }

        [Fact]
        public void test_s9_bridge_exposes_exactly_one_lisp_function_yuantus_helper_call()
        {
            var sources = ReadBridgeSources();

            var lispFunctionCount = CountOccurrences(sources, "[LispFunction(");
            Assert.Equal(1, lispFunctionCount);
            Assert.Contains("[LispFunction(\"yuantus-helper-call\")]", sources);
        }

        [Fact]
        public void test_s9_lisp_function_accepts_endpoint_and_json_string_arguments_only()
        {
            var sources = ReadBridgeSources();

            Assert.Contains("public static object YuantusHelperCall(ResultBuffer args)", sources);
            Assert.Contains("values.Length != 2", sources);
            Assert.DoesNotContain("values.Length >= 2", sources);
            Assert.DoesNotContain("values.Length > 2", sources);

            Assert.Contains("public BridgeResult Call(string endpoint, string jsonRequest)", sources);
        }

        [Fact]
        public async Task test_s9_rejects_absolute_uri_network_path_backslash_and_control_char_endpoint_before_transport()
        {
            string[] rejectedEndpoints =
            {
                "",
                " ",
                " /diff/preview",
                "/diff/preview ",
                "diff/preview",
                "//evil.example/collect",
                "/redirect/http://evil/",
                "/redirect/https://evil/",
                "/redirect/file:///c/secret",
                "/path\\with\\backslash",
                "/path\twith\ttab",
                "/path\nwith\nnewline",
                "/path%2fbypass",
                "/path%2Fbypass",
                "/path%5Cbackslash",
                "/path%00null",
            };

            foreach (var endpoint in rejectedEndpoints)
            {
                var locator = new ThrowingBridgeLocator();
                var transport = new RecordingBridgeTransport();
                var writer = new RecordingCommandLineWriter();
                var service = new BridgeCallService(locator, transport, writer);

                var result = await service.CallAsync(endpoint, "{}", CancellationToken.None);

                Assert.False(result.Ok);
                Assert.Equal(ErrorCodes.HelperInputValidationFailed, result.Code);
                Assert.Equal(0, locator.Calls);
                Assert.Equal(0, transport.Calls);
                Assert.Single(writer.Lines);
            }
        }

        [Fact]
        public async Task test_s9_rejects_missing_or_non_object_json_request_without_calling_transport()
        {
            string[] rejected = { null, "", "not-json", "[]", "42", "\"string\"", "null", "true" };

            foreach (var json in rejected)
            {
                var locator = new ThrowingBridgeLocator();
                var transport = new RecordingBridgeTransport();
                var writer = new RecordingCommandLineWriter();
                var service = new BridgeCallService(locator, transport, writer);

                var result = await service.CallAsync("/diff/preview", json, CancellationToken.None);

                Assert.False(result.Ok);
                Assert.Equal(ErrorCodes.HelperInputValidationFailed, result.Code);
                Assert.Equal(0, locator.Calls);
                Assert.Equal(0, transport.Calls);
            }
        }

        [Fact]
        public async Task test_s9_posts_valid_json_object_to_helper_through_shared_locator_and_transport()
        {
            var locator = new RecordingBridgeLocator(new Uri("http://127.0.0.1:7959"));
            var transport = new RecordingBridgeTransport
            {
                Response = new JObject { ["server_response"] = new JObject { ["item_id"] = "ITEM-1" } }
            };
            var writer = new RecordingCommandLineWriter();
            var service = new BridgeCallService(locator, transport, writer);

            var payload = new JObject { ["item_id"] = "ITEM-1", ["profile_id"] = "sheet" };
            var result = await service.CallAsync("/diff/preview", payload.ToString(), CancellationToken.None);

            Assert.True(result.Ok);

            // Strict call shape per taskbook §5 test 6: locator called once
            // before transport, transport called once with the exact endpoint
            // and exact JSON object the bridge received from Lisp.
            Assert.Equal(1, locator.Calls);
            Assert.Equal(1, transport.Calls);
            Assert.True(locator.CalledBefore(transport));
            Assert.Equal(new Uri("http://127.0.0.1:7959"), transport.LastBaseUri);
            Assert.Equal("/diff/preview", transport.LastEndpoint);
            Assert.True(JToken.DeepEquals(payload, transport.LastPayload), "transport payload must match Lisp-supplied JSON object byte-for-byte.");
            Assert.Empty(writer.Lines);
        }

        [Fact]
        public async Task test_s9_returns_helper_data_payload_as_json_string_on_success()
        {
            // Non-null object data: returned as serialized JSON object.
            var transport = new RecordingBridgeTransport
            {
                Response = new JObject { ["pull_id"] = "PULL-1", ["write_cad_fields"] = new JObject { ["MAT"] = "AL6061" } }
            };
            var service = new BridgeCallService(new RecordingBridgeLocator(new Uri("http://127.0.0.1:7959")), transport, new RecordingCommandLineWriter());

            var result = await service.CallAsync("/diff/preview", "{}", CancellationToken.None);

            Assert.True(result.Ok);
            var parsed = JObject.Parse(result.Payload);
            Assert.Equal("PULL-1", parsed.Value<string>("pull_id"));

            // Null data (JTokenType.Null): returned as literal "null" string,
            // distinguishing successful JSON-null from transport failure.
            var nullTransport = new RecordingBridgeTransport { Response = JValue.CreateNull() };
            var nullService = new BridgeCallService(new RecordingBridgeLocator(new Uri("http://127.0.0.1:7959")), nullTransport, new RecordingCommandLineWriter());
            var nullResult = await nullService.CallAsync("/diff/preview", "{}", CancellationToken.None);
            Assert.True(nullResult.Ok);
            Assert.Equal("null", nullResult.Payload);

            // Missing data (C# null returned by transport): also "null" string.
            var missingTransport = new RecordingBridgeTransport { Response = null };
            var missingService = new BridgeCallService(new RecordingBridgeLocator(new Uri("http://127.0.0.1:7959")), missingTransport, new RecordingCommandLineWriter());
            var missingResult = await missingService.CallAsync("/diff/preview", "{}", CancellationToken.None);
            Assert.True(missingResult.Ok);
            Assert.Equal("null", missingResult.Payload);
        }

        [Fact]
        public async Task test_s9_returns_nil_and_writes_sanitized_error_on_helper_exception()
        {
            string[] codes =
            {
                ErrorCodes.AuthLocalTokenInvalid,
                ErrorCodes.AuthPlmNotLoggedIn,
                ErrorCodes.ProtoVersionUnsupported,
                ErrorCodes.HelperDpapiUnavailable,
                ErrorCodes.HelperUnhealthy,
                ErrorCodes.PlmInboundConflict,
            };
            foreach (var code in codes)
            {
                var transport = new RecordingBridgeTransport
                {
                    Throw = new HelperException(code, "helper returned " + code + " for diff preview", false)
                };
                var writer = new RecordingCommandLineWriter();
                var service = new BridgeCallService(new RecordingBridgeLocator(new Uri("http://127.0.0.1:7959")), transport, writer);

                var result = await service.CallAsync("/diff/preview", "{}", CancellationToken.None);

                Assert.False(result.Ok);
                Assert.Equal(code, result.Code);
                var line = Assert.Single(writer.Lines);
                Assert.Equal(code, line.Code);
            }
        }

        [Fact]
        public async Task test_s9_error_output_never_contains_local_token_request_body_or_response_body()
        {
            // The bridge's defense against token/body leakage is the
            // short-reason convention in BridgeCallService: every caught
            // exception is mapped to its TYPE NAME (e.g. "HelperException",
            // "JsonException"), never the message body. The writer's
            // character sanitizer defangs control characters and special
            // syntax; it is NOT a content-aware redactor and is not asked
            // to be. So this test verifies the contract that matters:
            // when a HelperException carries a secret in its Message, the
            // production code path passes only the type name to the writer,
            // and no token / body / payload value appears anywhere in the
            // recorded failure line.
            const string SecretToken = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
            const string SecretBody = "supersecret-request-body-content";
            var writer = new RecordingCommandLineWriter();
            var transport = new RecordingBridgeTransport
            {
                Throw = new HelperException(
                    ErrorCodes.AuthLocalTokenInvalid,
                    "helper returned 401 with response body containing " + SecretBody,
                    false)
            };
            var service = new BridgeCallService(new RecordingBridgeLocator(new Uri("http://127.0.0.1:7959")), transport, writer);

            var payload = new JObject { ["secret_field"] = SecretBody, ["item_id"] = SecretToken };
            var result = await service.CallAsync("/sync/inbound", payload.ToString(), CancellationToken.None);

            Assert.False(result.Ok);
            Assert.Equal(ErrorCodes.AuthLocalTokenInvalid, result.Code);

            var line = Assert.Single(writer.Lines);
            // Production short-reason convention: caught HelperException
            // yields the type name, not the secret-bearing message.
            Assert.Equal("HelperException", line.Reason);
            Assert.Equal(ErrorCodes.AuthLocalTokenInvalid, line.Code);

            // Neither the secret token, the secret body, nor any payload
            // field value appears in any recorded line field.
            Assert.DoesNotContain(SecretToken, line.Code ?? string.Empty);
            Assert.DoesNotContain(SecretToken, line.Reason ?? string.Empty);
            Assert.DoesNotContain(SecretBody, line.Code ?? string.Empty);
            Assert.DoesNotContain(SecretBody, line.Reason ?? string.Empty);

            // Defense-in-depth: rendering the line through the production
            // ConsoleBridgeCommandLineWriter using ONLY the values the
            // production path produced (code + ShortReason) yields no
            // secret either. This is the realistic NETLOAD output shape.
            using (var captured = new StringWriter())
            {
                var prior = Console.Error;
                Console.SetError(captured);
                try
                {
                    new ConsoleBridgeCommandLineWriter().WriteFailure(line.Code, line.Reason);
                }
                finally
                {
                    Console.SetError(prior);
                }
                var stderr = captured.ToString();
                Assert.Contains("[YUANTUS_HELPER_CALL_FAILED]", stderr);
                Assert.Contains("code=" + ErrorCodes.AuthLocalTokenInvalid, stderr);
                Assert.Contains("reason=HelperException", stderr);
                Assert.DoesNotContain(SecretToken, stderr);
                Assert.DoesNotContain(SecretBody, stderr);
            }
        }

        [Fact]
        public void test_s9_sync_wrapper_preserves_helper_exception_code_without_aggregate_exception()
        {
            var transport = new RecordingBridgeTransport
            {
                Throw = new HelperException(ErrorCodes.HelperUnhealthy, "helper is unhealthy", true)
            };
            var writer = new RecordingCommandLineWriter();
            var service = new BridgeCallService(new RecordingBridgeLocator(new Uri("http://127.0.0.1:7959")), transport, writer);

            var result = service.Call("/diff/preview", "{}");

            Assert.False(result.Ok);
            Assert.Equal(ErrorCodes.HelperUnhealthy, result.Code);
            Assert.NotEqual("AggregateException", writer.Lines.Single().Reason);

            var sources = ReadBridgeSources();
            Assert.Contains(".GetAwaiter().GetResult()", sources);
            Assert.DoesNotContain(".Result;", sources);
            Assert.DoesNotContain(".Wait()", sources);
        }

        [Fact]
        public void test_s9_adds_no_helper_server_routes_and_preserves_route_count_ten()
        {
            var helperSources = ReadHelperSources();
            var bridgeSources = ReadBridgeSources();

            var mapCount = CountOccurrences(helperSources, "MapGet(") + CountOccurrences(helperSources, "MapPost(");
            Assert.Equal(10, mapCount);

            Assert.DoesNotContain("MapGet(", bridgeSources);
            Assert.DoesNotContain("MapPost(", bridgeSources);
            Assert.DoesNotContain("MapPut(", bridgeSources);
            Assert.DoesNotContain("MapDelete(", bridgeSources);
        }

        [Fact]
        public void test_s9_does_not_add_shell_notify_dedup_check_compose_validate_tasks_or_diagnostics_routes()
        {
            var sources = ReadBridgeSources();

            Assert.DoesNotContain("/shell/notify", sources);
            Assert.DoesNotContain("/dedup/check", sources);
            Assert.DoesNotContain("/compose", sources);
            Assert.DoesNotContain("/validate", sources);
            Assert.DoesNotContain("/tasks", sources);
            Assert.DoesNotContain("/diagnostics/snapshot", sources);
            Assert.DoesNotContain("UseCors", sources);
        }

        [Fact]
        public void test_s9_bridge_contains_no_dwg_write_business_diff_parsing_or_modal_ui_logic()
        {
            var sources = ReadBridgeSources();

            Assert.DoesNotContain("CadMaterialFieldService", sources);
            Assert.DoesNotContain("write_cad_fields\"]", sources); // accessing the business field
            Assert.DoesNotContain("applied_fields\"]", sources);
            Assert.DoesNotContain("failed_fields\"]", sources);
            Assert.DoesNotContain("YUANTUS_DIFF_PREVIEW", sources);
            Assert.DoesNotContain("MessageBox", sources);
            Assert.DoesNotContain("Window.Show", sources);
            Assert.DoesNotContain(".ShowDialog(", sources);
            Assert.DoesNotContain("Transaction.Start", sources);
            Assert.DoesNotContain("Database.NewDocument", sources);
            Assert.DoesNotContain("BlockReference", sources);
        }

        [Fact]
        public void test_s9_bridge_uses_shared_helper_locator_transport_and_local_token_store_only()
        {
            var sources = ReadBridgeSources();

            Assert.Contains("using Yuantus.Cad.Shared.Discovery;", sources);
            Assert.Contains("using Yuantus.Cad.Shared.Transport;", sources);
            Assert.Contains("HelperLocator", sources);
            Assert.Contains("HelperTransport", sources);

            // No direct HttpClient construction or DPAPI/local-token access
            // outside the shared abstractions.
            Assert.DoesNotContain("new HttpClient(", sources);
            Assert.DoesNotContain("ProtectedData.", sources);
            Assert.DoesNotContain("LocalTokenStore.ReadLocalToken", sources);
            Assert.DoesNotContain("LocalTokenStore.WriteLocalToken", sources);
            Assert.DoesNotContain("Process.Start", sources);
        }

        [Fact]
        public void test_s9_bridge_core_is_sdk_free_contract_testable_without_native_cad()
        {
            // The fact that this test assembly compiles + runs without any
            // AutoCAD reference is itself the SDK-free coverage signal.
            var bridgeAssembly = typeof(BridgeCallService).Assembly;
            var coreTypes = new[]
            {
                typeof(BridgeCallService),
                typeof(BridgeResult),
                typeof(EndpointValidator),
                typeof(IBridgeLocator),
                typeof(IBridgeTransport),
                typeof(IBridgeCommandLineWriter),
                typeof(ConsoleBridgeCommandLineWriter),
                typeof(SharedBridgeLocator),
                typeof(SharedBridgeTransport),
            };
            foreach (var type in coreTypes)
            {
                Assert.Equal(bridgeAssembly, type.Assembly);
            }

            // The CAD-host adapter is excluded from the SDK-free build by the
            // AUTOCAD_HOST conditional symbol; if it were compiled the test
            // runner would have failed at type load.
            Assert.Null(bridgeAssembly.GetType("Yuantus.Cad.Bridge.Adapters.AutoCadHostAdapter"));
        }

        [Fact]
        public void test_s9_workflow_runs_bridge_contracts_and_static_verifier()
        {
            var workflow = File.ReadAllText(Path.Combine(FindRepoRoot(), ".github", "workflows", "cad-helper-shared-dotnet.yml"));

            Assert.Contains("clients/cad-desktop-helper/Bridge/**", workflow);
            Assert.Contains("clients/cad-desktop-helper/Bridge.Tests/**", workflow);
            Assert.Contains("dotnet test clients/cad-desktop-helper/Bridge.Tests/Yuantus.Cad.Bridge.Tests.csproj", workflow);
            Assert.Contains("verify_bridge_static.py", workflow);
        }

        [Fact]
        public void test_s9_static_verifier_rejects_absolute_uri_forwarding_and_direct_httpclient_token_reads()
        {
            var verifier = Path.Combine(FindRepoRoot(), "clients", "cad-desktop-helper", "verify_bridge_static.py");
            Assert.True(File.Exists(verifier), "verify_bridge_static.py must exist alongside the Bridge sources.");
            var text = File.ReadAllText(verifier);

            // The verifier must scan for these danger patterns.
            Assert.Contains("HttpClient", text);
            Assert.Contains("ProtectedData", text);
            Assert.Contains("LocalTokenStore.ReadLocalToken", text);
            Assert.Contains("absolute_scheme", text);
            Assert.Contains("HelperLocator", text);
            Assert.Contains("HelperTransport", text);
        }

        [Fact]
        public void test_s9_dev_verification_records_deferred_native_cad_netload_signoff()
        {
            var dev = File.ReadAllText(Path.Combine(FindRepoRoot(), "docs", "DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S9_LISP_BRIDGE_R1_20260523.md"));

            Assert.Contains("Deferred", dev);
            Assert.Contains("NETLOAD", dev);
            Assert.Contains("operational signoff", dev);
            Assert.Contains("CAD command-line writer", dev);
        }

        [Fact]
        public void test_s9_s10_dependency_is_documented_and_no_lisp_shell_command_files_are_added()
        {
            var sources = ReadBridgeSources();
            Assert.DoesNotContain("YUANTUS_DIFF_PREVIEW", sources);
            Assert.DoesNotContain("PLMMATPULL", sources);
            Assert.DoesNotContain("PLMMATPUSH", sources);
            Assert.DoesNotContain("PLMMATCOMPOSE", sources);
            Assert.DoesNotContain("PLMMATPROFILES", sources);

            var bridgeRoot = Path.Combine(FindRepoRoot(), "clients", "cad-desktop-helper", "Bridge");
            var lspFiles = Directory.GetFiles(bridgeRoot, "*.lsp", SearchOption.AllDirectories);
            Assert.Empty(lspFiles);

            var dev = File.ReadAllText(Path.Combine(FindRepoRoot(), "docs", "DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S9_LISP_BRIDGE_R1_20260523.md"));
            Assert.Contains("S10", dev);
        }

        [Fact]
        public void test_s9_static_wiring_reaches_production_helper_locator_and_transport()
        {
            // Test 20 (added during M1 convergence): production wiring must
            // reach S1 Shared HelperLocator/HelperTransport, not local fakes.
            // S1 Shared.Tests already provide real-seam coverage; this test
            // pins that the bridge cannot silently bypass those seams.
            var locatorSource = File.ReadAllText(Path.Combine(
                FindRepoRoot(), "clients", "cad-desktop-helper", "Bridge", "SharedBridgeLocator.cs"));
            Assert.Contains("using Yuantus.Cad.Shared.Discovery;", locatorSource);
            Assert.Contains("new HelperLocator()", locatorSource);
            Assert.Contains(".EnsureHelperRunningAsync(", locatorSource);

            var transportSource = File.ReadAllText(Path.Combine(
                FindRepoRoot(), "clients", "cad-desktop-helper", "Bridge", "SharedBridgeTransport.cs"));
            Assert.Contains("using Yuantus.Cad.Shared.Transport;", transportSource);
            Assert.Contains("new HelperTransport(", transportSource);
            Assert.Contains(".PostJsonAsync<JToken>(", transportSource);

            var serviceSource = File.ReadAllText(Path.Combine(
                FindRepoRoot(), "clients", "cad-desktop-helper", "Bridge", "BridgeCallService.cs"));
            Assert.Contains("new SharedBridgeLocator()", serviceSource);
            Assert.Contains("new SharedBridgeTransport()", serviceSource);
        }

        // -------------------- helpers --------------------

        private static string ReadBridgeSources()
        {
            return ReadCsharpSources(Path.Combine(FindRepoRoot(), "clients", "cad-desktop-helper", "Bridge"));
        }

        private static string ReadHelperSources()
        {
            return ReadCsharpSources(Path.Combine(FindRepoRoot(), "clients", "cad-desktop-helper", "Helper"));
        }

        private static string ReadCsharpSources(string root)
        {
            return string.Join(
                "\n",
                Directory.GetFiles(root, "*.cs", SearchOption.AllDirectories)
                    .Where(p => !p.Contains(Path.DirectorySeparatorChar + "bin" + Path.DirectorySeparatorChar.ToString()))
                    .Where(p => !p.Contains(Path.DirectorySeparatorChar + "obj" + Path.DirectorySeparatorChar.ToString()))
                    .OrderBy(p => p)
                    .Select(File.ReadAllText));
        }

        private static int CountOccurrences(string text, string needle)
        {
            var count = 0;
            var index = 0;
            while ((index = text.IndexOf(needle, index, StringComparison.Ordinal)) >= 0)
            {
                count++;
                index += needle.Length;
            }
            return count;
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

        // -------------------- fakes --------------------

        // Shared monotonic sequencer used by both RecordingBridgeLocator
        // and RecordingBridgeTransport. Two type-scoped counters would
        // independently start at 0 and both record order 1 within a single
        // test, defeating the §5 test 6 strict-sequencing assertion (locator
        // must be called before transport). One shared counter is the
        // minimum fix.
        private static class CallSequence
        {
            private static int _counter;

            public static int Next()
            {
                return System.Threading.Interlocked.Increment(ref _counter);
            }
        }

        private sealed class RecordingBridgeLocator : IBridgeLocator
        {
            private readonly Uri _baseUri;
            public int Calls { get; private set; }
            public int CallOrder { get; private set; }

            public RecordingBridgeLocator(Uri baseUri)
            {
                _baseUri = baseUri;
            }

            public Task<Uri> EnsureHelperRunningAsync(CancellationToken cancellationToken)
            {
                Calls++;
                CallOrder = CallSequence.Next();
                return Task.FromResult(_baseUri);
            }

            public bool CalledBefore(RecordingBridgeTransport other)
            {
                return CallOrder > 0 && other.CallOrder > 0 && CallOrder < other.CallOrder;
            }
        }

        private sealed class ThrowingBridgeLocator : IBridgeLocator
        {
            public int Calls { get; private set; }

            public Task<Uri> EnsureHelperRunningAsync(CancellationToken cancellationToken)
            {
                Calls++;
                throw new InvalidOperationException("Locator must not be called when input validation has rejected the call.");
            }
        }

        private sealed class RecordingBridgeTransport : IBridgeTransport
        {
            public int Calls { get; private set; }
            public int CallOrder { get; private set; }
            public Uri LastBaseUri { get; private set; }
            public string LastEndpoint { get; private set; }
            public JObject LastPayload { get; private set; }
            public JToken Response { get; set; }
            public HelperException Throw { get; set; }

            public Task<JToken> PostJsonAsync(
                Uri baseUri,
                string endpoint,
                JObject payload,
                CancellationToken cancellationToken)
            {
                Calls++;
                CallOrder = CallSequence.Next();
                LastBaseUri = baseUri;
                LastEndpoint = endpoint;
                LastPayload = payload;
                if (Throw != null)
                {
                    throw Throw;
                }
                return Task.FromResult(Response);
            }
        }

        private sealed class RecordingCommandLineWriter : IBridgeCommandLineWriter
        {
            public List<WriterLine> Lines { get; } = new List<WriterLine>();

            public void WriteFailure(string code, string reason)
            {
                Lines.Add(new WriterLine { Code = code, Reason = reason });
            }
        }

        private sealed class WriterLine
        {
            public string Code { get; set; }
            public string Reason { get; set; }
        }
    }
}
