using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;
using Xunit;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Helper.Tests
{
    public sealed class HelperResetLocalTokenContractTests
    {
        [Fact]
        public async Task test_s7_cli_argument_is_program_only_and_service_args_stay_rejected()
        {
            var resetCommand = new RecordingResetCommand();
            var errorWriter = new RecordingErrorWriter();
            var host = new RecordingHostRunner();
            var runtime = CreateRuntime(errorWriter, host, resetCommand);

            var resetExit = await HelperCommand.RunAsync(new[] { "--reset-local-token" }, runtime, CancellationToken.None);
            Assert.Equal(0, resetExit);
            Assert.Equal(1, resetCommand.RunCalls);
            Assert.Equal(0, host.RunCalls);

            var serviceExit = await HelperCommand.RunAsync(new string[0], runtime, CancellationToken.None);
            Assert.Equal(0, serviceExit);
            Assert.Equal(1, host.RunCalls);
            Assert.Equal(1, resetCommand.RunCalls);

            var rejectExit = await HelperCommand.RunAsync(new[] { "--unknown-flag" }, runtime, CancellationToken.None);
            Assert.Equal(1, rejectExit);
            Assert.Single(errorWriter.Messages);
            Assert.Contains(ErrorCodes.HelperInputValidationFailed, errorWriter.Messages[0]);
            Assert.Equal(1, host.RunCalls);
            Assert.Equal(1, resetCommand.RunCalls);

            var extraArgExit = await HelperCommand.RunAsync(new[] { "--reset-local-token", "--extra" }, runtime, CancellationToken.None);
            Assert.Equal(1, extraArgExit);
            Assert.Equal(2, errorWriter.Messages.Count);
            Assert.Contains(ErrorCodes.HelperInputValidationFailed, errorWriter.Messages[1]);
            Assert.Equal(1, resetCommand.RunCalls);

            var caseVariantExit = await HelperCommand.RunAsync(new[] { "--Reset-Local-Token" }, runtime, CancellationToken.None);
            Assert.Equal(1, caseVariantExit);
            Assert.Equal(3, errorWriter.Messages.Count);
            Assert.Contains(ErrorCodes.HelperInputValidationFailed, errorWriter.Messages[2]);
            Assert.Equal(1, resetCommand.RunCalls);
        }

        [Fact]
        public void test_reset_local_token_requires_interactive_local_console()
        {
            var redirected = CreateCommand(invocation: new FakeInvocationContext { IsInputRedirected = true, IsUserInteractive = true });
            Assert.Equal(1, redirected.Command.Run());
            Assert.Equal(ErrorCodes.HelperResetRequiresInteractive, redirected.LastErrorCode());
            Assert.Equal("error", redirected.Audit.Events.Single().Outcome);

            var notInteractive = CreateCommand(invocation: new FakeInvocationContext { IsInputRedirected = false, IsUserInteractive = false });
            Assert.Equal(1, notInteractive.Command.Run());
            Assert.Equal(ErrorCodes.HelperResetRequiresInteractive, notInteractive.LastErrorCode());
        }

        [Fact]
        public void test_reset_local_token_rejects_ssh_winrm_or_rdp_remote_invocation()
        {
            foreach (var sshVar in new[] { "SSH_CLIENT", "SSH_CONNECTION", "SSH_TTY" })
            {
                var ssh = CreateCommand(invocation: BaseInteractive(env: new Dictionary<string, string> { { sshVar, "1.2.3.4 5000 22" } }));
                Assert.Equal(1, ssh.Command.Run());
                Assert.Equal(ErrorCodes.HelperResetRequiresInteractive, ssh.LastErrorCode());
            }

            foreach (var launcher in new[] { "wsmprovhost.exe", "winrshost.exe", "sshd.exe" })
            {
                var winrm = CreateCommand(invocation: BaseInteractive(launcherNames: new[] { launcher }));
                Assert.Equal(1, winrm.Command.Run());
                Assert.Equal(ErrorCodes.HelperResetRequiresInteractive, winrm.LastErrorCode());
            }

            foreach (var rdpName in new[] { "RDP-Tcp#0", "RDP-Tcp#1", "rdp-tcp#2" })
            {
                var rdp = CreateCommand(invocation: BaseInteractive(env: new Dictionary<string, string> { { "SESSIONNAME", rdpName } }));
                Assert.Equal(1, rdp.Command.Run());
                Assert.Equal(ErrorCodes.HelperResetRequiresInteractive, rdp.LastErrorCode());
            }

            var localConsole = CreateCommand(invocation: BaseInteractive(env: new Dictionary<string, string> { { "SESSIONNAME", "Console" } }));
            localConsole.Console.QueueInput("n");
            Assert.Equal(1, localConsole.Command.Run());
            Assert.Equal(ErrorCodes.HelperResetCancelled, localConsole.LastErrorCode());
        }

        [Fact]
        public void test_reset_local_token_prompts_and_cancels_unless_user_confirms_y()
        {
            foreach (var cancelInput in new string[] { null, "", "n", "N", "no", "yes", " y" })
            {
                var fixture = CreateCommand();
                fixture.Console.QueueInput(cancelInput);

                Assert.Equal(1, fixture.Command.Run());
                Assert.Contains(ResetLocalTokenCommand.ConfirmationPrompt, fixture.Console.Written);
                Assert.Equal(ErrorCodes.HelperResetCancelled, fixture.LastErrorCode());
                Assert.Equal(0, fixture.Token.WriteCalls);
                Assert.Equal(1, fixture.Audit.Events.Count);
                Assert.Equal("error", fixture.Audit.Events.Single().Outcome);
            }

            foreach (var confirmInput in new[] { "y", "Y" })
            {
                var fixture = CreateCommand();
                fixture.Console.QueueInput(confirmInput);

                Assert.Equal(0, fixture.Command.Run());
                Assert.Equal(1, fixture.Token.WriteCalls);
                Assert.Equal("ok", fixture.Audit.Events.Single().Outcome);
            }
        }

        [Fact]
        public void test_reset_local_token_rejects_when_helper_mutex_or_active_session_exists()
        {
            var mutexHeld = CreateCommand(detection: ActiveHelperDetection.ActiveDetected("current session mutex is held"));
            mutexHeld.Console.QueueInput("y");
            Assert.Equal(1, mutexHeld.Command.Run());
            Assert.Equal(ErrorCodes.HelperResetHelperRunning, mutexHeld.LastErrorCode());
            Assert.Equal(0, mutexHeld.Token.WriteCalls);

            var crossSession = CreateCommand(detection: ActiveHelperDetection.ActiveDetected("cross-session helper detected"));
            crossSession.Console.QueueInput("y");
            Assert.Equal(1, crossSession.Command.Run());
            Assert.Equal(ErrorCodes.HelperResetHelperRunning, crossSession.LastErrorCode());

            var installIds = new FakeInstallIdProvider(Guid.Parse("00000000-0000-0000-0000-0000000000aa"));
            var detector = new DefaultActiveHelperDetector(
                installIds,
                new FakeMutexFactory(false),
                new StubSessionFileScanner(new List<HelperSessionFileRecord>()),
                new FakeProcessInspector(true, "any"));
            Assert.Equal(true, detector.Detect().Active);
        }

        [Fact]
        public void test_reset_local_token_ignores_stale_session_records_without_deleting_them()
        {
            using (var temp = TempWorkspace.Create())
            {
                var sessionFile = Path.Combine(temp.Root, "helper-session-42.json");
                File.WriteAllText(sessionFile, "{\"pid\":12345,\"image_path\":\"" + EscapePath(@"C:\\tmp\\helper.exe") + "\"}");
                var scanner = new FileSystemHelperSessionFileScanner(new TestHelperPaths(temp.Root));
                var detector = new DefaultActiveHelperDetector(
                    new FakeInstallIdProvider(Guid.NewGuid()),
                    new FakeMutexFactory(true),
                    scanner,
                    new FakeProcessInspector(false, null));

                var detection = detector.Detect();

                Assert.False(detection.Active);
                Assert.True(File.Exists(sessionFile));
            }
        }

        [Fact]
        public void test_reset_local_token_writes_new_64_char_lower_hex_dpapi_token()
        {
            var fixture = CreateCommand(randomBytes: new DeterministicRandomBytes());
            fixture.Console.QueueInput("y");

            Assert.Equal(0, fixture.Command.Run());

            Assert.Equal("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f", fixture.Token.WrittenToken);
            Assert.Equal(64, fixture.Token.WrittenToken.Length);
            Assert.Equal(fixture.Token.WrittenToken.ToLowerInvariant(), fixture.Token.WrittenToken);
        }

        [Fact]
        public void test_reset_local_token_never_prints_or_audits_token_value()
        {
            var randomBytes = new DeterministicRandomBytes();
            var fixture = CreateCommand(randomBytes: randomBytes);
            fixture.Console.QueueInput("y");

            Assert.Equal(0, fixture.Command.Run());

            var token = fixture.Token.WrittenToken;
            Assert.Equal("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f", token);
            Assert.DoesNotContain(token, string.Join("\n", fixture.Console.Written));
            Assert.DoesNotContain(token, string.Join("\n", fixture.Errors.Messages));
            foreach (var auditEvent in fixture.Audit.Events)
            {
                Assert.NotEqual(token, auditEvent.AppliedFieldsJson);
                Assert.NotEqual(token, auditEvent.FailedFieldsJson);
                Assert.NotEqual(token, auditEvent.DrawingPath);
                Assert.NotEqual(token, auditEvent.PullId);
                Assert.NotEqual(token, auditEvent.ItemId);
                Assert.NotEqual(token, auditEvent.ProfileId);
                Assert.NotEqual(token, auditEvent.CadSystem);
                Assert.NotEqual(token, auditEvent.ErrorCode);
                Assert.NotEqual(token, auditEvent.TraceId);
            }
        }

        [Fact]
        public void test_reset_local_token_writes_internal_audit_ok_event_after_success()
        {
            var fixture = CreateCommand();
            fixture.Console.QueueInput("Y");

            Assert.Equal(0, fixture.Command.Run());

            var auditEvent = fixture.Audit.Events.Single();
            Assert.Equal(ResetLocalTokenCommand.AuditEndpoint, auditEvent.Endpoint);
            Assert.Equal("ok", auditEvent.Outcome);
            Assert.Null(auditEvent.ErrorCode);
            Assert.True(auditEvent.DurationMs >= 0);
            Assert.Matches("^[0-9a-f]{32}$", auditEvent.TraceId);
            Assert.Null(auditEvent.DrawingPath);
            Assert.Null(auditEvent.ProfileId);
            Assert.Null(auditEvent.ItemId);
            Assert.Null(auditEvent.PullId);
            Assert.Null(auditEvent.CadSystem);
            Assert.Null(auditEvent.AppliedFieldsJson);
            Assert.Null(auditEvent.FailedFieldsJson);
        }

        [Fact]
        public void test_reset_local_token_writes_internal_audit_error_event_for_refusals_and_failures()
        {
            var cancelled = CreateCommand();
            cancelled.Console.QueueInput("n");
            Assert.Equal(1, cancelled.Command.Run());
            Assert.Equal("error", cancelled.Audit.Events.Single().Outcome);
            Assert.Equal(ErrorCodes.HelperResetCancelled, cancelled.Audit.Events.Single().ErrorCode);

            var helperRunning = CreateCommand(detection: ActiveHelperDetection.ActiveDetected("mutex held"));
            helperRunning.Console.QueueInput("y");
            Assert.Equal(1, helperRunning.Command.Run());
            Assert.Equal(ErrorCodes.HelperResetHelperRunning, helperRunning.Audit.Events.Single().ErrorCode);

            var dpapiFail = CreateCommand(tokenStore: new FakeLocalTokenStore { WriteThrows = new HelperException(ErrorCodes.HelperLocalTokenBootstrapFailed, "no DPAPI", false) });
            dpapiFail.Console.QueueInput("y");
            Assert.Equal(1, dpapiFail.Command.Run());
            Assert.Equal(ErrorCodes.HelperLocalTokenBootstrapFailed, dpapiFail.Audit.Events.Single().ErrorCode);

            var remoteShell = CreateCommand(invocation: BaseInteractive(env: new Dictionary<string, string> { { "SSH_CLIENT", "x" } }));
            Assert.Equal(1, remoteShell.Command.Run());
            Assert.Equal(ErrorCodes.HelperResetRequiresInteractive, remoteShell.Audit.Events.Single().ErrorCode);
        }

        [Fact]
        public void test_reset_local_token_success_audit_failure_warns_but_keeps_success_exit_code()
        {
            var fixture = CreateCommand(audit: new RecordingAuditStore { ThrowOnWrite = true });
            fixture.Console.QueueInput("y");

            Assert.Equal(0, fixture.Command.Run());

            Assert.Equal(1, fixture.Token.WriteCalls);
            var warningLine = Assert.Single(fixture.Warnings.Lines);
            Assert.Contains("[AUDIT_WRITE_FAILED]", warningLine);
            Assert.Contains("endpoint=internal:reset-local-token", warningLine);
            Assert.DoesNotContain(fixture.Token.WrittenToken, warningLine);

            var failedRefusal = CreateCommand(audit: new RecordingAuditStore { ThrowOnWrite = true });
            failedRefusal.Console.QueueInput("n");
            Assert.Equal(1, failedRefusal.Command.Run());
            Assert.Contains("[AUDIT_WRITE_FAILED]", failedRefusal.Warnings.Lines.Single());
        }

        [Fact]
        public void test_reset_local_token_has_no_http_route_or_named_pipe_trigger()
        {
            var sources = ReadHelperSources();

            Assert.DoesNotContain("MapGet(\"/admin/reset-token\"", sources);
            Assert.DoesNotContain("MapPost(\"/admin/reset-token\"", sources);
            Assert.DoesNotContain("MapGet(\"/reset-local-token\"", sources);
            Assert.DoesNotContain("MapPost(\"/reset-local-token\"", sources);
            Assert.DoesNotContain("NamedPipeServerStream", sources);
            Assert.DoesNotContain("NamedPipeClientStream", sources);
            var sharedSources = ReadSharedSources();
            Assert.DoesNotContain("--reset-local-token", sharedSources);
        }

        [Fact]
        public async Task test_reset_local_token_does_not_start_kestrel_or_publish_session_file()
        {
            var resetCommand = new RecordingResetCommand();
            var sessions = new FakeSessionFileStore();
            var host = new RecordingHostRunner();
            var runtime = CreateRuntime(new RecordingErrorWriter(), host, resetCommand, sessionFiles: sessions);

            var exit = await HelperCommand.RunAsync(new[] { "--reset-local-token" }, runtime, CancellationToken.None);

            Assert.Equal(0, exit);
            Assert.Equal(1, resetCommand.RunCalls);
            Assert.Equal(0, host.RunCalls);
            Assert.Equal(0, sessions.PublishCalls);
            Assert.Equal(0, sessions.DeleteCalls);
        }

        [Fact]
        public void test_s7_preserves_s6_contracts_with_g1a_document_routes()
        {
            var sources = ReadHelperSources();

            Assert.Equal(13, CountOccurrences(sources, "MapGet(") + CountOccurrences(sources, "MapPost("));
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
            Assert.Contains("MapPost(\"/document/checkout\"", sources);
            Assert.Contains("MapPost(\"/document/undo-checkout\"", sources);
            Assert.Contains("MapPost(\"/document/status\"", sources);
            Assert.Contains("audit_events", sources);
            Assert.Contains("SqliteAuditEventStore", sources);
            Assert.Contains("HelperBusinessAuditService", sources);
        }

        [Fact]
        public void test_reset_invocation_context_walks_real_parent_ancestry()
        {
            var invocation = new DefaultResetInvocationContext();

            var ancestry = invocation.CollectLauncherProcessImageNames();

            Assert.NotNull(ancestry);
            Assert.True(
                ancestry.Count >= 2,
                "DefaultResetInvocationContext must walk at least one parent process so wsmprovhost/winrshost/sshd detection is wired; got [" + string.Join(", ", ancestry) + "].");

            var sources = ReadHelperSources();
            Assert.Contains("NtQueryInformationProcess", sources);
            Assert.Contains("InheritedFromUniqueProcessId", sources);
            Assert.Contains("MaxAncestryDepth", sources);
        }

        [Fact]
        public void test_s7_keeps_cad_helper_dotnet_workflow_covering_helper_tests()
        {
            var workflow = File.ReadAllText(Path.Combine(FindRepoRoot(), ".github", "workflows", "cad-helper-shared-dotnet.yml"));

            Assert.Contains("clients/cad-desktop-helper/Helper/**", workflow);
            Assert.Contains("clients/cad-desktop-helper/Helper.Tests/**", workflow);
            Assert.Contains("dotnet build clients/cad-desktop-helper/Helper/Yuantus.Cad.Helper.csproj", workflow);
            Assert.Contains("dotnet test clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj", workflow);
        }

        private static ResetFixture CreateCommand(
            FakeLocalTokenStore tokenStore = null,
            DeterministicRandomBytes randomBytes = null,
            ActiveHelperDetection detection = null,
            RecordingAuditStore audit = null,
            RecordingAuditWarnings warnings = null,
            QueueingResetConsole console = null,
            FakeInvocationContext invocation = null,
            FakeClock clock = null,
            RecordingErrorWriter errors = null)
        {
            tokenStore = tokenStore ?? new FakeLocalTokenStore();
            randomBytes = randomBytes ?? new DeterministicRandomBytes();
            detection = detection ?? ActiveHelperDetection.NotActive();
            audit = audit ?? new RecordingAuditStore();
            warnings = warnings ?? new RecordingAuditWarnings();
            console = console ?? new QueueingResetConsole();
            invocation = invocation ?? BaseInteractive();
            clock = clock ?? new FakeClock(DateTimeOffset.Parse("2026-05-22T10:00:00Z"));
            errors = errors ?? new RecordingErrorWriter();

            var detector = new StubActiveHelperDetector(detection);
            var command = new ResetLocalTokenCommand(
                tokenStore,
                randomBytes,
                detector,
                audit,
                warnings,
                console,
                invocation,
                clock,
                errors);
            return new ResetFixture(command, tokenStore, audit, warnings, console, errors, randomBytes);
        }

        private static FakeInvocationContext BaseInteractive(
            Dictionary<string, string> env = null,
            string[] launcherNames = null)
        {
            return new FakeInvocationContext
            {
                IsInputRedirected = false,
                IsUserInteractive = true,
                EnvironmentVariables = env ?? new Dictionary<string, string>(),
                LauncherNames = launcherNames ?? new string[0]
            };
        }

        private static HelperRuntime CreateRuntime(
            RecordingErrorWriter errorWriter,
            RecordingHostRunner host,
            IResetLocalTokenCommand reset,
            FakeSessionFileStore sessionFiles = null)
        {
            return new HelperRuntime(
                new TestHelperPaths(Path.Combine(Path.GetTempPath(), "yuantus-cad-helper-tests-" + Guid.NewGuid().ToString("N"))),
                new FakeInstallIdProvider(Guid.NewGuid()),
                new FakeLocalTokenStore(),
                new DeterministicRandomBytes(),
                new FakePortAllocator(7959),
                sessionFiles ?? new FakeSessionFileStore(),
                new FakeMutexFactory(true),
                new FakeHealthProbe(false),
                new FakeProcessInspector(false, null),
                new RecordingDelay(),
                new FakeClock(DateTimeOffset.Parse("2026-05-22T10:00:00Z")),
                errorWriter,
                host,
                reset);
        }

        private static string ReadHelperSources()
        {
            return ReadCsharpSources(Path.Combine(FindRepoRoot(), "clients", "cad-desktop-helper", "Helper"));
        }

        private static string ReadSharedSources()
        {
            return ReadCsharpSources(Path.Combine(FindRepoRoot(), "clients", "cad-desktop-helper", "Shared"));
        }

        private static string ReadCsharpSources(string root)
        {
            return string.Join(
                "\n",
                Directory.GetFiles(root, "*.cs", SearchOption.AllDirectories)
                    .Where(path => !path.Contains(Path.DirectorySeparatorChar + "bin" + Path.DirectorySeparatorChar.ToString()))
                    .Where(path => !path.Contains(Path.DirectorySeparatorChar + "obj" + Path.DirectorySeparatorChar.ToString()))
                    .OrderBy(path => path)
                    .Select(File.ReadAllText));
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

        private static string EscapePath(string path)
        {
            return path.Replace("\\", "\\\\");
        }

        private sealed class ResetFixture
        {
            public ResetFixture(
                ResetLocalTokenCommand command,
                FakeLocalTokenStore token,
                RecordingAuditStore audit,
                RecordingAuditWarnings warnings,
                QueueingResetConsole console,
                RecordingErrorWriter errors,
                DeterministicRandomBytes randomBytes)
            {
                Command = command;
                Token = token;
                Audit = audit;
                Warnings = warnings;
                Console = console;
                Errors = errors;
                RandomBytes = randomBytes;
            }

            public ResetLocalTokenCommand Command { get; private set; }
            public FakeLocalTokenStore Token { get; private set; }
            public RecordingAuditStore Audit { get; private set; }
            public RecordingAuditWarnings Warnings { get; private set; }
            public QueueingResetConsole Console { get; private set; }
            public RecordingErrorWriter Errors { get; private set; }
            public DeterministicRandomBytes RandomBytes { get; private set; }

            public string LastErrorCode()
            {
                var last = Errors.Messages.LastOrDefault();
                if (string.IsNullOrEmpty(last))
                {
                    return null;
                }
                var colon = last.IndexOf(':');
                return colon < 0 ? last : last.Substring(0, colon);
            }
        }

        private sealed class FakeLocalTokenStore : ILocalTokenStore
        {
            public string ExistingToken { get; set; }
            public string WrittenToken { get; private set; }
            public int WriteCalls { get; private set; }
            public HelperException WriteThrows { get; set; }

            public string Read()
            {
                return ExistingToken;
            }

            public void Write(string hexToken)
            {
                WriteCalls++;
                if (WriteThrows != null)
                {
                    throw WriteThrows;
                }
                WrittenToken = hexToken;
            }
        }

        private sealed class DeterministicRandomBytes : IRandomBytes
        {
            public byte[] GetBytes(int count)
            {
                var bytes = new byte[count];
                for (var i = 0; i < count; i++)
                {
                    bytes[i] = (byte)i;
                }
                return bytes;
            }
        }

        private sealed class StubActiveHelperDetector : IActiveHelperDetector
        {
            private readonly ActiveHelperDetection _detection;

            public StubActiveHelperDetector(ActiveHelperDetection detection)
            {
                _detection = detection;
            }

            public ActiveHelperDetection Detect()
            {
                return _detection;
            }
        }

        private sealed class StubSessionFileScanner : IHelperSessionFileScanner
        {
            private readonly IReadOnlyList<HelperSessionFileRecord> _records;

            public StubSessionFileScanner(IReadOnlyList<HelperSessionFileRecord> records)
            {
                _records = records;
            }

            public IReadOnlyList<HelperSessionFileRecord> Scan()
            {
                return _records;
            }
        }

        private sealed class RecordingAuditStore : IAuditEventStore
        {
            public bool ThrowOnWrite { get; set; }
            public List<AuditEvent> Events { get; private set; } = new List<AuditEvent>();

            public void Write(AuditEvent auditEvent)
            {
                if (ThrowOnWrite)
                {
                    throw new InvalidOperationException("audit write failed");
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

        private sealed class QueueingResetConsole : IResetConsole
        {
            private readonly Queue<string> _inputs = new Queue<string>();
            public List<string> Written { get; private set; } = new List<string>();

            public void QueueInput(string input)
            {
                _inputs.Enqueue(input);
            }

            public string ReadLine()
            {
                return _inputs.Count == 0 ? null : _inputs.Dequeue();
            }

            public void WriteLine(string message)
            {
                Written.Add(message);
            }
        }

        private sealed class FakeInvocationContext : IResetInvocationContext
        {
            public bool IsInputRedirected { get; set; }
            public bool IsUserInteractive { get; set; }
            public Dictionary<string, string> EnvironmentVariables { get; set; } = new Dictionary<string, string>();
            public string[] LauncherNames { get; set; } = new string[0];

            public string GetEnvironmentVariable(string name)
            {
                string value;
                return EnvironmentVariables.TryGetValue(name, out value) ? value : null;
            }

            public IReadOnlyCollection<string> CollectLauncherProcessImageNames()
            {
                return LauncherNames;
            }
        }

        private sealed class FakeClock : IClock
        {
            public FakeClock(DateTimeOffset utcNow)
            {
                UtcNow = utcNow;
            }

            public DateTimeOffset UtcNow { get; set; }
        }

        private sealed class RecordingErrorWriter : IErrorWriter
        {
            public List<string> Messages { get; private set; } = new List<string>();

            public void Write(string code, string message)
            {
                Messages.Add(code + ": " + message);
            }
        }

        private sealed class RecordingHostRunner : IHelperHostRunner
        {
            public int RunCalls { get; private set; }

            public Task RunAsync(
                int port,
                HelperSessionDocument session,
                TimeSpan idleTimeout,
                string localToken,
                HelperSecurityOptions securityOptions,
                IClock clock,
                IDelay delay,
                CancellationToken cancellationToken)
            {
                RunCalls++;
                return Task.CompletedTask;
            }
        }

        private sealed class RecordingResetCommand : IResetLocalTokenCommand
        {
            public int RunCalls { get; private set; }

            public int Run()
            {
                RunCalls++;
                return 0;
            }
        }

        private sealed class FakeInstallIdProvider : IInstallIdProvider
        {
            private readonly Guid _installId;

            public FakeInstallIdProvider(Guid installId)
            {
                _installId = installId;
            }

            public Guid GetOrCreate()
            {
                return _installId;
            }
        }

        private sealed class FakeMutexFactory : INamedMutexFactory
        {
            private readonly bool _acquired;

            public FakeMutexFactory(bool acquired)
            {
                _acquired = acquired;
            }

            public INamedMutexLease TryAcquire(string name)
            {
                return new FakeLease(_acquired);
            }

            private sealed class FakeLease : INamedMutexLease
            {
                public FakeLease(bool acquired)
                {
                    Acquired = acquired;
                }

                public bool Acquired { get; private set; }

                public void Dispose()
                {
                }
            }
        }

        private sealed class FakeProcessInspector : IProcessInspector
        {
            private readonly bool _exists;
            private readonly string _imagePath;

            public FakeProcessInspector(bool exists, string imagePath)
            {
                _exists = exists;
                _imagePath = imagePath;
            }

            public ProcessSnapshot Inspect(int pid)
            {
                return new ProcessSnapshot(_exists, _imagePath);
            }
        }

        private sealed class FakeHealthProbe : IHealthProbe
        {
            private readonly bool _healthy;

            public FakeHealthProbe(bool healthy)
            {
                _healthy = healthy;
            }

            public Task<bool> IsHealthyAsync(int port, CancellationToken cancellationToken)
            {
                return Task.FromResult(_healthy);
            }
        }

        private sealed class RecordingDelay : IDelay
        {
            public Task DelayAsync(TimeSpan delay, CancellationToken cancellationToken)
            {
                return Task.CompletedTask;
            }
        }

        private sealed class FakeSessionFileStore : ISessionFileStore
        {
            public HelperSessionDocument Current { get; set; }
            public int PublishCalls { get; private set; }
            public int DeleteCalls { get; private set; }

            public HelperSessionDocument Read()
            {
                return Current;
            }

            public void Publish(HelperSessionDocument session)
            {
                PublishCalls++;
                Current = session;
            }

            public void DeleteCurrent()
            {
                DeleteCalls++;
                Current = null;
            }
        }

        private sealed class FakePortAllocator : IPortAllocator
        {
            private readonly int _port;

            public FakePortAllocator(int port)
            {
                _port = port;
            }

            public PortBinding Allocate()
            {
                return new PortBinding("127.0.0.1", _port);
            }
        }

        private sealed class TestHelperPaths : IHelperPaths
        {
            public TestHelperPaths(string root)
            {
                RootDirectory = root;
            }

            public string RootDirectory { get; private set; }

            public string HelperSessionFilePath
            {
                get { return Path.Combine(RootDirectory, "helper-session-1.json"); }
            }

            public string HelperExePath
            {
                get { return Path.Combine(RootDirectory, "helper", "yuantus-cad-helper.exe"); }
            }

            public string ConfigFilePath
            {
                get { return Path.Combine(RootDirectory, "config.json"); }
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
