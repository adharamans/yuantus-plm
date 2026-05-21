using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json;

namespace Yuantus.Cad.Detector
{
    public static class DetectorCli
    {
        public static int Run(string[] args, TextWriter stdout, TextWriter stderr, DetectorRuntime runtime)
        {
            DetectorOptions options;
            var parseError = DetectorOptions.TryParse(args, out options);
            if (parseError != null)
            {
                stderr.WriteLine(parseError);
                return 1;
            }

            if (!runtime.IsWindows)
            {
                stderr.WriteLine("yuantus-cad-detector requires Windows.");
                return 64;
            }

            try
            {
                var detector = new CadDetector(runtime.Registry, runtime.FileSystem, runtime.HostInfoFactory);
                var report = detector.Scan();
                var output = options.Format == "table"
                    ? TableFormatter.Format(report)
                    : JsonConvert.SerializeObject(report, Formatting.Indented);

                if (!string.IsNullOrWhiteSpace(options.OutputPath))
                {
                    var parent = Path.GetDirectoryName(options.OutputPath);
                    if (!string.IsNullOrWhiteSpace(parent))
                    {
                        runtime.FileSystem.CreateDirectory(parent);
                    }
                    runtime.FileSystem.WriteAllText(options.OutputPath, output);
                }
                else
                {
                    stdout.WriteLine(output);
                }

                return report.Products != null && report.Products.Count > 0 ? 0 : 2;
            }
            catch (Exception ex)
            {
                stderr.WriteLine(ex.Message);
                return 1;
            }
        }
    }

    internal sealed class DetectorOptions
    {
        private static readonly string[] ForbiddenSwitches =
        {
            "--install", "--repair", "--fix", "--write", "--register", "--uninstall"
        };

        public string OutputPath { get; private set; }
        public string Format { get; private set; }
        public bool Verbose { get; private set; }

        public static string TryParse(string[] args, out DetectorOptions options)
        {
            options = new DetectorOptions { Format = "json" };
            args = args ?? new string[0];

            for (var i = 0; i < args.Length; i++)
            {
                var arg = args[i];
                if (ForbiddenSwitches.Any(x => string.Equals(x, arg, StringComparison.OrdinalIgnoreCase)))
                {
                    return "Unsupported write/install switch: " + arg;
                }

                if (string.Equals(arg, "--output", StringComparison.OrdinalIgnoreCase))
                {
                    if (i + 1 >= args.Length)
                    {
                        return "--output requires a path.";
                    }
                    options.OutputPath = args[++i];
                    continue;
                }

                if (string.Equals(arg, "--format", StringComparison.OrdinalIgnoreCase))
                {
                    if (i + 1 >= args.Length)
                    {
                        return "--format requires json or table.";
                    }
                    var format = args[++i].Trim().ToLowerInvariant();
                    if (format != "json" && format != "table")
                    {
                        return "--format requires json or table.";
                    }
                    options.Format = format;
                    continue;
                }

                if (string.Equals(arg, "--verbose", StringComparison.OrdinalIgnoreCase))
                {
                    options.Verbose = true;
                    continue;
                }

                return "Unknown argument: " + arg;
            }

            return null;
        }
    }

    internal static class TableFormatter
    {
        public static string Format(DetectorReport report)
        {
            var writer = new StringWriter();
            writer.WriteLine("Vendor\tProduct\tVersion\tCompatibility\tExePath");
            if (report.Products != null)
            {
                foreach (var product in report.Products)
                {
                    writer.WriteLine(
                        "{0}\t{1}\t{2}\t{3}\t{4}",
                        product.Vendor,
                        product.Product,
                        product.MarketingVersion ?? product.ReleaseKey ?? "",
                        product.Compatibility,
                        product.ExePath ?? "");
                }
            }
            return writer.ToString().TrimEnd();
        }
    }
}
