using System;
using System.IO;

namespace Yuantus.Cad.Detector
{
    internal static class Program
    {
        private static int Main(string[] args)
        {
            return DetectorCli.Run(
                args,
                Console.Out,
                Console.Error,
                DetectorRuntime.Default);
        }
    }
}
