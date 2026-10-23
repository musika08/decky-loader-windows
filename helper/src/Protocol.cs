using System;
using System.Collections.Generic;
using System.Web.Script.Serialization;
using Overdrive.Helper.GHelper;

namespace Overdrive.Helper
{
    // One JSON object per line.
    //   request {"id":n,"cmd":"name","args":{...}}
    //   reply   {"id":n,"ok":bool,"message":"","data":...}
    // Fixed command list; unknown commands, unknown args and wrongly typed args are refused before any hardware call.
    public class HelperCore
    {
        public const string Version = "0.1.0";

        private static readonly string[] None = new string[0];

        // cmd -> allowed arg names
        private static readonly Dictionary<string, string[]> Commands = new Dictionary<string, string[]>
        {
            { "hello", None },
            { "status", None },
            { "sensors", None },
            { "frame_sample", None },
            { "set_mode", new[] { "mode", "spl", "sppt", "fppt" } },
            { "set_power", new[] { "spl", "sppt", "fppt" } },
            { "get_fans", new[] { "mode", "fan" } },
            { "set_fans", new[] { "temps", "speeds", "fan" } },
            { "reset_fans", new[] { "mode" } },
            { "get_fps_limit", None },
            { "set_fps_limit", new[] { "fps" } },
            { "armoury_state", None },
            { "armoury_stop", None },
            { "armoury_restore", new[] { "recorded", "running" } },
            { "get_cpu_boost", None },
            { "set_cpu_boost", new[] { "value" } },
            { "get_power_mode", None },
            { "set_power_mode", new[] { "mode" } },
        };

        private static readonly string[] TopLevel = { "id", "cmd", "args" };

        // The only commands allowed on a device that is not an Ally (ruling P7: an allow-list of reads, so a command
        // added later is refused there unless it is listed here on purpose).
        public static readonly string[] ReadCommands =
        {
            "hello", "status", "sensors", "frame_sample", "get_fans", "get_fps_limit", "armoury_state",
            "get_cpu_boost", "get_power_mode"
        };

        // Windows power (CPU Boost and power mode). Set after construction; null means not available.
        public IWinPower WinPower { get; set; }

        // Power mode overlay GUIDs (G-Helper PowerNative POWER_SILENT / POWER_BALANCED / POWER_TURBO).
        private const string OverlayEco = "961cc777-2547-4f9d-8174-7d86181b8a7a";
        private const string OverlayBalanced = "00000000-0000-0000-0000-000000000000";
        private const string OverlayBest = "ded574b5-45a0-4f42-8737-46345c09c238";
        public const string WinPowerUnavailable = "Windows power control is not available.";

        public static IEnumerable<string> CommandNames { get { return Commands.Keys; } }

        // DeviceGet's value when the DSTS call returned nothing (G-Helper AsusACPI.DeviceGet: raw - 65536).
        public const int NotReported = -65536;

        public const string NotAllyMessage = "Overdrive supports the ROG Ally family only";

        private readonly IAcpi acpi;
        private readonly IAdl adl;
        private readonly IServices services;
        private readonly bool ally;
        private readonly JavaScriptSerializer json = new JavaScriptSerializer();

        // What this helper process last applied, for reset_fans.
        private string lastMode;
        private int[] lastPower;

        // G-Helper app/Mode/ModeControl.cs SetPerformanceMode: mode write, 100 ms, AutoFans, 1000 ms, AutoPower.
        // The firmware applies the mode's own limits after a mode write, so manual limits wait for the same 1.1 s.
        public const int ModeToFansDelayMs = 100;
        public const int FansToPowerDelayMs = 1000;

        private readonly Action<int> sleep;

        public HelperCore(IAcpi acpi, IAdl adl, IServices services, bool ally)
            : this(acpi, adl, services, ally, System.Threading.Thread.Sleep)
        {
        }

        // sleep is injectable so tests record the delays instead of waiting.
        public HelperCore(IAcpi acpi, IAdl adl, IServices services, bool ally, Action<int> sleep)
        {
            this.acpi = acpi;
            this.adl = adl;
            this.services = services;
            this.ally = ally;
            this.sleep = sleep ?? System.Threading.Thread.Sleep;
        }

