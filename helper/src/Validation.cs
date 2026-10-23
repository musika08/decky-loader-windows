using System;

namespace Overdrive.Helper
{
    // Limits the helper enforces on every request, whatever the caller already checked.
    // The reason strings returned by CheckCurve are copied verbatim by src/curve.ts (ruling P1); change both together.
    public static class Validation
    {
        public const int PowerMin = 5;    // G-Helper AsusACPI.MinTotal
        public const int PowerMax = 50;   // G-Helper AsusACPI.MaxTotal for the Ally

        public static readonly int[] AllowedFps = { 0, 30, 40, 45, 50, 60, 75, 90, 120 };

        public const int CurvePoints = 8;
        // G-Helper app/Fans.cs: tempMin = 20, tempMax = 110, fansMax = 100 (lines 31-34), and the fan editor
        // keeps every point at or above dymin = (temp - 70) * 1.2 (lines 1516-1517 and 1614-1616).
        public const int CurveTempMin = 20;
        public const int CurveTempMax = 110;
        public const int CurveSpeedMax = 100;

        // Each limit to 5..50 W, then fPPT >= sPPT >= SPL by raising the later ones.
        public static int[] ClampPower(int spl, int sppt, int fppt)
        {
            spl = Clamp(spl, PowerMin, PowerMax);
            sppt = Clamp(sppt, PowerMin, PowerMax);
            fppt = Clamp(fppt, PowerMin, PowerMax);
            if (sppt < spl) sppt = spl;
            if (fppt < sppt) fppt = sppt;
            return new[] { spl, sppt, fppt };
        }

        public static bool IsAllowedFps(int fps)
        {
            return Array.IndexOf(AllowedFps, fps) >= 0;
        }

        // Lowest fan percentage G-Helper's editor allows at this temperature: ceil((temp - 70) * 1.2), at least 0.
        public static int MinSpeedAt(int temp)
        {
            int tenths = (temp - 70) * 12;            // (temp - 70) * 1.2 in tenths of a percent
            if (tenths <= 0) return 0;
            return (tenths + 9) / 10;
        }

        public static bool CheckCurve(int[] temps, int[] speeds, out string reason)
        {
            reason = "";
            if (temps == null || speeds == null || temps.Length != CurvePoints || speeds.Length != CurvePoints)
            {
                reason = "The fan curve needs 8 points.";
                return false;
            }
            for (int i = 0; i < CurvePoints; i++)
            {
                int n = i + 1;
                if (temps[i] < CurveTempMin || temps[i] > CurveTempMax)
                {
                    reason = "Point " + n + ": temperature must be between " + CurveTempMin + " and " + CurveTempMax + " °C.";
                    return false;
                }
                if (speeds[i] < 0 || speeds[i] > CurveSpeedMax)
                {
                    reason = "Point " + n + ": fan speed must be between 0 and " + CurveSpeedMax + " %.";
                    return false;
                }
                // The firmware's own curves repeat the last temperature (e.g. 62,62,62 on the Ally), so temperatures
                // only have to be non-decreasing; points that share a temperature must share the fan speed.
                if (i > 0 && temps[i] < temps[i - 1])
                {
                    reason = "Point " + n + ": temperature can't be lower than point " + i + ".";
                    return false;
                }
                if (i > 0 && speeds[i] < speeds[i - 1])
                {
                    reason = "Point " + n + ": fan speed can't be lower than point " + i + ".";
                    return false;
                }
                if (i > 0 && temps[i] == temps[i - 1] && speeds[i] != speeds[i - 1])
                {
                    reason = "Point " + n + ": same temperature as point " + i + ", so the fan speed must be the same.";
                    return false;
                }
                int min = MinSpeedAt(temps[i]);
                if (speeds[i] < min)
                {
                    reason = "Point " + n + ": at " + temps[i] + " °C the fan must be at least " + min + " %.";
                    return false;
                }
            }
            return true;
        }

        private static int Clamp(int v, int lo, int hi)
        {
            return v < lo ? lo : (v > hi ? hi : v);
        }
    }
}
