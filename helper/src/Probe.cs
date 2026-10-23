using System;
using System.Collections.Generic;
using System.Web.Script.Serialization;

namespace Overdrive.Helper
{
    // IAcpi that only reads: writes throw before reaching the wrapped device. Used by --probe.
    public class ReadOnlyAcpi : IAcpi
    {
        private readonly IAcpi inner;

        public ReadOnlyAcpi(IAcpi inner)
        {
            if (inner == null) throw new ArgumentNullException("inner");
            this.inner = inner;
        }

        public int DeviceGet(uint id) { return inner.DeviceGet(id); }
        public byte[] GetFanCurve(int fan, int mode) { return inner.GetFanCurve(fan, mode); }

        public int DeviceSet(uint id, int value)
        {
            throw new InvalidOperationException("Read-only: DeviceSet 0x" + id.ToString("X8") + " refused.");
        }

        public int SetFanCurve(int fan, byte[] curve)
        {
            throw new InvalidOperationException("Read-only: SetFanCurve refused.");
        }
    }

    // asus-helper.exe --probe: one JSON line with what the device reports. Read-only by construction:
    // every call goes through ReadOnlyAcpi, and only DeviceGet and GetFanCurve are used.
    // {"product","mode","spl","sppt","fppt","cpuTempC","fanRpm","fanCurve":{"temps","speeds"},"errors"}
    // mode, spl, sppt, fppt are the raw DeviceGet values; cpuTempC and fanRpm are converted as G-Helper does.
    public static class Probe
    {
        public static string Run(IAcpi device, string product)
        {
            IAcpi acpi = new ReadOnlyAcpi(device);
            var errors = new List<string>();
            var d = new Dictionary<string, object>();

            d["product"] = product;
            if (product == null) errors.Add("product: BIOS SystemProductName could not be read.");

            int? mode = HelperCore.ReadRaw(acpi, DeviceIds.PerformanceMode, "mode", errors);
            d["mode"] = mode;
            d["spl"] = HelperCore.ReadRaw(acpi, DeviceIds.Spl, "spl", errors);
            d["sppt"] = HelperCore.ReadRaw(acpi, DeviceIds.Sppt, "sppt", errors);
            d["fppt"] = HelperCore.ReadRaw(acpi, DeviceIds.Fppt, "fppt", errors);
            HelperCore.ReadSensors(acpi, d, errors);

            // Curve of the current mode; balanced when the mode is not one of 0, 1, 2.
            int curveMode = DeviceIds.ModeBalanced;
            if (mode.HasValue && (mode.Value == DeviceIds.ModeBalanced || mode.Value == DeviceIds.ModeTurbo || mode.Value == DeviceIds.ModeSilent))
                curveMode = mode.Value;
            object fanCurve = null;
            try
            {
                byte[] c = acpi.GetFanCurve(DeviceIds.FanCpu, curveMode);
                if (c == null || c.Length != 16 || Array.TrueForAll(c, b => b == 0))
                    errors.Add("fanCurve: the device returned no fan curve for mode " + curveMode + ".");
                else
                {
                    var temps = new int[8];
                    var speeds = new int[8];
                    for (int i = 0; i < 8; i++) { temps[i] = c[i]; speeds[i] = c[i + 8]; }
                    var fc = new Dictionary<string, object>();
                    fc["temps"] = temps;
                    fc["speeds"] = speeds;
                    fanCurve = fc;
                }
            }
            catch (Exception ex) { errors.Add("fanCurve: " + ex.Message); }
            d["fanCurve"] = fanCurve;
            d["errors"] = errors;

            return new JavaScriptSerializer().Serialize(d);
        }
    }
}