        // Never throws: every line gets exactly one single-line reply.
        public string HandleLine(string line)
        {
            object id = null;
            try
            {
                object parsed;
                try { parsed = json.DeserializeObject(line); }
                catch (Exception ex) { return Reply(null, false, "Malformed request: " + ex.Message, null); }

                var req = parsed as Dictionary<string, object>;
                if (req == null) return Reply(null, false, "Malformed request: expected a JSON object.", null);

                object rawId;
                if (req.TryGetValue("id", out rawId))
                {
                    int n;
                    if (!TryInt(rawId, out n)) return Reply(null, false, "Malformed request: id must be an integer.", null);
                    id = n;
                }
                else return Reply(null, false, "Malformed request: id is missing.", null);

                foreach (string key in req.Keys)
                    if (Array.IndexOf(TopLevel, key) < 0) return Reply(id, false, "Unknown field '" + key + "'.", null);

                object rawCmd;
                req.TryGetValue("cmd", out rawCmd);
                string cmd = rawCmd as string;
                string[] allowed;
                if (cmd == null || !Commands.TryGetValue(cmd, out allowed))
                    return Reply(id, false, "Refused: unknown command '" + (cmd ?? "") + "'.", null);

                object rawArgs;
                Dictionary<string, object> args;
                if (!req.TryGetValue("args", out rawArgs) || rawArgs == null) args = new Dictionary<string, object>();
                else
                {
                    args = rawArgs as Dictionary<string, object>;
                    if (args == null) return Reply(id, false, "Malformed request: args must be an object.", null);
                }
                foreach (string key in args.Keys)
                    if (Array.IndexOf(allowed, key) < 0)
                        return Reply(id, false, "Refused: unknown argument '" + key + "' for " + cmd + ".", null);

                return Dispatch(id, cmd, args);
            }
            catch (Exception ex)
            {
                return Reply(id, false, ex.Message, null);
            }
        }

        private string Dispatch(object id, string cmd, Dictionary<string, object> args)
        {
            if (!ally && Array.IndexOf(ReadCommands, cmd) < 0) return Reply(id, false, NotAllyMessage, null);
            switch (cmd)
            {
                case "hello": return Hello(id);
                case "status": return Status(id);
                case "sensors": return Sensors(id);
                case "frame_sample": return FrameSample(id);
                case "set_mode": return SetMode(id, args);
                case "set_power": return SetPower(id, args);
                case "get_fans": return GetFans(id, args);
                case "set_fans": return SetFans(id, args);
                case "reset_fans": return ResetFans(id, args);
                case "get_fps_limit": return GetFps(id);
                case "set_fps_limit": return SetFps(id, args);
                case "armoury_state": return ArmouryState(id);
                case "armoury_stop": return ArmouryStop(id);
                case "armoury_restore": return ArmouryRestore(id, args);
                case "get_cpu_boost": return GetCpuBoost(id);
                case "set_cpu_boost": return SetCpuBoost(id, args);
                case "get_power_mode": return GetPowerMode(id);
                case "set_power_mode": return SetPowerMode(id, args);
            }
            return Reply(id, false, "Refused: unknown command '" + cmd + "'.", null);
        }

        // ---------- commands ----------

        private string Hello(object id)
        {
            var d = new Dictionary<string, object>();
            d["version"] = Version;
            d["ally"] = ally;
            return Reply(id, true, "", d);
        }

        private string Status(object id)
        {
            var d = new Dictionary<string, object>();
            var errors = new List<string>();
            int? modeRaw = ReadSetting(acpi, DeviceIds.PerformanceMode, "mode", errors);
            d["modeRaw"] = modeRaw;
            d["mode"] = ModeName(modeRaw);
            d["spl"] = ReadPower(acpi, DeviceIds.Spl, "spl", errors);
            d["sppt"] = ReadPower(acpi, DeviceIds.Sppt, "sppt", errors);
            d["fppt"] = ReadPower(acpi, DeviceIds.Fppt, "fppt", errors);
            d["lastMode"] = lastMode;
            object fps = null;
            try
            {
                int f = adl.GetFpsLimit();
                if (f < 0) errors.Add("fps: " + FpsUnavailable()); else fps = f;
            }
            catch (Exception ex) { errors.Add("fps: " + ex.Message); }
            d["fpsLimit"] = fps;
            ReadSensors(acpi, adl, d, errors);
            d["errors"] = errors;
            return Reply(id, true, "", d);
        }

        private string Sensors(object id)
        {
            var d = new Dictionary<string, object>();
            var errors = new List<string>();
            ReadSensors(acpi, adl, d, errors);
            d["errors"] = errors;
            return Reply(id, true, "", d);
        }

        // One sample for Auto TDP, read only: the game's frame rate (AMD frame metrics; 0 = no game in front, null =
        // unavailable, with an error) and the graphics power (null unless 1..1000 W, as in sensors).
        private string FrameSample(object id)
        {
            var d = new Dictionary<string, object>();
            var errors = new List<string>();
            float? fps = null;
            try
            {
                fps = adl.GetFrameRate();
                if (!fps.HasValue) errors.Add("frame rate: " + (adl.Unavailable ?? "not available"));
            }
            catch (Exception ex) { errors.Add("frame rate: " + ex.Message); }
            bool supported = true;
            try { supported = adl.FrameRateSupported; } catch (Exception) { }
            int? power = null;
            try
            {
                GpuSensors g = adl.GetGpuSensors();
                if (g != null) power = InRange(g.GfxPowerW, 1, 1000);
            }
            catch (Exception ex) { errors.Add("gpu power: " + ex.Message); }
            d["supported"] = supported;
            d["fps"] = fps.HasValue ? (object)fps.Value : null;
            d["gpuPowerW"] = power;
            d["errors"] = errors;
            return Reply(id, true, "", d);
        }

