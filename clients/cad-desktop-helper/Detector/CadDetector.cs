using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Microsoft.Win32;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace Yuantus.Cad.Detector
{
    public sealed class CadDetector
    {
        private static readonly Dictionary<string, string> AutoCadMarketingVersions =
            new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
            {
                { "R22.0", "2018" },
                { "R23.0", "2019" },
                { "R23.1", "2020" },
                { "R24.0", "2021" },
                { "R24.1", "2022" },
                { "R24.2", "2023" },
                { "R24.3", "2024" },
                { "R25.0", "2025" }
            };

        private static readonly string[] AutoCadReleaseOrder =
        {
            "R22.0", "R23.0", "R23.1", "R24.0", "R24.1", "R24.2", "R24.3", "R25.0"
        };

        private static readonly HashSet<string> R3SupportedAutoCadReleases =
            new HashSet<string>(new[] { "R22.0", "R24.3" }, StringComparer.OrdinalIgnoreCase);

        private readonly ICadRegistryReader _registry;
        private readonly IDetectorFileSystem _fileSystem;
        private readonly Func<HostInfo> _hostInfoFactory;

        public CadDetector(ICadRegistryReader registry, IDetectorFileSystem fileSystem, Func<HostInfo> hostInfoFactory)
        {
            _registry = registry;
            _fileSystem = fileSystem;
            _hostInfoFactory = hostInfoFactory;
        }

        public DetectorReport Scan()
        {
            var products = new List<DetectedProduct>();
            var warnings = new List<DetectorMessage>();

            ScanAutoCad(products, warnings);
            ScanYearKeyProduct(products, warnings, YearKeyProduct.ZwCad());
            ScanYearKeyProduct(products, warnings, YearKeyProduct.GstarCad());
            ScanYearKeyProduct(products, warnings, YearKeyProduct.SolidWorks());

            products = Deduplicate(products);

            var recommendations = new List<DetectorMessage>();
            if (!products.Any(p => string.Equals(p.Compatibility, "supported", StringComparison.OrdinalIgnoreCase)))
            {
                recommendations.Add(new DetectorMessage
                {
                    Level = "info",
                    Code = "HELPER_NOT_INSTALLED",
                    Message = "No supported Yuantus CAD bundle was detected. Use the companion installer when helper integration is required."
                });
            }

            return new DetectorReport
            {
                SchemaVersion = "1.0",
                ScannedAt = DateTimeOffset.Now,
                Host = _hostInfoFactory(),
                Products = products,
                Recommendations = recommendations,
                Warnings = warnings
            };
        }

        private void ScanAutoCad(List<DetectedProduct> products, List<DetectorMessage> warnings)
        {
            foreach (var view in RegistryViews())
            {
                using (var root = OpenHklmQuietly("SOFTWARE\\Autodesk\\AutoCAD", view, warnings))
                {
                    if (root == null)
                    {
                        continue;
                    }

                    foreach (var releaseKey in SafeSubKeyNames(root, warnings, "AUTOCAD_RELEASE_ENUM_FAILED"))
                    {
                        using (var release = root.OpenSubKey(releaseKey))
                        {
                            if (release == null)
                            {
                                continue;
                            }

                            foreach (var profileKey in SafeSubKeyNames(release, warnings, "AUTOCAD_PROFILE_ENUM_FAILED"))
                            {
                                using (var profile = release.OpenSubKey(profileKey))
                                {
                                    if (profile == null)
                                    {
                                        continue;
                                    }
                                    products.Add(BuildAutoCadProduct(releaseKey, profile));
                                }
                            }
                        }
                    }
                }
            }
        }

        private DetectedProduct BuildAutoCadProduct(string releaseKey, IDetectorRegistryKey profile)
        {
            string marketingVersion;
            AutoCadMarketingVersions.TryGetValue(releaseKey, out marketingVersion);

            var productName = FirstNonEmpty(
                profile.GetStringValue("ProductName"),
                profile.GetStringValue("Product"));
            if (string.IsNullOrWhiteSpace(productName))
            {
                productName = "AutoCAD";
            }

            var product = productName.StartsWith("AutoCAD Plant 3D", StringComparison.OrdinalIgnoreCase)
                ? "AutoCAD Plant 3D"
                : "AutoCAD";

            var installRoot = NormalizePath(FirstNonEmpty(
                profile.GetStringValue("ProductRoot"),
                profile.GetStringValue("AcadLocation"),
                profile.GetStringValue("InstallLocation"),
                profile.GetStringValue("InstallPath")));
            var exePath = ResolveExePath(installRoot, profile.GetStringValue("AcadLocation"), "acad.exe");
            var language = NormalizeLanguage(profile.GetStringValue("Language"));
            var supportDirs = AutoCadSupportDirs(installRoot, marketingVersion, releaseKey, language);
            var pluginDirs = AutodeskPluginDirs();
            var bundle = DetectBundle(pluginDirs, releaseKey);
            var exeExists = !string.IsNullOrWhiteSpace(exePath) && _fileSystem.FileExists(exePath);

            return new DetectedProduct
            {
                Id = ProductId(product, marketingVersion, language, releaseKey),
                Vendor = "Autodesk",
                Product = product,
                ReleaseKey = releaseKey,
                MarketingVersion = marketingVersion,
                Language = language,
                InstallRoot = installRoot,
                ExePath = exePath,
                SupportDirs = supportDirs,
                PluginBundleDirs = pluginDirs,
                YuantusBundle = bundle,
                Compatibility = AutoCadCompatibility(releaseKey, exeExists, bundle),
                Errors = new List<DetectorMessage>()
            };
        }

        private void ScanYearKeyProduct(List<DetectedProduct> products, List<DetectorMessage> warnings, YearKeyProduct source)
        {
            foreach (var view in RegistryViews())
            {
                using (var root = OpenHklmQuietly(source.RegistryRoot, view, warnings))
                {
                    if (root == null)
                    {
                        continue;
                    }

                    foreach (var year in SafeSubKeyNames(root, warnings, source.ErrorCode))
                    {
                        using (var key = root.OpenSubKey(year))
                        {
                            if (key == null)
                            {
                                continue;
                            }
                            products.Add(BuildYearKeyProduct(source, year, key));
                        }
                    }
                }
            }
        }

        private DetectedProduct BuildYearKeyProduct(YearKeyProduct source, string year, IDetectorRegistryKey key)
        {
            var installRoot = NormalizePath(FirstNonEmpty(
                key.GetStringValue("InstallDir"),
                key.GetStringValue("InstallLocation"),
                key.GetStringValue("InstallPath"),
                key.GetStringValue("ProductRoot"),
                key.GetStringValue("Path")));
            var exePath = ResolveExePath(installRoot, key.GetStringValue("ExePath"), source.ExeName);
            var exeExists = !string.IsNullOrWhiteSpace(exePath) && _fileSystem.FileExists(exePath);

            return new DetectedProduct
            {
                Id = ProductId(source.Product, year, null, null),
                Vendor = source.Vendor,
                Product = source.Product,
                ReleaseKey = null,
                MarketingVersion = year,
                Language = null,
                InstallRoot = installRoot,
                ExePath = exePath,
                SupportDirs = new List<string>(),
                PluginBundleDirs = new List<string>(),
                YuantusBundle = new YuantusBundleInfo { Present = false, SupportsRelease = new List<string>() },
                Compatibility = exeExists ? "experimental" : "registry-orphan",
                Errors = new List<DetectorMessage>()
            };
        }

        private IDetectorRegistryKey OpenHklmQuietly(string subKey, RegistryView view, List<DetectorMessage> warnings)
        {
            try
            {
                return _registry.OpenHklm(subKey, view);
            }
            catch (Exception ex)
            {
                warnings.Add(new DetectorMessage
                {
                    Level = "warning",
                    Code = "REGISTRY_READ_FAILED",
                    Message = subKey + " (" + view + "): " + ex.Message
                });
                return null;
            }
        }

        private static IEnumerable<string> SafeSubKeyNames(IDetectorRegistryKey key, List<DetectorMessage> warnings, string code)
        {
            try
            {
                return key.GetSubKeyNames() ?? new string[0];
            }
            catch (Exception ex)
            {
                warnings.Add(new DetectorMessage
                {
                    Level = "warning",
                    Code = code,
                    Message = ex.Message
                });
                return new string[0];
            }
        }

        private YuantusBundleInfo DetectBundle(List<string> pluginDirs, string releaseKey)
        {
            foreach (var pluginDir in pluginDirs)
            {
                if (string.IsNullOrWhiteSpace(pluginDir))
                {
                    continue;
                }

                var bundlePath = Path.Combine(pluginDir, "CADDedup.bundle");
                if (!_fileSystem.DirectoryExists(bundlePath))
                {
                    continue;
                }

                var packagePath = Path.Combine(bundlePath, "PackageContents.xml");
                var supports = new List<string>();
                string version = null;
                if (_fileSystem.FileExists(packagePath))
                {
                    try
                    {
                        var package = _fileSystem.ReadAllText(packagePath);
                        version = ReadPackageVersion(package);
                        supports = ReadSupportedReleases(package);
                    }
                    catch
                    {
                        supports = new List<string>();
                    }
                }

                return new YuantusBundleInfo
                {
                    Present = true,
                    Path = bundlePath,
                    PackageVersion = version,
                    SupportsRelease = supports
                };
            }

            return new YuantusBundleInfo
            {
                Present = false,
                SupportsRelease = new List<string>()
            };
        }

        private static string ReadPackageVersion(string packageContents)
        {
            try
            {
                var document = System.Xml.Linq.XDocument.Parse(packageContents);
                var root = document.Root;
                if (root == null)
                {
                    return null;
                }
                return AttributeValue(root, "AppVersion") ??
                       AttributeValue(root, "PackageVersion") ??
                       AttributeValue(root, "Version");
            }
            catch
            {
                return null;
            }
        }

        private static List<string> ReadSupportedReleases(string packageContents)
        {
            var releases = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            try
            {
                var document = System.Xml.Linq.XDocument.Parse(packageContents);
                foreach (var element in document.Descendants())
                {
                    var min = AttributeValue(element, "SeriesMin");
                    var max = AttributeValue(element, "SeriesMax");
                    if (!string.IsNullOrWhiteSpace(min) && !string.IsNullOrWhiteSpace(max))
                    {
                        foreach (var release in ExpandReleaseRange(min, max))
                        {
                            releases.Add(release);
                        }
                    }

                    foreach (var name in new[] { "Series", "Release", "ReleaseKey", "SupportsRelease", "SupportedRelease" })
                    {
                        var raw = AttributeValue(element, name);
                        if (string.IsNullOrWhiteSpace(raw))
                        {
                            continue;
                        }
                        foreach (var release in SplitReleases(raw))
                        {
                            releases.Add(release);
                        }
                    }
                }
            }
            catch
            {
                return new List<string>();
            }

            return releases.OrderBy(x => Array.IndexOf(AutoCadReleaseOrder, x)).ThenBy(x => x).ToList();
        }

        private static string AttributeValue(System.Xml.Linq.XElement element, string name)
        {
            var attribute = element.Attributes().FirstOrDefault(
                x => string.Equals(x.Name.LocalName, name, StringComparison.OrdinalIgnoreCase));
            return attribute == null ? null : attribute.Value;
        }

        private static IEnumerable<string> SplitReleases(string raw)
        {
            return raw.Split(new[] { ',', ';', '|', ' ' }, StringSplitOptions.RemoveEmptyEntries)
                .Select(x => x.Trim())
                .Where(x => x.StartsWith("R", StringComparison.OrdinalIgnoreCase));
        }

        private static IEnumerable<string> ExpandReleaseRange(string min, string max)
        {
            var minIndex = Array.FindIndex(AutoCadReleaseOrder, x => string.Equals(x, min, StringComparison.OrdinalIgnoreCase));
            var maxIndex = Array.FindIndex(AutoCadReleaseOrder, x => string.Equals(x, max, StringComparison.OrdinalIgnoreCase));
            if (minIndex < 0 || maxIndex < 0 || minIndex > maxIndex)
            {
                return new[] { min, max };
            }
            return AutoCadReleaseOrder.Skip(minIndex).Take(maxIndex - minIndex + 1);
        }

        private string AutoCadCompatibility(string releaseKey, bool exeExists, YuantusBundleInfo bundle)
        {
            if (!exeExists)
            {
                return "registry-orphan";
            }
            if (string.IsNullOrWhiteSpace(releaseKey) || !AutoCadMarketingVersions.ContainsKey(releaseKey))
            {
                return "unknown";
            }
            if (!R3SupportedAutoCadReleases.Contains(releaseKey))
            {
                return "unknown";
            }
            if (bundle != null && bundle.Present)
            {
                if (bundle.SupportsRelease != null &&
                    bundle.SupportsRelease.Count > 0 &&
                    !bundle.SupportsRelease.Any(x => string.Equals(x, releaseKey, StringComparison.OrdinalIgnoreCase)))
                {
                    return "bundle-mismatch";
                }
                return "supported";
            }
            return "supported-no-bundle";
        }

        private List<string> AutoCadSupportDirs(string installRoot, string marketingVersion, string releaseKey, string language)
        {
            var dirs = new List<string>();
            if (!string.IsNullOrWhiteSpace(installRoot))
            {
                dirs.Add(Path.Combine(installRoot, "Support"));
            }

            var appData = _fileSystem.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            if (!string.IsNullOrWhiteSpace(appData) &&
                !string.IsNullOrWhiteSpace(marketingVersion) &&
                !string.IsNullOrWhiteSpace(releaseKey))
            {
                dirs.Add(Path.Combine(
                    appData,
                    "Autodesk",
                    "AutoCAD " + marketingVersion,
                    releaseKey,
                    string.IsNullOrWhiteSpace(language) ? "enu" : language,
                    "Support"));
            }
            return dirs;
        }

        private List<string> AutodeskPluginDirs()
        {
            var dirs = new List<string>();
            var appData = _fileSystem.GetFolderPath(Environment.SpecialFolder.ApplicationData);
            if (!string.IsNullOrWhiteSpace(appData))
            {
                dirs.Add(Path.Combine(appData, "Autodesk", "ApplicationPlugins"));
            }

            var common = _fileSystem.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
            if (!string.IsNullOrWhiteSpace(common))
            {
                dirs.Add(Path.Combine(common, "Autodesk", "ApplicationPlugins"));
            }
            return dirs;
        }

        private static List<DetectedProduct> Deduplicate(IEnumerable<DetectedProduct> products)
        {
            var result = new Dictionary<string, DetectedProduct>(StringComparer.OrdinalIgnoreCase);
            foreach (var product in products)
            {
                var key = FirstNonEmpty(
                    NormalizePath(product.InstallRoot),
                    NormalizePath(product.ExePath),
                    product.Vendor + "|" + product.Product + "|" + product.MarketingVersion + "|" + product.ReleaseKey);
                DetectedProduct existing;
                if (!result.TryGetValue(key, out existing) ||
                    IsBetterProduct(product, existing))
                {
                    result[key] = product;
                }
            }
            return result.Values.OrderBy(x => x.Vendor).ThenBy(x => x.Product).ThenBy(x => x.MarketingVersion).ToList();
        }

        private static bool IsBetterProduct(DetectedProduct candidate, DetectedProduct existing)
        {
            if (string.Equals(existing.Compatibility, "registry-orphan", StringComparison.OrdinalIgnoreCase) &&
                !string.Equals(candidate.Compatibility, "registry-orphan", StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
            return false;
        }

        private static RegistryView[] RegistryViews()
        {
            return new[] { RegistryView.Registry64, RegistryView.Registry32 };
        }

        private static string ResolveExePath(string installRoot, string explicitPath, string exeName)
        {
            if (!string.IsNullOrWhiteSpace(explicitPath) &&
                explicitPath.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
            {
                return NormalizePath(explicitPath);
            }
            if (!string.IsNullOrWhiteSpace(installRoot))
            {
                return Path.Combine(installRoot, exeName);
            }
            if (!string.IsNullOrWhiteSpace(explicitPath))
            {
                return Path.Combine(NormalizePath(explicitPath), exeName);
            }
            return null;
        }

        private static string NormalizePath(string path)
        {
            if (string.IsNullOrWhiteSpace(path))
            {
                return null;
            }
            return path.Trim().TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        }

        private static string NormalizeLanguage(string language)
        {
            if (string.IsNullOrWhiteSpace(language))
            {
                return null;
            }
            return language.Trim().Replace("_", "-");
        }

        private static string ProductId(string product, string marketingVersion, string language, string releaseKey)
        {
            var tokens = new List<string>
            {
                Slug(product)
            };
            if (!string.IsNullOrWhiteSpace(marketingVersion))
            {
                tokens.Add(Slug(marketingVersion));
            }
            else if (!string.IsNullOrWhiteSpace(releaseKey))
            {
                tokens.Add(Slug(releaseKey));
            }
            if (!string.IsNullOrWhiteSpace(language))
            {
                tokens.Add(Slug(language));
            }
            return string.Join("-", tokens);
        }

        private static string Slug(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                return "unknown";
            }
            var chars = value.ToLowerInvariant()
                .Select(ch => char.IsLetterOrDigit(ch) ? ch : '-')
                .ToArray();
            return new string(chars).Trim('-').Replace("--", "-");
        }

        private static string FirstNonEmpty(params string[] values)
        {
            foreach (var value in values)
            {
                if (!string.IsNullOrWhiteSpace(value))
                {
                    return value;
                }
            }
            return null;
        }

        private sealed class YearKeyProduct
        {
            public string Vendor { get; private set; }
            public string Product { get; private set; }
            public string RegistryRoot { get; private set; }
            public string ExeName { get; private set; }
            public string ErrorCode { get; private set; }

            public static YearKeyProduct ZwCad()
            {
                return new YearKeyProduct
                {
                    Vendor = "ZWSOFT",
                    Product = "ZWCAD",
                    RegistryRoot = "SOFTWARE\\ZWSOFT\\ZWCAD",
                    ExeName = "ZWCAD.exe",
                    ErrorCode = "ZWCAD_ENUM_FAILED"
                };
            }

            public static YearKeyProduct GstarCad()
            {
                return new YearKeyProduct
                {
                    Vendor = "Gstarsoft",
                    Product = "GstarCAD",
                    RegistryRoot = "SOFTWARE\\Gstarsoft\\GstarCAD",
                    ExeName = "GstarCAD.exe",
                    ErrorCode = "GSTARCAD_ENUM_FAILED"
                };
            }

            public static YearKeyProduct SolidWorks()
            {
                return new YearKeyProduct
                {
                    Vendor = "Dassault",
                    Product = "SolidWorks",
                    RegistryRoot = "SOFTWARE\\SolidWorks\\SOLIDWORKS",
                    ExeName = "SLDWORKS.exe",
                    ErrorCode = "SOLIDWORKS_ENUM_FAILED"
                };
            }
        }
    }
}
