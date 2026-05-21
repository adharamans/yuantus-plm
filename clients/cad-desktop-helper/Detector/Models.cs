using System;
using System.Collections.Generic;
using Newtonsoft.Json;

namespace Yuantus.Cad.Detector
{
    public sealed class DetectorReport
    {
        [JsonProperty("schema_version")]
        public string SchemaVersion { get; set; }

        [JsonProperty("scanned_at")]
        public DateTimeOffset ScannedAt { get; set; }

        [JsonProperty("host")]
        public HostInfo Host { get; set; }

        [JsonProperty("products")]
        public List<DetectedProduct> Products { get; set; }

        [JsonProperty("recommendations")]
        public List<DetectorMessage> Recommendations { get; set; }

        [JsonProperty("warnings")]
        public List<DetectorMessage> Warnings { get; set; }
    }

    public sealed class HostInfo
    {
        [JsonProperty("os")]
        public string Os { get; set; }

        [JsonProperty("arch")]
        public string Arch { get; set; }

        [JsonProperty("username")]
        public string Username { get; set; }

        [JsonProperty("is_admin")]
        public bool IsAdmin { get; set; }
    }

    public sealed class DetectedProduct
    {
        [JsonProperty("id")]
        public string Id { get; set; }

        [JsonProperty("vendor")]
        public string Vendor { get; set; }

        [JsonProperty("product")]
        public string Product { get; set; }

        [JsonProperty("release_key")]
        public string ReleaseKey { get; set; }

        [JsonProperty("marketing_version")]
        public string MarketingVersion { get; set; }

        [JsonProperty("language")]
        public string Language { get; set; }

        [JsonProperty("install_root")]
        public string InstallRoot { get; set; }

        [JsonProperty("exe_path")]
        public string ExePath { get; set; }

        [JsonProperty("support_dirs")]
        public List<string> SupportDirs { get; set; }

        [JsonProperty("plugin_bundle_dirs")]
        public List<string> PluginBundleDirs { get; set; }

        [JsonProperty("yuantus_bundle")]
        public YuantusBundleInfo YuantusBundle { get; set; }

        [JsonProperty("compatibility")]
        public string Compatibility { get; set; }

        [JsonProperty("errors")]
        public List<DetectorMessage> Errors { get; set; }
    }

    public sealed class YuantusBundleInfo
    {
        [JsonProperty("present")]
        public bool Present { get; set; }

        [JsonProperty("path")]
        public string Path { get; set; }

        [JsonProperty("package_version")]
        public string PackageVersion { get; set; }

        [JsonProperty("supports_release")]
        public List<string> SupportsRelease { get; set; }
    }

    public sealed class DetectorMessage
    {
        [JsonProperty("level")]
        public string Level { get; set; }

        [JsonProperty("code")]
        public string Code { get; set; }

        [JsonProperty("message")]
        public string Message { get; set; }
    }
}
