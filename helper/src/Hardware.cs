using System.Collections.Generic;

namespace Overdrive.Helper
{
    // Overdrive-only hardware seams. The seams both helpers use are in SharedHardware.cs (namespace Ally.Shared).

    // ASUS ACPI (\\.\ATKACPI), shaped after G-Helper app/AsusACPI.cs.
    public interface IAcpi
    {
        // DSTS read; G-Helper returns the raw status minus 65536.
        int DeviceGet(uint id);
        // DEVS write; G-Helper treats a result of 1 as OK.
        int DeviceSet(uint id, int value);
        // 16 bytes: temperatures in 0-7, fan percentages in 8-15. fan: 0 = CPU, 1 = GPU (G-Helper AsusFan.CPU/GPU).
        // mode: performance mode value (Balanced 0, Turbo 1, Silent 2); the implementation does G-Helper's swap.
        byte[] GetFanCurve(int fan, int mode);
        // Same 16-byte layout; result 1 = OK.
        int SetFanCurve(int fan, byte[] curve);
    }

    // Windows power settings, shaped after G-Helper app/Mode/PowerNative.cs (CPU Boost and power mode overlay).
    public interface IWinPower
    {
        // Boost mode index (0..6) of the active scheme as { AC, DC }, or null on error.
        int[] GetCpuBoost();
        // Writes the AC and DC boost index, applying the scheme after each write.
        bool SetCpuBoost(int value, out string err);
        // Effective power mode overlay GUID (lowercase D-format), or null on error.
        string GetOverlay();
        // Sets the power mode overlay.
        bool SetOverlay(string guid, out string err);
    }

    // Device ids (spec section 3; same values as G-Helper app/AsusACPI.cs).
    public static class DeviceIds
    {
        public const uint PerformanceMode = 0x00120075;
        public const uint Spl = 0x001200A3;   // PPT_APUA3, sustained
        public const uint Sppt = 0x001200A0;  // PPT_APUA0, slow boost
        public const uint Fppt = 0x001200C1;  // PPT_APUC1, fast boost
        public const uint TempCpu = 0x00120094;
        public const uint TempGpu = 0x00120097;       // Temp_GPU, read only (fallback when ADL has no temperature)
        public const uint CpuFan = 0x00110013;
        public const uint GpuFan = 0x00110014;        // GPU_Fan, read only (RPM)
        public const uint CpuFanCurve = 0x00110024;   // DevsCPUFanCurve, written by SetFanCurve
        public const uint GpuFanCurve = 0x00110025;   // DevsGPUFanCurve, written by SetFanCurve

        public const int ModeBalanced = 0;
        public const int ModeTurbo = 1;
        public const int ModeSilent = 2;

        public const int FanCpu = 0;
        public const int FanGpu = 1;
    }
}