        // cpuTempC, fanRpm (CPU fan) and gpuFanRpm (GPU fan), converted as G-Helper does (GHelperSensors; the GPU fan
        // is G-Helper's GetFan(AsusFan.GPU), endpoint GPU_Fan); null plus an error when not reported.
        // Then the GPU readings (ReadGpuSensors). Shared by status, sensors and --probe.
        public static void ReadSensors(IAcpi acpi, IAdl adl, Dictionary<string, object> d, List<string> errors)
        {
            int? temp = ReadRaw(acpi, DeviceIds.TempCpu, "cpuTemp", errors);
            int? tempC = null;
            if (temp.HasValue)
            {
                tempC = GHelperSensors.CpuTempC(temp.Value);
                if (!tempC.HasValue) errors.Add("cpuTemp: not reported by the device (raw " + temp.Value + ").");
            }
            d["cpuTempC"] = tempC;

            int? fan = ReadRaw(acpi, DeviceIds.CpuFan, "cpuFan", errors);
            int? rpm = null;
            if (fan.HasValue)
            {
                rpm = GHelperSensors.FanRpm(fan.Value);
                if (!rpm.HasValue) errors.Add("cpuFan: not reported by the device (raw " + fan.Value + ").");
            }
            d["fanRpm"] = rpm;

            int? gpuFan = ReadRaw(acpi, DeviceIds.GpuFan, "gpuFan", errors);
            int? gpuRpm = null;
            if (gpuFan.HasValue)
            {
                gpuRpm = GHelperSensors.FanRpm(gpuFan.Value);
                if (!gpuRpm.HasValue) errors.Add("gpuFan: not reported by the device (raw " + gpuFan.Value + ").");
            }
            d["gpuFanRpm"] = gpuRpm;

            ReadGpuSensors(acpi, adl, d, errors);
        }

        // gpuTempC, gpuUse (%), cpuPowerW, gpuPowerW: read only, after G-Helper HardwareControl.GetGPUTemp and its
        // AmdApu().GetiGpuSensors() uses. The temperature prefers ADL (edge, else GFX, else SOC) and, as GetGPUTemp
        // does, takes it only when 0 <= t < 125, else falls back to ACPI Temp_GPU (0x00120097) when 0 < t < 125;
        // neither gives null plus one error. gpuUse is ADL's graphics activity, cpuPowerW its CPU power and gpuPowerW
        // its graphics power (the same call); each is null without an error when ADL does not report it.
        public static void ReadGpuSensors(IAcpi acpi, IAdl adl, Dictionary<string, object> d, List<string> errors)
        {
            GpuSensors g = null;
            string adlProblem = "not available";
            if (adl != null)
            {
                try
                {
                    g = adl.GetGpuSensors();
                    if (g == null) adlProblem = adl.Unavailable ?? "the graphics driver did not report its sensors";
                }
                catch (Exception ex) { adlProblem = ex.Message; }
            }

            int? temp = null;
            if (g != null && g.TempC.HasValue && g.TempC.Value >= 0 && g.TempC.Value < 125) temp = g.TempC;
            else if (g != null) adlProblem = g.TempC.HasValue ? "temperature " + g.TempC.Value + " out of range" : "no temperature reported";
            if (!temp.HasValue)
            {
                int? raw = null;
                string acpiProblem;
                try { raw = acpi.DeviceGet(DeviceIds.TempGpu); acpiProblem = "raw " + raw.Value; }
                catch (Exception ex) { acpiProblem = ex.Message; }
                if (raw.HasValue && raw.Value > 0 && raw.Value < 125) temp = raw;
                else errors.Add("gpuTemp: not reported (AMD driver: " + adlProblem + "; ACPI: " + acpiProblem + ").");
            }
            d["gpuTempC"] = temp;
            d["gpuUse"] = g != null ? InRange(g.Use, 0, 100) : null;
            // A running CPU or GPU never draws 0 W: the Ally's driver leaves cpuPower at 0, so 0 means not reported.
            d["cpuPowerW"] = g != null ? InRange(g.CpuPowerW, 1, 1000) : null;
            d["gpuPowerW"] = g != null ? InRange(g.GfxPowerW, 1, 1000) : null;
        }

        private static int? InRange(int? v, int lo, int hi)
        {
            return v.HasValue && v.Value >= lo && v.Value <= hi ? v : null;
        }

