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
    // {"product","mode","modeName","spl","sppt","fppt","cpuTempC","fanRpm","fanCurve":{"temps","speeds"},
    //  "fanCurves":{"balanced","turbo","silent"},"errors"}
    // mode, spl, sppt, fppt are the raw DeviceGet values (null plus an error when -65536, nothing reported; a power
    // limit of 0 is null without an error); modeName is balanced/turbo/silent or "other" (HelperCore.ModeName);
    // cpuTempC and fanRpm are converted as G-Helper does; fanCurve is the current mode's curve.
    public static class Probe
    {
        public static string Run(IAcpi device, string product)
        {
            IAcpi acpi = new ReadOnlyAcpi(device);
            var errors = new List<string>();
            var d = new Dictionary<string, object>();

            d["product"] = product;
            if (product == null) errors.Add("product: BIOS SystemProductName could not be read.");

            int? mode = HelperCore.ReadSetting(acpi, DeviceIds.PerformanceMode, "mode", errors);
            d["mode"] = mode;
            d["modeName"] = HelperCore.ModeName(mode);
            d["spl"] = HelperCore.ReadPower(acpi, DeviceIds.Spl, "spl", errors);
            d["sppt"] = HelperCore.ReadPower(acpi, DeviceIds.Sppt, "sppt", errors);
            d["fppt"] = HelperCore.ReadPower(acpi, DeviceIds.Fppt, "fppt", errors);
            HelperCore.ReadSensors(acpi, d, errors);

            // Curve of the current mode; balanced when the mode is not one of 0, 1, 2.
            int curveMode = DeviceIds.ModeBalanced;
            if (mode.HasValue && (mode.Value == DeviceIds.ModeBalanced || mode.Value == DeviceIds.ModeTurbo || mode.Value == DeviceIds.ModeSilent))
                curveMode = mode.Value;
            d["fanCurve"] = ReadCurve(acpi, curveMode, "fanCurve", errors);

            // The curve of every mode (ruling P6/P7), so one run records all three defaults.
            var curves = new Dictionary<string, object>();
            curves["balanced"] = ReadCurve(acpi, DeviceIds.ModeBalanced, "fanCurves.balanced", errors);
            curves["turbo"] = ReadCurve(acpi, DeviceIds.ModeTurbo, "fanCurves.turbo", errors);
            curves["silent"] = ReadCurve(acpi, DeviceIds.ModeSilent, "fanCurves.silent", errors);
            d["fanCurves"] = curves;
            d["errors"] = errors;

            return new JavaScriptSerializer().Serialize(d);
        }

        // {temps, speeds} of one mode's CPU fan curve, or null plus an error (G-Helper AsusACPI.IsInvalidCurve).
        private static object ReadCurve(IAcpi acpi, int mode, string name, List<string> errors)
        {
            try
            {
                object curve = HelperCore.SplitCurve(acpi.GetFanCurve(DeviceIds.FanCpu, mode));
                if (curve == null) errors.Add(name + ": the device returned no fan curve for mode " + mode + ".");
                return curve;
            }
            catch (Exception ex)
            {
                errors.Add(name + ": " + ex.Message);
                return null;
            }
        }
    }
}
