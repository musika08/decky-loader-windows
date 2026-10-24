using System;
using System.Collections.Generic;
using System.Web.Script.Serialization;

namespace Ally.Shared
{
    // The JSON-lines protocol pieces both helpers use: the reply line, the typed argument readers and the
    // allowed-argument check. Pure functions; no hardware.
    public static class ProtocolKit
    {
        // One reply line: {"id":n,"ok":bool,"message":"","data":...}.
        public static string Reply(object id, bool ok, string message, object data)
        {
            var r = new Dictionary<string, object>();
            r["id"] = id;
            r["ok"] = ok;
            r["message"] = message ?? "";
            r["data"] = data;
            return new JavaScriptSerializer().Serialize(r);
        }

        // Null when every argument name is allowed for the command; otherwise the refusal message.
        // commands maps a command name to its allowed argument names.
        public static string CheckArgs(Dictionary<string, string[]> commands, string cmd, Dictionary<string, object> args)
        {
            string[] allowed;
            if (cmd == null || !commands.TryGetValue(cmd, out allowed))
                return "Refused: unknown command '" + (cmd ?? "") + "'.";
            foreach (string key in args.Keys)
                if (Array.IndexOf(allowed, key) < 0)
                    return "Refused: unknown argument '" + key + "' for " + cmd + ".";
            return null;
        }

        public static bool GetBool(Dictionary<string, object> args, string key, out bool value, out string err)
        {
            value = false;
            err = null;
            object raw;
            if (!args.TryGetValue(key, out raw)) { err = "Refused: '" + key + "' is required."; return false; }
            if (!(raw is bool)) { err = "Refused: '" + key + "' must be true or false."; return false; }
            value = (bool)raw;
            return true;
        }

        public static bool GetInt(Dictionary<string, object> args, string key, out int value, out string err)
        {
            value = 0;
            err = null;
            object raw;
            if (!args.TryGetValue(key, out raw)) { err = "Refused: '" + key + "' is required."; return false; }
            if (!TryInt(raw, out value)) { err = "Refused: '" + key + "' must be a whole number."; return false; }
            return true;
        }

        public static bool GetIntArray(Dictionary<string, object> args, string key, out int[] values, out string err)
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
        public static bool TryInt(object raw, out int value)
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
    }
}
