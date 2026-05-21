using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Microsoft.Win32;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using Xunit;

namespace Yuantus.Cad.Detector.Tests
{
    public sealed class DetectorContractTests
    {
        [Fact]
        public void test_missing_registry_roots_are_quietly_skipped()
        {
            var runtime = TestRuntime(new FakeRegistryReader(), new FakeFileSystem());

            var report = new CadDetector(runtime.Registry, runtime.FileSystem, runtime.HostInfoFactory).Scan();

            Assert.Empty(report.Products);
            Assert.Empty(report.Warnings);
        }

        [Fact]
        public void test_autocad_2018_with_bundle_returns_supported_json()
        {
            var registry = new FakeRegistryReader();
            registry.AddKey(
                RegistryView.Registry64,
                "SOFTWARE\\Autodesk\\AutoCAD\\R22.0\\ACAD-1001:409",
                new Dictionary<string, string>
                {
                    { "ProductName", "AutoCAD 2018" },
                    { "ProductRoot", "C:\\Program Files\\Autodesk\\AutoCAD 2018" },
                    { "Language", "zh-CN" }
                });

            var fs = new FakeFileSystem();
            fs.AddFile("C:\\Program Files\\Autodesk\\AutoCAD 2018\\acad.exe", "");
            fs.SetFolder(Environment.SpecialFolder.ApplicationData, "C:\\Users\\frank\\AppData\\Roaming");
            fs.SetFolder(Environment.SpecialFolder.CommonApplicationData, "C:\\ProgramData");
            fs.AddDirectory("C:\\Users\\frank\\AppData\\Roaming\\Autodesk\\ApplicationPlugins\\CADDedup.bundle");
            fs.AddFile(
                "C:\\Users\\frank\\AppData\\Roaming\\Autodesk\\ApplicationPlugins\\CADDedup.bundle\\PackageContents.xml",
                "<ApplicationPackage AppVersion=\"0.1.0\"><Components><RuntimeRequirements SeriesMin=\"R22.0\" SeriesMax=\"R24.3\" /></Components></ApplicationPackage>");

            var runtime = TestRuntime(registry, fs);
            var report = new CadDetector(runtime.Registry, runtime.FileSystem, runtime.HostInfoFactory).Scan();

            var product = Assert.Single(report.Products);
            Assert.Equal("Autodesk", product.Vendor);
            Assert.Equal("AutoCAD", product.Product);
            Assert.Equal("R22.0", product.ReleaseKey);
            Assert.Equal("2018", product.MarketingVersion);
            Assert.Equal("supported", product.Compatibility);
            Assert.True(product.YuantusBundle.Present);
            Assert.Equal("0.1.0", product.YuantusBundle.PackageVersion);
            Assert.Contains("R22.0", product.YuantusBundle.SupportsRelease);
        }

        [Fact]
        public void test_registry64_and_registry32_duplicate_install_root_dedupes()
        {
            var registry = new FakeRegistryReader();
            foreach (var view in new[] { RegistryView.Registry64, RegistryView.Registry32 })
            {
                registry.AddKey(
                    view,
                    "SOFTWARE\\Autodesk\\AutoCAD\\R22.0\\ACAD-1001:409",
                    new Dictionary<string, string>
                    {
                        { "ProductName", "AutoCAD 2018" },
                        { "ProductRoot", "C:\\Program Files\\Autodesk\\AutoCAD 2018" }
                    });
            }

            var fs = new FakeFileSystem();
            fs.AddFile("C:\\Program Files\\Autodesk\\AutoCAD 2018\\acad.exe", "");
            var runtime = TestRuntime(registry, fs);

            var report = new CadDetector(runtime.Registry, runtime.FileSystem, runtime.HostInfoFactory).Scan();

            Assert.Single(report.Products);
        }

        [Fact]
        public void test_unknown_autocad_rkey_has_unknown_compatibility_and_empty_marketing_version()
        {
            var registry = new FakeRegistryReader();
            registry.AddKey(
                RegistryView.Registry64,
                "SOFTWARE\\Autodesk\\AutoCAD\\R26.0\\ACAD-1001:409",
                new Dictionary<string, string>
                {
                    { "ProductName", "AutoCAD Future" },
                    { "ProductRoot", "C:\\Program Files\\Autodesk\\AutoCAD Future" }
                });
            var fs = new FakeFileSystem();
            fs.AddFile("C:\\Program Files\\Autodesk\\AutoCAD Future\\acad.exe", "");
            var runtime = TestRuntime(registry, fs);

            var product = Assert.Single(new CadDetector(runtime.Registry, runtime.FileSystem, runtime.HostInfoFactory).Scan().Products);

            Assert.Equal("R26.0", product.ReleaseKey);
            Assert.Null(product.MarketingVersion);
            Assert.Equal("unknown", product.Compatibility);
        }