        // A setting read (mode, spl, sppt, fppt): like ReadRaw, but the IOCTL-failure value -65536 is not a reading
        // and becomes null plus "name: not reported by the device (raw -65536)." (ruling P7).
        public static int? ReadSetting(IAcpi acpi, uint devId, string name, List<string> errors)
        {
            int? v = ReadRaw(acpi, devId, name, errors);
            if (v.HasValue && v.Value == NotReported)
            {
                errors.Add(name + ": not reported by the device (raw " + NotReported + ").");
                return null;
            }
            return v;
        }

        // A power limit read (spl, sppt, fppt): like ReadSetting, and 0 is "not reported" too (null, no error): the Ally
        // reads 0 while the firmware's own limits are in force (probe on the device, 2026-10-23).
        public static int? ReadPower(IAcpi acpi, uint devId, string name, List<string> errors)
        {
            int? v = ReadSetting(acpi, devId, name, errors);
            if (v.HasValue && v.Value == 0) return null;
            return v;
        }

        // Mode read back from the device: balanced/turbo/silent for 0/1/2, "other" for any other value (Armoury
        // Crate's own modes, e.g. 7 on the Ally), null when not read. Only ever reported, never written back.
        public static string ModeName(int? raw)
        {
            if (!raw.HasValue) return null;
            switch (raw.Value)
            {
                case DeviceIds.ModeBalanced: return "balanced";
                case DeviceIds.ModeTurbo: return "turbo";
                case DeviceIds.ModeSilent: return "silent";
                default: return "other";
            }
        }

        // 16-byte curve -> {temps, speeds}, or null when G-Helper's AsusACPI.IsInvalidCurve rejects it
        // (wrong length or all zero).
        public static Dictionary<string, object> SplitCurve(byte[] curve)
        {
            if (curve == null || AsusACPI.IsInvalidCurve(curve)) return null;
            var temps = new int[8];
            var speeds = new int[8];
            for (int i = 0; i < 8; i++) { temps[i] = curve[i]; speeds[i] = curve[i + 8]; }
            var d = new Dictionary<string, object>();
            d["temps"] = temps;
            d["speeds"] = speeds;
            return d;
        }

        // One DeviceGet; a failure becomes "name: message" in errors and null.
        public static int? ReadRaw(IAcpi acpi, uint devId, string name, List<string> errors)
        {
            try { return acpi.DeviceGet(devId); }
            catch (Exception ex) { errors.Add(name + ": " + ex.Message); return null; }
        }

        private string SetMode(object id, Dictionary<string, object> args)
        {
            string mode, err;
            if (!GetMode(args, true, out mode, out err)) return Reply(id, false, err, null);

            // Limits are required with "manual" and optional with the other modes: with Armoury Crate stopped the
            // Ally's firmware mode alone changes the fans but not the watts, so the plugin sends each mode's own.
            bool anyPower = args.ContainsKey("spl") || args.ContainsKey("sppt") || args.ContainsKey("fppt");
            int[] power = null;
            if (mode == "manual" || anyPower)
            {
                if (!GetPower(args, out power, out err)) return Reply(id, false, err, null);
            }

            if (!Set(DeviceIds.PerformanceMode, ModeValue(mode), "the performance mode", out err)) return Reply(id, false, err, null);
            lastMode = mode;
            if (power == null) lastPower = null;   // reset_fans puts back only limits sent with this mode

            var d = new Dictionary<string, object>();
            d["mode"] = mode;
            if (power != null)
            {
                WaitForModeLimits();
                if (!ApplyPower(power, out err)) return Reply(id, false, err, null);
                AddPower(d, power);
            }
            return Reply(id, true, "", d);
        }

        private string SetPower(object id, Dictionary<string, object> args)
        {
            int[] power;
            string err;
            if (!GetPower(args, out power, out err)) return Reply(id, false, err, null);
            if (!ApplyPower(power, out err)) return Reply(id, false, err, null);
            var d = new Dictionary<string, object>();
            AddPower(d, power);
            return Reply(id, true, "", d);
        }

        // fan: "cpu" (default) or "gpu" (G-Helper AsusFan.CPU / AsusFan.GPU).
        private string GetFans(object id, Dictionary<string, object> args)
        {
            string mode, err;
            int fan;
            if (!GetMode(args, false, out mode, out err)) return Reply(id, false, err, null);
            if (!GetFan(args, out fan, out err)) return Reply(id, false, err, null);
            if (mode == null) mode = lastMode ?? "balanced";

            Dictionary<string, object> curve = SplitCurve(acpi.GetFanCurve(fan, ModeValue(mode)));
            if (curve == null) return Reply(id, false, "The device did not return a " + FanName(fan) + " fan curve.", null);

            var d = new Dictionary<string, object>();
            d["mode"] = mode;
            d["fan"] = FanName(fan).ToLowerInvariant();
            d["temps"] = curve["temps"];
            d["speeds"] = curve["speeds"];
            return Reply(id, true, "", d);
        }

