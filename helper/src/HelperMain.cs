using System;
using System.IO;
using System.Text;
using Microsoft.Win32;
using Ally.Shared;

namespace Traction.Helper
{
    public static class HelperMain
    {
        public static int Main(string[] args)
        {
            if (args.Length != 0)
            {
                Console.Error.WriteLine("Usage: traction-helper.exe");
                return 2;
            }

            var utf8 = new UTF8Encoding(false);
            var output = new StreamWriter(Console.OpenStandardOutput(), utf8);
            var input = new StreamReader(Console.OpenStandardInput(), utf8);
            string product = ReadProductName();
            using (var adl = new AdlHardware())
            {
                var core = new TractionCore(adl, new ServiceHardware(), IsAlly(product));
                core.Hid = new AsusHid();
                core.SystemControls = new SystemControls();
                core.ProductName = product;
                Run(input, output, core);
            }
            return 0;
        }

        // Reads request lines until EOF and writes exactly one reply line per non-empty request line.
        // Nothing thrown by a request leaves this loop.
        public static void Run(TextReader input, TextWriter output, TractionCore core)
        {
            while (true)
            {
                string line;
                try { line = input.ReadLine(); }
                catch (IOException) { return; }
                if (line == null) return;
                if (line.Trim().Length == 0) continue;

                string reply;
                try { reply = core.HandleLine(line); }
                catch (Exception ex)
                {
                    reply = "{\"id\":null,\"ok\":false,\"message\":\"Internal error: "
                        + ex.GetType().Name + "\",\"data\":null}";
                }
                try
                {
                    output.Write(reply);
                    output.Write("\n");
                    output.Flush();
                }
                catch (IOException) { return; }
            }
        }

        // BIOS SystemProductName contains "RC7" (owner device "ROG Xbox Ally X RC73XA_RC73XA"). Read only.
        public static bool IsAlly(string productName)
        {
            return productName != null && productName.Contains("RC7");
        }

        // BIOS SystemProductName, or null when it cannot be read. Read only.
        public static string ReadProductName()
        {
            try
            {
                using (RegistryKey k = Registry.LocalMachine.OpenSubKey(@"HARDWARE\DESCRIPTION\System\BIOS", false))
                {
                    if (k == null) return null;
                    return k.GetValue("SystemProductName") as string;
                }
            }
            catch (Exception) { return null; }
        }
    }
}
