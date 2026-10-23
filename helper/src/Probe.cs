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
    // {"product","mode","modeName","spl","sppt","fppt","cpuTempC","fanRpm","gpuFanRpm","gpuTempC","gpuUse","cpuPowerW",
    //  "gpuPowerW","fanCurve":{"temps","speeds"},
    //  "fanCurves":{"balanced","turbo","silent"},"fanCurvesGpu":{"balanced","turbo","silent"},"errors"}
    // mode, spl, sppt, fppt are the raw DeviceGet values (null plus an error when -65536, nothing reported; a power
    // limit of 0 is null without an error); modeName is balanced/turbo/silent or "other" (HelperCore.ModeName);
    // cpuTempC, fanRpm and gpuFanRpm are converted as G-Helper does (GPU readings: HelperCore.ReadGpuSensors); fanCurve is the current mode's CPU fan curve;
    // fanCurves / fanCurvesGpu are every mode's CPU / GPU fan curve (G-Helper DevsCPUFanCurve / DevsGPUFanCurve).
    public static class Probe
    {
        public static string Run(IAcpi device, string product)
        {
            return Run(device, product, null);
        }

        // adl: the AMD driver's GPU sensors (read only), or null to read the GPU temperature from ACPI only.
        public static string Run(IAcpi device, string product, IAdl adl)
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
            HelperCore.ReadSensors(acpi, adl, d, errors);

            // Curve of the current mode; balanced when the mode is not one of 0, 1, 2.
            int curveMode = DeviceIds.ModeBalanced;
            if (mode.HasValue && (mode.Value == DeviceIds.ModeBalanced || mode.Value == DeviceIds.ModeTurbo || mode.Value == DeviceIds.ModeSilent))
                curveMode = mode.Value;
            d["fanCurve"] = ReadCurve(acpi, DeviceIds.FanCpu, curveMode, "fanCurve", errors);

            // The curve of every mode for each fan (ruling P6/P7), so one run records all six defaults.
            d["fanCurves"] = ReadAllModes(acpi, DeviceIds.FanCpu, "fanCurves", errors);
            d["fanCurvesGpu"] = ReadAllModes(acpi, DeviceIds.FanGpu, "fanCurvesGpu", errors);
            d["errors"] = errors;

            return new JavaScriptSerializer().Serialize(d);
        }

        private static Dictionary<string, object> ReadAllModes(IAcpi acpi, int fan, string name, List<string> errors)
        {
            var curves = new Dictionary<string, object>();
            curves["balanced"] = ReadCurve(acpi, fan, DeviceIds.ModeBalanced, name + ".balanced", errors);
            curves["turbo"] = ReadCurve(acpi, fan, DeviceIds.ModeTurbo, name + ".turbo", errors);
            curves["silent"] = ReadCurve(acpi, fan, DeviceIds.ModeSilent, name + ".silent", errors);
            return curves;
        }

        // {temps, speeds} of one mode's fan curve, or null plus an error (G-Helper AsusACPI.IsInvalidCurve).
        private static object ReadCurve(IAcpi acpi, int fan, int mode, string name, List<string> errors)
        {
            try
            {
                object curve = HelperCore.SplitCurve(acpi.GetFanCurve(fan, mode));
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