        [Fact]
        public void test_zwcad_detection_is_experimental_when_exe_exists()
        {
            var registry = new FakeRegistryReader();
            registry.AddKey(
                RegistryView.Registry64,
                "SOFTWARE\\ZWSOFT\\ZWCAD\\2024",
                new Dictionary<string, string>
                {
                    { "InstallPath", "C:\\Program Files\\ZWSOFT\\ZWCAD 2024" }
                });
            var fs = new FakeFileSystem();
            fs.AddFile("C:\\Program Files\\ZWSOFT\\ZWCAD 2024\\ZWCAD.exe", "");
            var runtime = TestRuntime(registry, fs);

            var product = Assert.Single(new CadDetector(runtime.Registry, runtime.FileSystem, runtime.HostInfoFactory).Scan().Products);

            Assert.Equal("ZWSOFT", product.Vendor);
            Assert.Equal("ZWCAD", product.Product);
            Assert.Equal("2024", product.MarketingVersion);
            Assert.Equal("experimental", product.Compatibility);
        }

        [Fact]
        public void test_missing_exe_is_registry_orphan()
        {
            var registry = new FakeRegistryReader();
            registry.AddKey(
                RegistryView.Registry64,
                "SOFTWARE\\Gstarsoft\\GstarCAD\\2024",
                new Dictionary<string, string>
                {
                    { "InstallPath", "C:\\Program Files\\Gstarsoft\\GstarCAD 2024" }
                });
            var runtime = TestRuntime(registry, new FakeFileSystem());

            var product = Assert.Single(new CadDetector(runtime.Registry, runtime.FileSystem, runtime.HostInfoFactory).Scan().Products);

            Assert.Equal("registry-orphan", product.Compatibility);
        }

        [Fact]
        public void test_json_output_contains_required_schema_fields()
        {
            var registry = new FakeRegistryReader();
            var fs = new FakeFileSystem();
            var runtime = TestRuntime(registry, fs);
            var stdout = new StringWriter();
            var stderr = new StringWriter();

            var exitCode = DetectorCli.Run(new string[0], stdout, stderr, runtime);

            Assert.Equal(2, exitCode);
            Assert.Equal("", stderr.ToString());
            var json = JObject.Parse(stdout.ToString());
            Assert.Equal("1.0", json.Value<string>("schema_version"));
            Assert.NotNull(json["scanned_at"]);
            Assert.NotNull(json["host"]);
            Assert.NotNull(json["products"]);
            Assert.NotNull(json["recommendations"]);
            Assert.NotNull(json["warnings"]);
        }

        [Fact]
        public void test_non_windows_returns_64()
        {
            var runtime = new DetectorRuntime(
                false,
                new FakeRegistryReader(),
                new FakeFileSystem(),
                Host);
            var stdout = new StringWriter();
            var stderr = new StringWriter();

            var exitCode = DetectorCli.Run(new string[0], stdout, stderr, runtime);

            Assert.Equal(64, exitCode);
            Assert.Contains("requires Windows", stderr.ToString());
        }

        [Fact]
        public void test_output_writes_file_and_keeps_stdout_silent()
        {
            var fs = new FakeFileSystem();
            var runtime = TestRuntime(new FakeRegistryReader(), fs);
            var stdout = new StringWriter();
            var stderr = new StringWriter();

            var exitCode = DetectorCli.Run(
                new[] { "--output", "C:\\temp\\detector.json" },
                stdout,
                stderr,
                runtime);

            Assert.Equal(2, exitCode);
            Assert.Equal("", stdout.ToString());
            Assert.True(fs.Writes.ContainsKey(Normalize("C:\\temp\\detector.json")));
        }

        [Fact]
        public void test_install_or_repair_switch_is_rejected()
        {
            var runtime = TestRuntime(new FakeRegistryReader(), new FakeFileSystem());
            var stdout = new StringWriter();
            var stderr = new StringWriter();

            var exitCode = DetectorCli.Run(new[] { "--install" }, stdout, stderr, runtime);

            Assert.Equal(1, exitCode);
            Assert.Contains("Unsupported write/install switch", stderr.ToString());
        }

        [Fact]
        public void test_table_format_is_for_human_readable_output()
        {
            var registry = new FakeRegistryReader();
            registry.AddKey(
                RegistryView.Registry64,
                "SOFTWARE\\SolidWorks\\SOLIDWORKS\\2024",
                new Dictionary<string, string>
                {
                    { "InstallDir", "C:\\Program Files\\SOLIDWORKS Corp\\SOLIDWORKS" }
                });
            var fs = new FakeFileSystem();
            fs.AddFile("C:\\Program Files\\SOLIDWORKS Corp\\SOLIDWORKS\\SLDWORKS.exe", "");
            var runtime = TestRuntime(registry, fs);
            var stdout = new StringWriter();

            var exitCode = DetectorCli.Run(new[] { "--format", "table" }, stdout, new StringWriter(), runtime);

            Assert.Equal(0, exitCode);
            Assert.Contains("Vendor\tProduct\tVersion\tCompatibility\tExePath", stdout.ToString());
            Assert.Contains("Dassault\tSolidWorks\t2024\texperimental", stdout.ToString());
        }

