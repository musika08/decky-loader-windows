// G-Helper - https://github.com/seerge/g-helper - app/HardwareControl.cs, app/Fan/FanSensorControl.cs
// Copyright seerge and contributors, licensed GPL-3.0. Commit c09017453d9ed0c52261b67a48d02bd55232e6ac.
// (The upstream files have no header of their own; these lines record their origin.)
// Modified for Overdrive (ported to C# 5 / .NET Framework 4.8), 2026
//
// The sensor conversions G-Helper applies, as pure functions over IAcpi.DeviceGet values.
// This is the only place Overdrive converts sensor readings (status, sensors and --probe all use it).

using Overdrive.Helper.GHelper;

namespace Overdrive.Helper
{
    public static class GHelperSensors
    {
        // HardwareControl.GetCPUTemp: `cpuTemp = Program.acpi.DeviceGet(AsusACPI.Temp_CPU);` is degrees C as is;
        // `if (cpuTemp < 0)` means ACPI has no reading (upstream then tries the Windows thermal zone; not ported).
        public static int? CpuTempC(int raw)
        {
            if (raw < 0) return null;
            return raw;
        }

        // AsusACPI.GetFan (FanFromRaw) gives the fan value or -1; HardwareControl.ReadSensorsOverlay
        // `cpuFanRPM = Program.acpi.GetFan(AsusFan.CPU) * 100;` and FanSensorControl.FormatFan
        // (`if (value < 0) return null;` ... `(value * 100) + "RPM"`) turn it into RPM.
        public static int? FanRpm(int raw)
        {
            int value = AsusACPI.FanFromRaw(raw);
            if (value < 0) return null;
            return value * 100;
        }
    }
}
