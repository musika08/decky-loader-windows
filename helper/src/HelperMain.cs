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
            var input = new StreamReader(Console.OpenStandardInput(), utf8);
            var output = new StreamWriter(Console.OpenStandardOutput(), utf8);
            var hw = new UnavailableHardware();
            Run(input, output, new HelperCore(hw, hw, hw, IsAlly()));
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
            try
            {
                using (RegistryKey k = Registry.LocalMachine.OpenSubKey(@"HARDWARE\DESCRIPTION\System\BIOS", false))
                {
                    if (k == null) return false;
                    string name = k.GetValue("SystemProductName") as string;
                    return name != null && name.Contains("RC7");
                }
            }
            catch (Exception) { return false; }
        }
    }

    // Placeholder until the ported G-Helper layers land (Task 3 ACPI, Task 4 ADL and services):
    // every hardware call fails with a clear message and nothing is touched.
    public class UnavailableHardware : IAcpi, IAdl, IServices
    {
        private static Exception NotBuilt()
        {
            return new NotSupportedException("The hardware layer is not built into this helper yet.");
        }

        public int DeviceGet(uint id) { throw NotBuilt(); }
        public int DeviceSet(uint id, int value) { throw NotBuilt(); }
        public byte[] GetFanCurve(int fan, int mode) { throw NotBuilt(); }
        public int SetFanCurve(int fan, byte[] curve) { throw NotBuilt(); }
        public int GetFpsLimit() { return -1; }
        public int SetFpsLimit(int fps) { return -1; }
        public Dictionary<string, string> StartTypes(IEnumerable<string> names) { throw NotBuilt(); }
        public List<string> Running(IEnumerable<string> names) { throw NotBuilt(); }
        public void Stop(string name, string startType) { throw NotBuilt(); }
        public void Start(string name, string startType) { throw NotBuilt(); }
    }
}