        [Fact]
        public void test_schema_file_documents_detector_report_contract()
        {
            var schemaPath = Path.Combine(
                AppContext.BaseDirectory,
                "..",
                "..",
                "..",
                "..",
                "Detector",
                "Schemas",
                "cad-detector-report.schema.json");

            var raw = File.ReadAllText(Path.GetFullPath(schemaPath));
            var schema = JObject.Parse(raw);

            Assert.Equal("object", schema.Value<string>("type"));
            var required = schema["required"].Values<string>().ToArray();
            Assert.Contains("schema_version", required);
            Assert.Contains("products", required);
        }

        private static DetectorRuntime TestRuntime(FakeRegistryReader registry, FakeFileSystem fileSystem)
        {
            fileSystem.SetFolder(Environment.SpecialFolder.ApplicationData, "C:\\Users\\frank\\AppData\\Roaming");
            fileSystem.SetFolder(Environment.SpecialFolder.CommonApplicationData, "C:\\ProgramData");
            return new DetectorRuntime(true, registry, fileSystem, Host);
        }

        private static HostInfo Host()
        {
            return new HostInfo
            {
                Os = "Windows 11 Pro",
                Arch = "X64",
                Username = "frank",
                IsAdmin = false
            };
        }

        private static string Normalize(string path)
        {
            return path.Replace('\\', '/').TrimEnd('/');
        }

        private sealed class FakeFileSystem : IDetectorFileSystem
        {
            private readonly HashSet<string> _files = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            private readonly HashSet<string> _directories = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            private readonly Dictionary<string, string> _contents = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            private readonly Dictionary<Environment.SpecialFolder, string> _folders = new Dictionary<Environment.SpecialFolder, string>();

            public Dictionary<string, string> Writes { get; private set; }

            public FakeFileSystem()
            {
                Writes = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            }

            public void AddFile(string path, string contents)
            {
                var normalized = Normalize(path);
                _files.Add(normalized);
                _contents[normalized] = contents;
            }

            public void AddDirectory(string path)
            {
                _directories.Add(Normalize(path));
            }

            public void SetFolder(Environment.SpecialFolder folder, string path)
            {
                _folders[folder] = path;
            }

            public bool FileExists(string path)
            {
                return path != null && _files.Contains(Normalize(path));
            }

            public bool DirectoryExists(string path)
            {
                return path != null && _directories.Contains(Normalize(path));
            }

            public string ReadAllText(string path)
            {
                return _contents[Normalize(path)];
            }

            public void WriteAllText(string path, string contents)
            {
                Writes[Normalize(path)] = contents;
            }

            public void CreateDirectory(string path)
            {
                _directories.Add(Normalize(path));
            }

            public string GetFolderPath(Environment.SpecialFolder folder)
            {
                string path;
                return _folders.TryGetValue(folder, out path) ? path : "";
            }
        }

        private sealed class FakeRegistryReader : ICadRegistryReader
        {
            private readonly Dictionary<RegistryView, FakeRegistryKey> _roots =
                new Dictionary<RegistryView, FakeRegistryKey>();

            public void AddKey(RegistryView view, string path, Dictionary<string, string> values)
            {
                FakeRegistryKey root;
                if (!_roots.TryGetValue(view, out root))
                {
                    root = new FakeRegistryKey("");
                    _roots[view] = root;
                }

                var current = root;
                foreach (var segment in path.Split(new[] { '\\' }, StringSplitOptions.RemoveEmptyEntries))
                {
                    current = current.GetOrCreate(segment);
                }
                foreach (var pair in values)
                {
                    current.Values[pair.Key] = pair.Value;
                }
            }

            public IDetectorRegistryKey OpenHklm(string subKey, RegistryView view)
            {
                FakeRegistryKey root;
                if (!_roots.TryGetValue(view, out root))
                {
                    return null;
                }

                var current = root;
                foreach (var segment in subKey.Split(new[] { '\\' }, StringSplitOptions.RemoveEmptyEntries))
                {
                    if (!current.Children.TryGetValue(segment, out current))
                    {
                        return null;
                    }
                }
                return current;
            }
        }

        private sealed class FakeRegistryKey : IDetectorRegistryKey
        {
            public FakeRegistryKey(string name)
            {
                Name = name;
                Values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                Children = new Dictionary<string, FakeRegistryKey>(StringComparer.OrdinalIgnoreCase);
            }

            public string Name { get; private set; }
            public Dictionary<string, string> Values { get; private set; }
            public Dictionary<string, FakeRegistryKey> Children { get; private set; }

            public FakeRegistryKey GetOrCreate(string name)
            {
                FakeRegistryKey child;
                if (!Children.TryGetValue(name, out child))
                {
                    child = new FakeRegistryKey(name);
                    Children[name] = child;
                }
                return child;
            }

            public string GetStringValue(string name)
            {
                string value;
                return Values.TryGetValue(name, out value) ? value : null;
            }

            public IEnumerable<string> GetSubKeyNames()
            {
                return Children.Keys;
            }

            public IDetectorRegistryKey OpenSubKey(string name)
            {
                FakeRegistryKey child;
                return Children.TryGetValue(name, out child) ? child : null;
            }

            public void Dispose()
            {
            }
        }
    }
}
