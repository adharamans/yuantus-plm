using System;
using System.Diagnostics;
using System.Threading;
using System.Threading.Tasks;
using Yuantus.Cad.Shared.Transport;

namespace Yuantus.Cad.Shared.Discovery
{
    /// <summary>
    /// Resolves a running helper base URI from the session file or starts the helper process.
    /// </summary>
    public sealed class HelperLocator : IDisposable
    {
        /// <summary>
        /// Default maximum wait for a newly spawned helper to publish a healthy session file.
        /// </summary>
        public static readonly TimeSpan DefaultMaxWait = TimeSpan.FromSeconds(5);

        /// <summary>
        /// Default poll interval while waiting for helper startup.
        /// </summary>
        public static readonly TimeSpan DefaultPollInterval = TimeSpan.FromMilliseconds(100);

        private readonly HelperProbe _probe;
        private readonly Func<HelperSessionFile> _readSessionFile;
        private readonly Func<Process> _spawn;
        private readonly TimeSpan _maxWait;
        private readonly TimeSpan _pollInterval;
        private readonly bool _disposeProbe;

        /// <summary>
        /// Initializes a locator using the default session file, spawner, and health probe.
        /// </summary>
        public HelperLocator()
            : this(new HelperProbe(), HelperSessionFile.Read, HelperSpawner.Spawn, DefaultMaxWait, DefaultPollInterval, true)
        {
        }

        /// <summary>
        /// Initializes a locator with caller-supplied primitives for tests and custom hosts.
        /// </summary>
        /// <param name="probe">Health probe to use. The caller retains ownership.</param>
        /// <param name="readSessionFile">Function that reads the current helper session file.</param>
        /// <param name="spawn">Function that starts the helper process.</param>
        /// <param name="maxWait">Maximum time to wait for a spawned helper to become healthy.</param>
        /// <param name="pollInterval">Interval between session-file probe attempts.</param>
        public HelperLocator(
            HelperProbe probe,
            Func<HelperSessionFile> readSessionFile,
            Func<Process> spawn,
            TimeSpan maxWait,
            TimeSpan pollInterval)
            : this(probe, readSessionFile, spawn, maxWait, pollInterval, false)
        {
        }

        private HelperLocator(
            HelperProbe probe,
            Func<HelperSessionFile> readSessionFile,
            Func<Process> spawn,
            TimeSpan maxWait,
            TimeSpan pollInterval,
            bool disposeProbe)
        {
            _probe = probe;
            _readSessionFile = readSessionFile;
            _spawn = spawn;
            _maxWait = maxWait;
            _pollInterval = pollInterval;
            _disposeProbe = disposeProbe;
        }

        /// <summary>
        /// Returns the base URI of an existing healthy helper, or starts one and waits for health.
        /// </summary>
        /// <param name="cancellationToken">Cancellation token for startup and probe polling.</param>
        /// <returns>Base URI for the local helper.</returns>
        public async Task<Uri> EnsureHelperRunningAsync(CancellationToken cancellationToken)
        {
            var existing = _readSessionFile();
            if (existing != null)
            {
                var probe = await _probe.HealthAsync(existing.Port, cancellationToken).ConfigureAwait(false);
                if (probe.IsHealthy)
                {
                    return existing.ToBaseUri();
                }
            }

            _spawn();
            var stopwatch = Stopwatch.StartNew();
            while (stopwatch.Elapsed < _maxWait)
            {
                cancellationToken.ThrowIfCancellationRequested();
                var current = _readSessionFile();
                if (current != null)
                {
                    var probe = await _probe.HealthAsync(current.Port, cancellationToken).ConfigureAwait(false);
                    if (probe.IsHealthy)
                    {
                        return current.ToBaseUri();
                    }
                }
                await Task.Delay(_pollInterval, cancellationToken).ConfigureAwait(false);
            }

            throw new HelperException(
                ErrorCodes.HelperPortBusy,
                "Timed out waiting for yuantus-cad-helper.exe /healthz.",
                true);
        }

        /// <summary>
        /// Releases the owned probe when this locator created it.
        /// </summary>
        public void Dispose()
        {
            if (_disposeProbe)
            {
                _probe.Dispose();
            }
        }
    }
}
