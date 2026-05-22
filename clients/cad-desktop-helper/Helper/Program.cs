using System;
using System.Threading;

namespace Yuantus.Cad.Helper
{
    internal static class Program
    {
        private static int Main(string[] args)
        {
            using (var cancellation = new CancellationTokenSource())
            {
                Console.CancelKeyPress += (sender, eventArgs) =>
                {
                    eventArgs.Cancel = true;
                    cancellation.Cancel();
                };

                return HelperCommand
                    .RunAsync(args, HelperRuntime.Default, cancellation.Token)
                    .GetAwaiter()
                    .GetResult();
            }
        }
    }
}
