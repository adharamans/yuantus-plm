namespace Yuantus.Cad.Shared.Transport
{
    public static class ErrorCodes
    {
        public const string HelperPortBusy = "HELPER_PORT_BUSY";
        public const string HelperDpapiUnavailable = "HELPER_DPAPI_UNAVAILABLE";
        public const string HelperSingletonLost = "HELPER_SINGLETON_LOST";
        public const string HelperUnhealthy = "HELPER_UNHEALTHY";
        public const string HelperLocalTokenBootstrapFailed = "HELPER_LOCAL_TOKEN_BOOTSTRAP_FAILED";
        public const string HelperInstallIdUnavailable = "HELPER_INSTALL_ID_UNAVAILABLE";
        public const string AuthLocalTokenMissing = "AUTH_LOCAL_TOKEN_MISSING";
        public const string AuthLocalTokenInvalid = "AUTH_LOCAL_TOKEN_INVALID";
        public const string AuthPlmNotLoggedIn = "AUTH_PLM_NOT_LOGGED_IN";
        public const string AuthTenantMissing = "AUTH_TENANT_MISSING";
        public const string OriginProcessNotAllowed = "ORIGIN_PROCESS_NOT_ALLOWED";
        public const string CadContextMissing = "CAD_CONTEXT_MISSING";
        public const string AuditWriteFailed = "AUDIT_WRITE_FAILED";
        public const string AuditPullIdUnknown = "AUDIT_PULL_ID_UNKNOWN";
        public const string AuditAlreadyReported = "AUDIT_ALREADY_REPORTED";
        public const string AuditPullIdExpired = "AUDIT_PULL_ID_EXPIRED";
        public const string PlmItemNotFound = "PLM_ITEM_NOT_FOUND";
        public const string PlmProfileMismatch = "PLM_PROFILE_MISMATCH";
        public const string PlmValidationFailed = "PLM_VALIDATION_FAILED";
        public const string PlmInboundConflict = "PLM_INBOUND_CONFLICT";
        public const string ProtoVersionUnsupported = "PROTO_VERSION_UNSUPPORTED";
        public const string HelperInputValidationFailed = "HELPER_INPUT_VALIDATION_FAILED";
    }
}