        private string SetFans(object id, Dictionary<string, object> args)
        {
            int[] temps, speeds;
            int fan;
            string err;
            if (!GetFan(args, out fan, out err)) return Reply(id, false, err, null);
            if (!GetIntArray(args, "temps", out temps, out err)) return Reply(id, false, err, null);
            if (!GetIntArray(args, "speeds", out speeds, out err)) return Reply(id, false, err, null);
            string reason;
            if (!Validation.CheckCurve(temps, speeds, out reason)) return Reply(id, false, reason, null);

            var curve = new byte[16];
            for (int i = 0; i < 8; i++) { curve[i] = (byte)temps[i]; curve[i + 8] = (byte)speeds[i]; }
            int result = acpi.SetFanCurve(fan, curve);
            if (result != 1)
                return Reply(id, false, "The device refused the " + FanName(fan) + " fan curve (result " + result + ").", null);
            var d = new Dictionary<string, object>();
            d["fan"] = FanName(fan).ToLowerInvariant();
            d["temps"] = temps;
            d["speeds"] = speeds;
            return Reply(id, true, "", d);
        }

        // G-Helper returns to the firmware fan curve by writing the current performance mode again
        // (app/Mode/ModeControl.cs AutoFans: DeviceSet(PerformanceMode, Modes.GetCurrentBase(), "Reset Mode")).
        // Like G-Helper, "current" is the mode this program set (it never reads the mode back from the device).
        // It never switches modes: without a mode set by this helper, or with a different mode, it refuses.
        // The mode write puts back the firmware curve of both fans (CPU and GPU) at once, as in G-Helper.
        private string ResetFans(object id, Dictionary<string, object> args)
        {
            string mode, err;
            if (!GetMode(args, false, out mode, out err)) return Reply(id, false, err, null);
            if (lastMode == null)
                return Reply(id, false, "Refused: set a performance mode first; reset_fans only resets the current mode's fan curve.", null);
            if (mode != null && mode != lastMode)
                return Reply(id, false, "Refused: reset_fans only resets the current mode's fan curve (current mode: " + lastMode + ").", null);
            mode = lastMode;
            if (!Set(DeviceIds.PerformanceMode, ModeValue(mode), "the performance mode", out err)) return Reply(id, false, err, null);
            lastMode = mode;
            bool reapplied = false;
            if (lastPower != null)
            {
                WaitForModeLimits();
                if (!ApplyPower(lastPower, out err)) return Reply(id, false, err, null);
                reapplied = true;
            }
            var d = new Dictionary<string, object>();
            d["mode"] = mode;
            d["powerReapplied"] = reapplied;
            d["fans"] = new[] { "cpu", "gpu" };
            return Reply(id, true, "", d);
        }

        private string GetFps(object id)
        {
            int f = adl.GetFpsLimit();
            if (f < 0) return Reply(id, false, FpsUnavailable(), null);
            var d = new Dictionary<string, object>();
            d["fps"] = f;
            return Reply(id, true, "", d);
        }

        private string SetFps(object id, Dictionary<string, object> args)
        {
            int fps;
            string err;
            if (!GetInt(args, "fps", out fps, out err)) return Reply(id, false, err, null);
            if (!Validation.IsAllowedFps(fps))
                return Reply(id, false, "Refused: FPS limit must be 0 (off) or one of 30, 40, 45, 50, 60, 75, 90, 120.", null);
            int result = adl.SetFpsLimit(fps);
            if (result == -1) return Reply(id, false, FpsUnavailable(), null);
            if (result != 1) return Reply(id, false, "The graphics driver refused the FPS limit.", null);
            var d = new Dictionary<string, object>();
            d["fps"] = fps;
            return Reply(id, true, "", d);
        }

        private string GetCpuBoost(object id)
        {
            if (WinPower == null) return Reply(id, false, WinPowerUnavailable, null);
            int[] b = WinPower.GetCpuBoost();
            if (b == null || b.Length != 2) return Reply(id, false, "Windows would not report the CPU Boost setting.", null);
            var d = new Dictionary<string, object>();
            d["ac"] = b[0];
            d["dc"] = b[1];
            return Reply(id, true, "", d);
        }

        private string SetCpuBoost(object id, Dictionary<string, object> args)
        {
            int value;
            string err;
            if (!GetInt(args, "value", out value, out err)) return Reply(id, false, err, null);
            if (value < 0 || value > 6) return Reply(id, false, "Refused: CPU Boost must be a whole number from 0 to 6.", null);
            if (WinPower == null) return Reply(id, false, WinPowerUnavailable, null);
            if (!WinPower.SetCpuBoost(value, out err)) return Reply(id, false, err ?? "Windows refused the CPU Boost setting.", null);
            var d = new Dictionary<string, object>();
            d["value"] = value;
            return Reply(id, true, "", d);
        }

