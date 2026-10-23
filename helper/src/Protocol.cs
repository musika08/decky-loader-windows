using System;
using System.Collections.Generic;
using System.Web.Script.Serialization;

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
                case "armoury_state":
                case "armoury_stop":
                    return Reply(id, false, "Armoury Crate control is not available in this build.", null);
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
            d["modeRaw"] = Read(DeviceIds.PerformanceMode, "mode", errors);
            d["spl"] = Read(DeviceIds.Spl, "spl", errors);
            d["sppt"] = Read(DeviceIds.Sppt, "sppt", errors);
            d["fppt"] = Read(DeviceIds.Fppt, "fppt", errors);
            d["lastMode"] = lastMode;
            object fps = null;
            try
            {
                int f = adl.GetFpsLimit();
                if (f < 0) errors.Add("fps: AMD frame rate control is unavailable."); else fps = f;
            }
            catch (Exception ex) { errors.Add("fps: " + ex.Message); }
            d["fpsLimit"] = fps;
            d["errors"] = errors;
            return Reply(id, true, "", d);
        }

        // Raw readings; Task 3 adds G-Helper's conversions to degrees C and RPM.
        private string Sensors(object id)
        {
            var d = new Dictionary<string, object>();
            var errors = new List<string>();
            d["cpuTempRaw"] = Read(DeviceIds.TempCpu, "cpuTemp", errors);
            d["cpuFanRaw"] = Read(DeviceIds.CpuFan, "cpuFan", errors);
            d["errors"] = errors;
            return Reply(id, true, "", d);
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

            byte[] curve = acpi.GetFanCurve(DeviceIds.FanCpu, ModeValue(mode));
            // G-Helper AsusACPI.IsInvalidCurve: wrong length or all zero.
            if (curve == null || curve.Length != 16 || Array.TrueForAll(curve, b => b == 0))
                return Reply(id, false, "The device did not return a fan curve.", null);

            var temps = new int[8];
            var speeds = new int[8];
            for (int i = 0; i < 8; i++) { temps[i] = curve[i]; speeds[i] = curve[i + 8]; }
            var d = new Dictionary<string, object>();
            d["mode"] = mode;
            d["temps"] = temps;
            d["speeds"] = speeds;
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

        // G-Helper returns to the firmware fan curve by writing the performance mode again
        // (app/Mode/ModeControl.cs AutoFans, "resetting to default profile").
        private string ResetFans(object id, Dictionary<string, object> args)
        {
            string mode, err;
            if (!GetMode(args, false, out mode, out err)) return Reply(id, false, err, null);
            if (mode == null) mode = lastMode ?? "balanced";
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
            if (f < 0) return Reply(id, false, "AMD frame rate control is unavailable.", null);
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
            if (result == -1) return Reply(id, false, "AMD frame rate control is unavailable.", null);
            if (result != 1) return Reply(id, false, "The graphics driver refused the FPS limit.", null);
            var d = new Dictionary<string, object>();
            d["fps"] = fps;
            return Reply(id, true, "", d);
        }

        private string ArmouryRestore(object id, Dictionary<string, object> args)
        {
            object raw;
            if (!args.TryGetValue("recorded", out raw) || !(raw is Dictionary<string, object>))
                return Reply(id, false, "Refused: 'recorded' must be an object of service name to start type.", null);
            foreach (KeyValuePair<string, object> kv in (Dictionary<string, object>)raw)
                if (!(kv.Value is string)) return Reply(id, false, "Refused: start type for '" + kv.Key + "' must be a string.", null);
            if (!args.TryGetValue("running", out raw) || !(raw is object[]))
                return Reply(id, false, "Refused: 'running' must be a list of service names.", null);
            foreach (object o in (object[])raw)
                if (!(o is string)) return Reply(id, false, "Refused: 'running' must contain only service names.", null);
            return Reply(id, false, "Armoury Crate control is not available in this build.", null);
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

        private object Read(uint devId, string name, List<string> errors)
        {
            try { return acpi.DeviceGet(devId); }
            catch (Exception ex) { errors.Add(name + ": " + ex.Message); return null; }
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
