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
        // 16 bytes: temperatures in 0-7, fan percentages in 8-15. fan: 0 = CPU (G-Helper AsusFan.CPU).
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
    }

    // Windows service control for the Armoury Crate services (Task 4).
    public interface IServices
    {
        Dictionary<string, string> StartTypes(IEnumerable<string> names);
        List<string> Running(IEnumerable<string> names);
        void Stop(string name, string startType);
        void Start(string name, string startType);
    }

    // Device ids (spec section 3; same values as G-Helper app/AsusACPI.cs).
    public static class DeviceIds
    {
        public const uint PerformanceMode = 0x00120075;
        public const uint Spl = 0x001200A3;   // PPT_APUA3, sustained
        public const uint Sppt = 0x001200A0;  // PPT_APUA0, slow boost
        public const uint Fppt = 0x001200C1;  // PPT_APUC1, fast boost
        public const uint TempCpu = 0x00120094;
        public const uint CpuFan = 0x00110013;

        public const int ModeBalanced = 0;
        public const int ModeTurbo = 1;
        public const int ModeSilent = 2;

        public const int FanCpu = 0;
    }
}