        private static string OverlayMode(string guid)
        {
            if (guid == OverlayEco) return "eco";
            if (guid == OverlayBalanced) return "balanced";
            if (guid == OverlayBest) return "best";
            return "other";
        }

        private string GetPowerMode(object id)
        {
            if (WinPower == null) return Reply(id, false, WinPowerUnavailable, null);
            string overlay = WinPower.GetOverlay();
            if (overlay == null) return Reply(id, false, "Windows would not report the power mode.", null);
            var d = new Dictionary<string, object>();
            d["overlay"] = overlay;
            d["mode"] = OverlayMode(overlay);
            return Reply(id, true, "", d);
        }

        private string SetPowerMode(object id, Dictionary<string, object> args)
        {
            object raw;
            if (!args.TryGetValue("mode", out raw)) return Reply(id, false, "Refused: 'mode' is required.", null);
            string mode = raw as string;
            string guid = mode == "eco" ? OverlayEco : mode == "balanced" ? OverlayBalanced : mode == "best" ? OverlayBest : null;
            if (guid == null) return Reply(id, false, "Refused: power mode must be eco, balanced or best.", null);
            if (WinPower == null) return Reply(id, false, WinPowerUnavailable, null);
            string err;
            if (!WinPower.SetOverlay(guid, out err)) return Reply(id, false, err ?? "Windows refused the power mode.", null);
            var d = new Dictionary<string, object>();
            d["mode"] = mode;
            d["overlay"] = guid;
            return Reply(id, true, "", d);
        }

        private string FpsUnavailable()
        {
            string why = adl.Unavailable;
            return "AMD frame rate control is unavailable" + (string.IsNullOrEmpty(why) ? "" : ": " + why) + ".";
        }

        // ---------- Armoury Crate services ----------
        // Only names in ArmouryServices (G-Helper AsusService.cs services + servicesAC) are ever touched.

        private string ArmouryState(object id)
        {
            Dictionary<string, string> types = services.StartTypes(ArmouryServices.All);
            List<string> running = services.Running(ArmouryServices.All);
            bool installed = false;
            foreach (string m in ArmouryServices.InstalledMarkers) if (types.ContainsKey(m)) installed = true;
            var d = new Dictionary<string, object>();
            d["installed"] = installed;
            d["startTypes"] = Ordered(types);
            d["running"] = running;
            return Reply(id, true, "", d);
        }

        // G-Helper AsusService.StopAsusServices: each running listed service is stopped and set to Manual (servicesAC)
        // or Disabled (services); services that are not running are left alone. The start types and running set are
        // read before the first change and returned, so the caller can store them for armoury_restore.
        // A failure rolls every service touched so far back to the recorded state.
        private string ArmouryStop(object id)
        {
            Dictionary<string, string> types = services.StartTypes(ArmouryServices.All);
            List<string> running = services.Running(ArmouryServices.All);

            var steps = new List<ServiceStep>();
            foreach (string name in ArmouryServices.All)
                if (running.Contains(name)) steps.Add(new ServiceStep(name, ServiceAction.Stop, ArmouryServices.StopStartType(name)));

            var data = new Dictionary<string, object>();
            data["startTypes"] = Ordered(types);
            data["running"] = running;
            string err;
            if (!ApplyServiceSteps(steps, types, running, "stop", data, out err)) return Reply(id, false, err, data);
            return Reply(id, true, "", data);
        }

