using System.Diagnostics;

namespace Yuantus.Cad.Shared.Identity
{
    public static class SessionContext
    {
        public static int CurrentSessionId
        {
            get { return Process.GetCurrentProcess().SessionId; }
        }
    }
}
