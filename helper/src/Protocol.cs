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
            { "set_mode", new[] { "mode", "spl", "sppt", "fppt" } },
            { "set_power", new[] { "spl", "sppt", "fppt" } },
            { "get_fans", new[] { "mode" } },
            { "set_fans", new[] { "temps", "speeds" } },
            { "reset_fans", new[] { "mode" } },
            { "get_fps_limit", None },
            { "set_fps_limit", new[] { "fps" } },
            { "armoury_state", None },
            { "armoury_stop", None },
            { "armoury_restore", new[] { "recorded", "running" } },
        };

        private static readonly string[] TopLevel = { "id", "cmd", "args" };

        // The only commands allowed on a device that is not an Ally (ruling P7: an allow-list of reads, so a command
        // added later is refused there unless it is listed here on purpose).
        public static readonly string[] ReadCommands =
        {
            "hello", "status", "sensors", "get_fans", "get_fps_limit", "armoury_state"
        };

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

        public HelperCore(IAcpi acpi, IAdl adl, IServices services, bool ally)
        {
            this.acpi = acpi;
            this.adl = adl;
            this.services = services;
            this.ally = ally;
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
            d["modeRaw"] = ReadSetting(acpi, DeviceIds.PerformanceMode, "mode", errors);
            d["spl"] = ReadSetting(acpi, DeviceIds.Spl, "spl", errors);
            d["sppt"] = ReadSetting(acpi, DeviceIds.Sppt, "sppt", errors);
            d["fppt"] = ReadSetting(acpi, DeviceIds.Fppt, "fppt", errors);
            d["lastMode"] = lastMode;
            object fps = null;
            try
            {
                int f = adl.GetFpsLimit();
                if (f < 0) errors.Add("fps: " + FpsUnavailable()); else fps = f;
            }
            catch (Exception ex) { errors.Add("fps: " + ex.Message); }
            d["fpsLimit"] = fps;
            ReadSensors(acpi, d, errors);
            d["errors"] = errors;
            return Reply(id, true, "", d);
        }

        private string Sensors(object id)
        {
            var d = new Dictionary<string, object>();
            var errors = new List<string>();
            ReadSensors(acpi, d, errors);
            d["errors"] = errors;
            return Reply(id, true, "", d);
        }

        // cpuTempC and fanRpm, converted as G-Helper does (GHelperSensors); null plus an error when not reported.
        // Shared by status, sensors and --probe.
        public static void ReadSensors(IAcpi acpi, Dictionary<string, object> d, List<string> errors)
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

            bool anyPower = args.ContainsKey("spl") || args.ContainsKey("sppt") || args.ContainsKey("fppt");
            int[] power = null;
            if (mode == "manual")
            {
                if (!GetPower(args, out power, out err)) return Reply(id, false, err, null);
            }
            else if (anyPower)
                return Reply(id, false, "Refused: power limits can only be set with mode 'manual'.", null);

            if (!Set(DeviceIds.PerformanceMode, ModeValue(mode), "the performance mode", out err)) return Reply(id, false, err, null);
            lastMode = mode;

            var d = new Dictionary<string, object>();
            d["mode"] = mode;
            if (power != null)
            {
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

        private string GetFans(object id, Dictionary<string, object> args)
        {
            string mode, err;
            if (!GetMode(args, false, out mode, out err)) return Reply(id, false, err, null);
            if (mode == null) mode = lastMode ?? "balanced";

            Dictionary<string, object> curve = SplitCurve(acpi.GetFanCurve(DeviceIds.FanCpu, ModeValue(mode)));
            if (curve == null) return Reply(id, false, "The device did not return a fan curve.", null);

            var d = new Dictionary<string, object>();
            d["mode"] = mode;
            d["temps"] = curve["temps"];
            d["speeds"] = curve["speeds"];
            return Reply(id, true, "", d);
        }

        private string SetFans(object id, Dictionary<string, object> args)
        {
            int[] temps, speeds;
            string err;
            if (!GetIntArray(args, "temps", out temps, out err)) return Reply(id, false, err, null);
            if (!GetIntArray(args, "speeds", out speeds, out err)) return Reply(id, false, err, null);
            string reason;
            if (!Validation.CheckCurve(temps, speeds, out reason)) return Reply(id, false, reason, null);

            var curve = new byte[16];
            for (int i = 0; i < 8; i++) { curve[i] = (byte)temps[i]; curve[i + 8] = (byte)speeds[i]; }
            int result = acpi.SetFanCurve(DeviceIds.FanCpu, curve);
            if (result != 1) return Reply(id, false, "The device refused the fan curve (result " + result + ").", null);
            var d = new Dictionary<string, object>();
            d["temps"] = temps;
            d["speeds"] = speeds;
            return Reply(id, true, "", d);
        }

        // G-Helper returns to the firmware fan curve by writing the current performance mode again
        // (app/Mode/ModeControl.cs AutoFans: DeviceSet(PerformanceMode, Modes.GetCurrentBase(), "Reset Mode")).
        // Like G-Helper, "current" is the mode this program set (it never reads the mode back from the device).
        // It never switches modes: without a mode set by this helper, or with a different mode, it refuses.
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
            if (mode == "manual" && lastPower != null)
            {
                if (!ApplyPower(lastPower, out err)) return Reply(id, false, err, null);
                reapplied = true;
            }
            var d = new Dictionary<string, object>();
            d["mode"] = mode;
            d["powerReapplied"] = reapplied;
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

            var steps = new List<ServiceStep>();
            var skipped = new List<string>();
            foreach (string name in ArmouryServices.All)
            {
                string type;
                if (!recorded.TryGetValue(name, out type)) continue;
                if (!types.ContainsKey(name)) { skipped.Add(name); continue; }
                steps.Add(new ServiceStep(name, toStart.Contains(name) ? ServiceAction.Start : ServiceAction.SetType, type));
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