        // Puts back exactly the given start types and starts the given running services, in list order. Services that
        // are no longer installed are skipped (reported in data.skipped). Nothing else is stopped or changed.
        // A failure rolls every service touched so far back to how it was before this call.
        private string ArmouryRestore(object id, Dictionary<string, object> args)
        {
            object raw;
            if (!args.TryGetValue("recorded", out raw) || !(raw is Dictionary<string, object>))
                return Reply(id, false, "Refused: 'recorded' must be an object of service name to start type.", null);
            var recorded = new Dictionary<string, string>();
            foreach (KeyValuePair<string, object> kv in (Dictionary<string, object>)raw)
            {
                if (!(kv.Value is string)) return Reply(id, false, "Refused: start type for '" + kv.Key + "' must be a string.", null);
                if (!ArmouryServices.IsListed(kv.Key))
                    return Reply(id, false, "Refused: '" + kv.Key + "' is not one of the Armoury Crate services Overdrive manages.", null);
                if (!ArmouryServices.IsStartType((string)kv.Value))
                    return Reply(id, false, "Refused: start type for '" + kv.Key + "' must be one of "
                        + string.Join(", ", ArmouryServices.StartTypeNames) + ".", null);
                recorded[kv.Key] = (string)kv.Value;
            }
            if (!args.TryGetValue("running", out raw) || !(raw is object[]))
                return Reply(id, false, "Refused: 'running' must be a list of service names.", null);
            var toStart = new List<string>();
            foreach (object o in (object[])raw)
            {
                string name = o as string;
                if (name == null) return Reply(id, false, "Refused: 'running' must contain only service names.", null);
                if (!ArmouryServices.IsListed(name))
                    return Reply(id, false, "Refused: '" + name + "' is not one of the Armoury Crate services Overdrive manages.", null);
                if (!recorded.ContainsKey(name))
                    return Reply(id, false, "Refused: running service '" + name + "' has no recorded start type.", null);
                toStart.Add(name);
            }

            Dictionary<string, string> types = services.StartTypes(ArmouryServices.All);
            List<string> running = services.Running(ArmouryServices.All);

            // Two passes, so a service that depends on another listed service never starts while that one is still
            // Disabled: first every recorded start type (G-Helper list order), then the services that were running,
            // in the recorded order. A failure rolls back every step so far, as before.
            var steps = new List<ServiceStep>();
            var skipped = new List<string>();
            foreach (string name in ArmouryServices.All)
            {
                string type;
                if (!recorded.TryGetValue(name, out type)) continue;
                if (!types.ContainsKey(name)) { skipped.Add(name); continue; }
                steps.Add(new ServiceStep(name, ServiceAction.SetType, type));
            }
            foreach (string name in toStart)
            {
                if (!types.ContainsKey(name)) continue;
                steps.Add(new ServiceStep(name, ServiceAction.Start, recorded[name]));
            }

            var data = new Dictionary<string, object>();
            data["skipped"] = skipped;
            string err;
            if (!ApplyServiceSteps(steps, types, running, "restore", data, out err)) return Reply(id, false, err, data);
            return Reply(id, true, "", data);
        }

        private enum ServiceAction { Stop, Start, SetType }

        private class ServiceStep
        {
            public readonly string Name;
            public readonly ServiceAction Action;
            public readonly string StartType;
            public ServiceStep(string name, ServiceAction action, string startType) { Name = name; Action = action; StartType = startType; }
        }

        // Runs the steps in order. On the first failure, every step done so far (the failed one included, as it may
        // have half-happened) is undone in reverse: back to its start type in `before`, and running only if it was.
        // Only the listed services this call changed are put back: a dependent service that ServiceController.Stop
        // stopped alongside one of them is not restarted (same as G-Helper, whose Stop-Service -Force does the same).
        // Worst case duration: up to 30 s per stop/start (ServiceHardware.Wait), times the services changed, and the
        // same again for a rollback; dependents add their own stop time.
        // data gets "failed" and "rolledBack"; err names the service and lists any rollback that also failed.
        private bool ApplyServiceSteps(List<ServiceStep> steps, Dictionary<string, string> before, List<string> wasRunning,
            string what, Dictionary<string, object> data, out string err)
        {
            err = null;
            for (int i = 0; i < steps.Count; i++)
            {
                ServiceStep st = steps[i];
                try
                {
                    switch (st.Action)
                    {
                        case ServiceAction.Stop: services.Stop(st.Name, st.StartType); break;
                        case ServiceAction.Start: services.Start(st.Name, st.StartType); break;
                        default: services.SetStartType(st.Name, st.StartType); break;
                    }
                }
                catch (Exception ex)
                {
                    string verb = st.Action == ServiceAction.Stop ? "stop" : (st.Action == ServiceAction.Start ? "start" : "set the start type of");
                    var rollbackFailures = new List<string>();
                    for (int j = i; j >= 0; j--)
                    {
                        ServiceStep u = steps[j];
                        string type;
                        if (!before.TryGetValue(u.Name, out type)) continue;
                        try
                        {
                            if (wasRunning.Contains(u.Name)) services.Start(u.Name, type);
                            else if (u.Action == ServiceAction.Start) services.Stop(u.Name, type);
                            else services.SetStartType(u.Name, type);
                        }
                        catch (Exception rex) { rollbackFailures.Add(u.Name + " (" + rex.Message + ")"); }
                    }
                    data["failed"] = st.Name;
                    data["rolledBack"] = rollbackFailures.Count == 0;
                    err = "Could not " + verb + " " + st.Name + " (" + what + "): " + ex.Message.TrimEnd('.') + "."
                        + (rollbackFailures.Count == 0
                            ? " Every service changed so far was put back."
                            : " Rollback also failed for " + string.Join(", ", rollbackFailures) + ".");
                    return false;
                }
            }
            return true;
        }

        // Start types in ArmouryServices order, for stable replies.
        private static Dictionary<string, object> Ordered(Dictionary<string, string> types)
        {
            var d = new Dictionary<string, object>();
            foreach (string name in ArmouryServices.All)
            {
                string t;
                if (types.TryGetValue(name, out t)) d[name] = t;
            }
            return d;
        }

        // ---------- hardware helpers ----------

