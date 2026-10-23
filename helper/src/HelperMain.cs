using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using Microsoft.Win32;

namespace Overdrive.Helper
{
    public static class HelperMain
    {
        public static int Main(string[] args)
        {
            var utf8 = new UTF8Encoding(false);
            var output = new StreamWriter(Console.OpenStandardOutput(), utf8);

            if (args.Length == 1 && args[0] == "--probe")
            {
                // Read-only: Probe wraps the device in ReadOnlyAcpi and only reads; ADL only reads its sensor log.
                using (var acpi = new AcpiHardware())
                using (var adl = new AdlHardware())
                {
                    output.Write(Probe.Run(acpi, ReadProductName(), adl));
                    output.Write('\n');
                    output.Flush();
                }
                return 0;
            }
            if (args.Length != 0)
            {
                Console.Error.WriteLine("Usage: asus-helper.exe [--probe]");
                return 2;
            }

            var input = new StreamReader(Console.OpenStandardInput(), utf8);
            using (var acpi = new AcpiHardware())
            using (var adl = new AdlHardware())
            {
                Run(input, output, new HelperCore(acpi, adl, new ServiceHardware(), IsAlly()));
            }
            return 0;
        }

        // Reads request lines until EOF and writes exactly one reply line per non-empty request line.
        // Nothing thrown by a request leaves this loop.
        public static void Run(TextReader input, TextWriter output, HelperCore core)
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

        // Spec: BIOS SystemProductName contains "RC7" (owner device "ROG Xbox Ally X RC73XA_RC73XA"). Read only.
        public static bool IsAlly()
        {
            string name = ReadProductName();
            return name != null && name.Contains("RC7");
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
