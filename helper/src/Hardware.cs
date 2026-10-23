using System.Collections.Generic;

namespace Overdrive.Helper
{
    // Hardware seams. The real implementations (Tasks 3 and 4) wrap code ported from G-Helper; tests use fakes.

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

    // AMD ADL frame rate target control, shaped after G-Helper app/Gpu/AMD/AmdGpuControl.cs.
    public interface IAdl
    {
        // Current limit (0 = off), or -1 when ADL or the adapter is unavailable.
        int GetFpsLimit();
        // 1 = OK, 0 = ADL refused, -1 = ADL or the adapter is unavailable.
        int SetFpsLimit(int fps);
        // Why the last call found frame rate control unavailable, or null.
        string Unavailable { get; }
        // Integrated GPU sensors (read only), or null when ADL or its sensor log is unavailable (reason in Unavailable).
        GpuSensors GetGpuSensors();
        // Frame rate of the game in front (AMD frame metrics): null when ADL or its frame metrics are unavailable
        // (reason in Unavailable), 0 when no game is in front. The first call starts the metrics.
        float? GetFrameRate();
        // False once the driver is known to lack the frame metrics entry points.
        bool FrameRateSupported { get; }
    }

    // G-Helper AmdGpuControl.GetiGpuSensors (temp, use, gfxPower, cpuPower, asicPower); null = not reported.
    public class GpuSensors
    {
        public int? TempC;       // edge, else GFX, else SOC temperature (degrees C)
        public int? Use;         // graphics activity (%)
        public int? GfxPowerW;   // graphics power (W)
        public int? CpuPowerW;   // CPU cores power (W)
        public int? AsicPowerW;  // whole APU power (W)
    }

    // Windows service control for the Armoury Crate services (names from ArmouryServices only).
    // Start types: "Automatic", "AutomaticDelayedStart", "Manual", "Disabled".
    public interface IServices
    {
        // Start type of each named service that exists (missing services are left out).
        Dictionary<string, string> StartTypes(IEnumerable<string> names);
        // The named services whose state is anything but Stopped.
        List<string> Running(IEnumerable<string> names);
        // Stop the service (no-op when stopped), then set its start type.
        void Stop(string name, string startType);
        // Set the start type, then start the service (no-op when running).
        void Start(string name, string startType);
        // Set the start type only.
        void SetStartType(string name, string startType);
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