        // G-Helper's gap between the mode write and AutoPower (100 ms, then AutoFans, then 1000 ms). The fan curve
        // is written by the plugin after the reply (the protocol sends limits together with mode "manual").
        private void WaitForModeLimits()
        {
            sleep(ModeToFansDelayMs);
            sleep(FansToPowerDelayMs);
        }

        private bool ApplyPower(int[] p, out string err)
        {
            // Order as G-Helper ModeControl.SetPower: SPL (A3), sPPT (A0), then fPPT (C1).
            if (!Set(DeviceIds.Spl, p[0], "the sustained power limit", out err)) return false;
            if (!Set(DeviceIds.Sppt, p[1], "the slow boost limit", out err)) return false;
            if (!Set(DeviceIds.Fppt, p[2], "the fast boost limit", out err)) return false;
            lastPower = p;
            return true;
        }

        private bool Set(uint devId, int value, string what, out string err)
        {
            int result = acpi.DeviceSet(devId, value);
            if (result != 1) { err = "The device refused " + what + " (result " + result + ")."; return false; }
            err = null;
            return true;
        }

        private static int ModeValue(string mode)
        {
            switch (mode)
            {
                case "silent": return DeviceIds.ModeSilent;
                case "turbo": return DeviceIds.ModeTurbo;
                default: return DeviceIds.ModeBalanced;   // balanced, and manual = balanced + custom limits
            }
        }

        private static void AddPower(Dictionary<string, object> d, int[] p)
        {
            d["spl"] = p[0];
            d["sppt"] = p[1];
            d["fppt"] = p[2];
        }

        // ---------- argument parsing ----------

        private static bool GetMode(Dictionary<string, object> args, bool required, out string mode, out string err)
        {
            mode = null;
            err = null;
            object raw;
            if (!args.TryGetValue("mode", out raw))
            {
                if (!required) return true;
                err = "Refused: 'mode' is required.";
                return false;
            }
            string s = raw as string;
            if (s != "silent" && s != "balanced" && s != "turbo" && s != "manual")
            {
                err = "Refused: mode must be silent, balanced, turbo or manual.";
                return false;
            }
            mode = s;
            return true;
        }

        // Optional "fan": "cpu" (the default when absent) or "gpu".
        private static bool GetFan(Dictionary<string, object> args, out int fan, out string err)
        {
            fan = DeviceIds.FanCpu;
            err = null;
            object raw;
            if (!args.TryGetValue("fan", out raw)) return true;
            string s = raw as string;
            if (s == "cpu") return true;
            if (s == "gpu") { fan = DeviceIds.FanGpu; return true; }
            err = "Refused: fan must be cpu or gpu.";
            return false;
        }

        public static string FanName(int fan)
        {
            return fan == DeviceIds.FanGpu ? "GPU" : "CPU";
        }

        private static bool GetPower(Dictionary<string, object> args, out int[] power, out string err)
        {
            power = null;
            int spl, sppt, fppt;
            if (!GetInt(args, "spl", out spl, out err)) return false;
            if (!GetInt(args, "sppt", out sppt, out err)) return false;
            if (!GetInt(args, "fppt", out fppt, out err)) return false;
            power = Validation.ClampPower(spl, sppt, fppt);
            return true;
        }

        private static bool GetInt(Dictionary<string, object> args, string key, out int value, out string err)
        {
            value = 0;
            err = null;
            object raw;
            if (!args.TryGetValue(key, out raw)) { err = "Refused: '" + key + "' is required."; return false; }
            if (!TryInt(raw, out value)) { err = "Refused: '" + key + "' must be a whole number."; return false; }
            return true;
        }

        private static bool GetIntArray(Dictionary<string, object> args, string key, out int[] values, out string err)
        {
            values = null;
            err = null;
            object raw;
            if (!args.TryGetValue(key, out raw)) { err = "Refused: '" + key + "' is required."; return false; }
            var arr = raw as object[];
            if (arr == null) { err = "Refused: '" + key + "' must be a list of whole numbers."; return false; }
            values = new int[arr.Length];
            for (int i = 0; i < arr.Length; i++)
                if (!TryInt(arr[i], out values[i])) { err = "Refused: '" + key + "' must be a list of whole numbers."; return false; }
            return true;
        }

        // JavaScriptSerializer gives int or long for whole numbers, decimal for fractions; anything else is not an int.
        private static bool TryInt(object raw, out int value)
        {
            value = 0;
            if (raw is int) { value = (int)raw; return true; }
            if (raw is long)
            {
                long l = (long)raw;
                if (l < int.MinValue || l > int.MaxValue) return false;
                value = (int)l;
                return true;
            }
            return false;
        }

        // ---------- reply ----------

        private string Reply(object id, bool ok, string message, object data)
        {
            var r = new Dictionary<string, object>();
            r["id"] = id;
            r["ok"] = ok;
            r["message"] = message ?? "";
            r["data"] = data;
            return json.Serialize(r);
        }
    }
}
