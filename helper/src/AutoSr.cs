// Overdrive: Auto SR (Windows "Automatic super resolution") pair editor. Pure string handling, no registry access.
//
// HKCU\Software\Microsoft\DirectX\UserGpuPreferences, REG_SZ DirectXUserGlobalSettings holds ";"-separated "Name=Value;"
// pairs, for example "VRROptimizeEnable=1;DXGIEffects=1028;SwapEffectUpgradeEnable=1;AutoHDREnable=0;". The Auto SR
// switch is the DXGIEffects pair: 1028 on, 1024 off (confirmed on the owner's Ally, 2026-10-24).

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using Ally.Shared;

namespace Overdrive.Helper
{
    public static class AutoSr
    {
        public const string Key = "DXGIEffects";
        public const int On = 1028;
        public const int Off = 1024;

        // The DXGIEffects value, or null when the pair is missing or not a whole number.
        public static int? Read(string settings)
        {
            foreach (string pair in Split(settings))
            {
                string value;
                if (!IsKey(pair, out value)) continue;
                int n;
                if (int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out n)) return n;
                return null;
            }
            return null;
        }

        // Replaces only the DXGIEffects pair (appended when missing). Every other pair keeps its text and order, but the
        // string is rebuilt: empty segments are dropped, every pair ends with ";" (also when the input had no trailing
        // ";"), and a repeated DXGIEffects pair after the first is removed.
        public static string Write(string settings, bool on)
        {
            string newPair = Key + "=" + (on ? On : Off);
            var sb = new StringBuilder();
            bool done = false;
            foreach (string pair in Split(settings))
            {
                string value;
                if (IsKey(pair, out value))
                {
                    if (done) continue;   // a repeated DXGIEffects pair is dropped so only one remains
                    sb.Append(newPair).Append(';');
                    done = true;
                }
                else sb.Append(pair).Append(';');
            }
            if (!done) sb.Append(newPair).Append(';');
            return sb.ToString();
        }

        private static IEnumerable<string> Split(string settings)
        {
            if (string.IsNullOrEmpty(settings)) yield break;
            foreach (string p in settings.Split(';'))
                if (p.Trim().Length > 0) yield return p;
        }

        private static bool IsKey(string pair, out string value)
        {
            value = null;
            int eq = pair.IndexOf('=');
            if (eq < 0) return false;
            if (!string.Equals(pair.Substring(0, eq).Trim(), Key, StringComparison.OrdinalIgnoreCase)) return false;
            value = pair.Substring(eq + 1).Trim();
            return true;
        }
    }
}
