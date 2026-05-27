using System;
using System.IO;

namespace Yuantus.Cad.Bridge
{
    /// <summary>
    /// Narrow seam for reading the bytes of a caller-owned local file for the
    /// Slice B <c>yuantus-helper-upload</c> primitive. The bridge (in-CAD-host
    /// process, client side) reads the user's own saved document and uploads
    /// the bytes over multipart; this is the caller holding its own bytes,
    /// distinct from the helper reading an arbitrary path (which G1-B/G1-C
    /// forbid and which the helper-side guards keep blocked).
    /// </summary>
    /// <remarks>
    /// Failures surface as a fixed reason <b>token</b> (never the path itself),
    /// so the sanitized command-line writer line cannot leak local filesystem
    /// content. Unit tests inject a fake; the production
    /// <see cref="BridgeFileSource"/> touches the real filesystem and is
    /// covered by temp-file tests in Bridge.Tests.
    /// </remarks>
    public interface IBridgeFileSource
    {
        /// <summary>
        /// Attempts to read all bytes of <paramref name="path"/>. Returns
        /// <c>true</c> with <paramref name="bytes"/> populated on success;
        /// otherwise <c>false</c> with a fixed <paramref name="reasonToken"/>
        /// (one of <c>file_path_missing</c>, <c>file_not_regular</c>,
        /// <c>file_missing</c>, <c>file_read_error</c>) and no path content.
        /// </summary>
        bool TryReadAllBytes(string path, out byte[] bytes, out string reasonToken);
    }

    /// <summary>
    /// Production <see cref="IBridgeFileSource"/>. Validates a non-empty path,
    /// rejects directories as not-regular, requires the file to exist, then
    /// reads the bytes. Any read exception maps to <c>file_read_error</c>. No
    /// branch echoes the supplied path into the reason token.
    /// </summary>
    public sealed class BridgeFileSource : IBridgeFileSource
    {
        public bool TryReadAllBytes(string path, out byte[] bytes, out string reasonToken)
        {
            bytes = null;
            reasonToken = null;

            if (string.IsNullOrWhiteSpace(path))
            {
                reasonToken = "file_path_missing";
                return false;
            }
            if (Directory.Exists(path))
            {
                reasonToken = "file_not_regular";
                return false;
            }
            if (!File.Exists(path))
            {
                reasonToken = "file_missing";
                return false;
            }
            try
            {
                bytes = File.ReadAllBytes(path);
                return true;
            }
            catch (Exception)
            {
                bytes = null;
                reasonToken = "file_read_error";
                return false;
            }
        }
    }
}
