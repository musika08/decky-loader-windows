using System.Collections.Generic;

namespace Ally.Shared
{
    // Hardware seams shared by the Overdrive and Traction helpers. The real implementations wrap code ported from
    // G-Helper; tests use fakes.

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

    // The ASUS controller's HID collection that takes feature report 0x5A (see AsusHid.cs).
    public sealed class HidInfo
    {
        public bool Found;
        public int Pid;
        public int FeatureLength;   // feature report length of that collection, report id byte included
    }

    // The ASUS controller, shaped after G-Helper app/USB/AsusHid.cs (FindHidStream / WriteInput).
    public interface IAsusHid
    {
        // The controller collection (VID 0x0B05, PID in MAIN_AURA_PIDS, feature report 0x5A), or Found = false.
        HidInfo Find();
        // Sends one feature report to that collection only. The report must start with 0x5A and be exactly
        // FeatureLength bytes long.
        bool SetFeature(byte[] report, out string err);
    }

    // One HID collection as Windows lists it.
    public sealed class HidCollection
    {
        public string Path;
        public int Vid;
        public int Pid;
        public int FeatureLength;
        public byte[] FeatureReportIds;   // report ids of the collection's feature reports
    }

    // The raw HID layer under AsusHid: lists collections and sends a feature report to one path.
    public interface IHidBus
    {
        List<HidCollection> List();
        bool SetFeature(string path, byte[] report, out string err);
    }

    // The touchscreen as Windows lists it (PnP FriendlyName like *touch*screen*).
    public sealed class TouchInfo
    {
        public bool Found;
        public bool Enabled;        // PnP status OK
        public string InstanceId;
        public string Error;        // set when the state could not be read (for example PowerShell took over 5 s)
    }

    // Touchscreen (PnP through PowerShell, as G-Helper TouchscreenHelper), Auto SR (HKCU) and the foreground app.
    public interface ISystemControls
    {
        // Reads the touchscreen state; waits at most 5 s.
        TouchInfo GetTouchscreen();
        // Starts Enable-PnpDevice / Disable-PnpDevice and returns at once. The process is killed after 15 s if still
        // running. False (with err) when it could not start or a previous change is still running.
        bool StartSetTouchscreen(bool enabled, out string err);
        // HKCU\Software\Microsoft\DirectX\UserGpuPreferences, DirectXUserGlobalSettings, or null when not set.
        // A missing key or value gives null with err null; a read error sets err.
        string ReadGpuSettings(out string err);
        bool WriteGpuSettings(string value, out string err);
        // True when a Microsoft.AutoSuperResolution2_* package folder exists; an access error counts as not installed.
        bool AutoSrInstalled();
        // Lowercase file name of the foreground window's process, or "" when there is none.
        string ForegroundExe();
    }
}
