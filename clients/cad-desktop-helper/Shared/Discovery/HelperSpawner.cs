using System;
using System.Diagnostics;
using Yuantus.Cad.Shared.Identity;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Shared.Discovery
{
    public static class HelperSpawner
    {
        private static Func<ProcessStartInfo, Process> _startProcess = DefaultStartProcess;

        public static Process Spawn()
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = Paths.HelperExePath,
                Arguments = string.Empty,
                UseShellExecute = false,
                CreateNoWindow = true
            };

            try
            {
                var process = _startProcess(startInfo);
                if (process == null)
                {
                    throw new InvalidOperationException("Process.Start returned null.");
                }
                return process;
            }
            catch (Exception ex)
            {
                throw new HelperException(
                    ErrorCodes.HelperPortBusy,
                    "Unable to start yuantus-cad-helper.exe.",
                    true,
                    null,
                    ex);
            }
        }

        internal static IDisposable UseStartProcessForTesting(Func<ProcessStartInfo, Process> starter)
        {
            var previous = _startProcess;
            _startProcess = starter;
            return new RestoreAction(() => _startProcess = previous);
        }

        private static Process DefaultStartProcess(ProcessStartInfo startInfo)
        {
            return Process.Start(startInfo);
        }

        private sealed class RestoreAction : IDisposable
        {
            private readonly Action _restore;
            private bool _disposed;

            public RestoreAction(Action restore)
            {
                _restore = restore;
            }

            public void Dispose()
            {
                if (_disposed)
                {
                    return;
                }
                _disposed = true;
                _restore();
            }
        }
    }
}
