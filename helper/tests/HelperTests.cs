using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Web.Script.Serialization;

namespace Overdrive.Helper.Tests
{
    // Tiny runner in the house style: every public static parameterless method of HelperTests is a test.
    // Prints PASS/FAIL per test; the exit code is the number of failures.
    public static class TestRunner
    {
        public static int Main(string[] args)
        {
            int passed = 0, failed = 0;
            var tests = new List<MethodInfo>(typeof(HelperTests).GetMethods(BindingFlags.Public | BindingFlags.Static | BindingFlags.DeclaredOnly));
            tests.Sort((a, b) => string.CompareOrdinal(a.Name, b.Name));
            foreach (MethodInfo m in tests)
            {
                if (m.GetParameters().Length != 0) continue;
                try
                {
                    m.Invoke(null, null);
                    passed++;
                    Console.WriteLine("PASS " + m.Name);
                }
                catch (Exception ex)
                {
                    failed++;
                    Exception inner = (ex is TargetInvocationException && ex.InnerException != null) ? ex.InnerException : ex;
                    Console.WriteLine("FAIL " + m.Name + " : " + inner.Message);
                }
            }
            Console.WriteLine();
            Console.WriteLine("Helper tests: " + passed + " passed, " + failed + " failed, " + (passed + failed) + " total");
            return failed;
        }
    }

    public class TestFailure : Exception
    {
        public TestFailure(string message) : base(message) { }
    }

    public static class HelperTests
    {
        // ---------- helpers ----------

        private static readonly JavaScriptSerializer Json = new JavaScriptSerializer();

        private static void True(bool cond, string what) { if (!cond) throw new TestFailure(what); }

        private static void Equal(object expected, object actual, string what)
        {
            if (!object.Equals(expected, actual))
                throw new TestFailure(what + ": expected <" + expected + "> got <" + actual + ">");
        }

        private static void SeqEqual(IList<string> expected, IList<string> actual, string what)
        {
            string e = string.Join(" | ", expected), a = string.Join(" | ", actual);
            if (e != a) throw new TestFailure(what + ":\n  expected " + e + "\n  got      " + a);
        }

        private class Rig
        {
            public FakeAcpi Acpi = new FakeAcpi();
            public FakeAdl Adl = new FakeAdl();
            public FakeServices Services = new FakeServices();
            public List<int> Sleeps = new List<int>();      // delays the helper asked for, never actually waited
            public HelperCore Core;
            public Rig() : this(true) { }
            public Rig(bool ally) { Core = new HelperCore(Acpi, Adl, Services, ally, ms => Sleeps.Add(ms)); }

            public Dictionary<string, object> Send(string line)
            {
                string reply = Core.HandleLine(line);
                True(reply != null && reply.IndexOf('\n') < 0, "reply must be one line");
                return (Dictionary<string, object>)Json.DeserializeObject(reply);
            }
        }

        private static bool Ok(Dictionary<string, object> r) { return (bool)r["ok"]; }
        private static string Msg(Dictionary<string, object> r) { return (string)r["message"]; }

        private static int[] T(params int[] v) { return v; }

        // A curve that passes every rule: temps 30..100, speeds 0..70.
        private static int[] GoodTemps() { return T(30, 40, 50, 60, 70, 80, 90, 100); }
        private static int[] GoodSpeeds() { return T(0, 10, 20, 30, 40, 50, 60, 70); }

        // ---------- protocol ----------

        public static void Hello_ReportsVersionAndAlly()
        {
            var rig = new Rig();
            var r = rig.Send("{\"id\":1,\"cmd\":\"hello\"}");
            True(Ok(r), "hello ok");
            Equal(1, r["id"], "id echoed");
            var data = (Dictionary<string, object>)r["data"];
            Equal("0.1.0", data["version"], "version");
            Equal(true, data["ally"], "ally");
        }

        public static void UnknownCommand_Refused()
        {
            var rig = new Rig();
            var r = rig.Send("{\"id\":7,\"cmd\":\"set_charge_limit\",\"args\":{\"limit\":80}}");
            True(!Ok(r), "unknown command must be refused");
            Equal(7, r["id"], "id echoed");
            True(Msg(r).Contains("unknown command"), "message names the problem: " + Msg(r));
            Equal(0, rig.Acpi.Calls.Count, "no hardware call");
        }

        public static void UnknownArg_Refused()
        {
            var rig = new Rig();
            var r = rig.Send("{\"id\":2,\"cmd\":\"set_power\",\"args\":{\"spl\":20,\"sppt\":25,\"fppt\":30,\"tdp\":40}}");
            True(!Ok(r), "unknown arg must be refused");
            True(Msg(r).Contains("tdp"), "message names the arg: " + Msg(r));
            Equal(0, rig.Acpi.Calls.Count, "no hardware call");

            r = rig.Send("{\"id\":3,\"cmd\":\"hello\",\"args\":{\"x\":1}}");
            True(!Ok(r), "hello takes no args");

            r = rig.Send("{\"id\":4,\"cmd\":\"hello\",\"extra\":1}");
            True(!Ok(r), "unknown top-level field refused");
        }

        public static void Malformed_Json_ReturnsError_NotCrash()
        {
            var rig = new Rig();
            var r = rig.Send("{\"id\":1,\"cmd\":");
            True(!Ok(r), "malformed json refused");
            True(r.ContainsKey("id") && r["id"] == null, "id is null when it cannot be read");

            r = rig.Send("[1,2,3]");
            True(!Ok(r), "non-object refused");
            r = rig.Send("{\"id\":\"x\",\"cmd\":\"hello\"}");
            True(!Ok(r), "non-integer id refused");
            r = rig.Send("{\"id\":5,\"cmd\":\"set_power\",\"args\":{\"spl\":\"20\",\"sppt\":25,\"fppt\":30}}");
            True(!Ok(r), "string where int expected refused");
            Equal(5, r["id"], "id echoed on bad arg type");
            r = rig.Send("{\"id\":6,\"cmd\":\"set_power\",\"args\":{\"spl\":20.5,\"sppt\":25,\"fppt\":30}}");
            True(!Ok(r), "fraction where int expected refused");
            Equal(0, rig.Acpi.Calls.Count, "no hardware call");
        }

        public static void MainLoop_OneReplyPerLine_UntilEof()
        {
            var rig = new Rig();
            var input = new StringReader("not json at all\n\n{\"id\":9,\"cmd\":\"hello\"}\n{\"id\":10,\"cmd\":\"nope\"}\n");
            var output = new StringWriter();
            HelperMain.Run(input, output, rig.Core);
            string[] lines = output.ToString().Split(new[] { "\n" }, StringSplitOptions.RemoveEmptyEntries);
            Equal(3, lines.Length, "one reply per non-empty line");
            True(lines[1].Contains("\"id\":9") && lines[1].Contains("\"ok\":true"), "hello answered after a bad line: " + lines[1]);
        }

        public static void MainLoop_HardwareException_BecomesErrorReply()
        {
            var rig = new Rig();
            rig.Acpi.ThrowOnWrite = true;
            var r = rig.Send("{\"id\":11,\"cmd\":\"set_mode\",\"args\":{\"mode\":\"turbo\"}}");
            True(!Ok(r), "exception reported as failure");
            True(Msg(r).Contains("write attempted"), "exception message passed on: " + Msg(r));
        }

        // ---------- power ----------

        public static void Power_ClampedAndOrdered()
        {
            int[] p = Validation.ClampPower(3, 60, 10);
            Equal("5,50,50", string.Join(",", p), "clamp to 5..50 then fppt >= sppt >= spl");
        }

        public static void Power_OrderFixed()
        {
            int[] p = Validation.ClampPower(30, 20, 25);
            Equal("30,30,30", string.Join(",", p), "later limits raised to the sustained limit");
        }

        public static void Power_Set_WritesInGHelperOrder()
        {
            var rig = new Rig();
            var r = rig.Send("{\"id\":1,\"cmd\":\"set_power\",\"args\":{\"spl\":30,\"sppt\":20,\"fppt\":25}}");
            True(Ok(r), "set_power ok: " + Msg(r));
            SeqEqual(new[] { "set 0x001200A3=30", "set 0x001200A0=30", "set 0x001200C1=30" }, rig.Acpi.Calls, "writes");
            var data = (Dictionary<string, object>)r["data"];
            Equal(30, data["sppt"], "applied sppt reported");
        }

        public static void Power_DeviceRefusal_StopsAndReports()
        {
            var rig = new Rig();
            rig.Acpi.SetResult = 0;
            var r = rig.Send("{\"id\":1,\"cmd\":\"set_power\",\"args\":{\"spl\":20,\"sppt\":25,\"fppt\":30}}");
            True(!Ok(r), "refusal reported");
            Equal(1, rig.Acpi.Calls.Count, "stops after the first refused write");
        }

        // ---------- mode ----------

        public static void Mode_Manual_AppliesPowerAfterBalanced()
        {
            var rig = new Rig();
            var r = rig.Send("{\"id\":1,\"cmd\":\"set_mode\",\"args\":{\"mode\":\"manual\",\"spl\":15,\"sppt\":20,\"fppt\":25}}");
            True(Ok(r), "manual ok: " + Msg(r));
            SeqEqual(new[] { "set 0x00120075=0", "set 0x001200A3=15", "set 0x001200A0=20", "set 0x001200C1=25" },
                rig.Acpi.Calls, "balanced first, then SPL, sPPT, fPPT");
        }

        // G-Helper ModeControl.SetPerformanceMode (commit in helper\GHELPER_COMMIT): mode write, 100 ms, AutoFans,
        // 1000 ms, AutoPower. The limits wait the same 1.1 s after the mode write; modes without limits do not wait.
        public static void Mode_Manual_WaitsLikeGHelperBeforePower()
        {
            var log = new List<string>();
            var acpi = new FakeAcpi();
            var core = new HelperCore(acpi, new FakeAdl(), new FakeServices(), true, ms =>
            {
                log.AddRange(acpi.Calls); acpi.Calls.Clear(); log.Add("sleep " + ms);
            });
            True(Ok((Dictionary<string, object>)Json.DeserializeObject(core.HandleLine(
                "{\"id\":1,\"cmd\":\"set_mode\",\"args\":{\"mode\":\"manual\",\"spl\":15,\"sppt\":20,\"fppt\":25}}"))), "manual ok");
            log.AddRange(acpi.Calls); acpi.Calls.Clear();
            SeqEqual(new[] { "set 0x00120075=0", "sleep 100", "sleep 1000", "set 0x001200A3=15", "set 0x001200A0=20", "set 0x001200C1=25" },
                log, "mode, 100 ms, 1000 ms, then the limits");
            Equal(1100, HelperCore.ModeToFansDelayMs + HelperCore.FansToPowerDelayMs, "1.1 s in total");

            log.Clear();
            True(Ok((Dictionary<string, object>)Json.DeserializeObject(core.HandleLine("{\"id\":2,\"cmd\":\"reset_fans\"}"))), "reset ok");
            log.AddRange(acpi.Calls); acpi.Calls.Clear();
            SeqEqual(new[] { "set 0x00120075=0", "sleep 100", "sleep 1000", "set 0x001200A3=15", "set 0x001200A0=20", "set 0x001200C1=25" },
                log, "reset_fans in Manual waits the same before the limits");

            var rig = new Rig();
            True(Ok(rig.Send("{\"id\":3,\"cmd\":\"set_mode\",\"args\":{\"mode\":\"turbo\"}}")), "turbo");
            True(Ok(rig.Send("{\"id\":4,\"cmd\":\"reset_fans\"}")), "reset turbo");
            Equal(0, rig.Sleeps.Count, "no limits, no wait");
        }

        public static void Mode_Values_MatchSpec()
        {
            var rig = new Rig();
            True(Ok(rig.Send("{\"id\":1,\"cmd\":\"set_mode\",\"args\":{\"mode\":\"silent\"}}")), "silent");
            True(Ok(rig.Send("{\"id\":2,\"cmd\":\"set_mode\",\"args\":{\"mode\":\"balanced\"}}")), "balanced");
            True(Ok(rig.Send("{\"id\":3,\"cmd\":\"set_mode\",\"args\":{\"mode\":\"turbo\"}}")), "turbo");
            SeqEqual(new[] { "set 0x00120075=2", "set 0x00120075=0", "set 0x00120075=1" }, rig.Acpi.Calls, "mode values");
        }

        public static void Mode_Invalid_Refused()
        {
            var rig = new Rig();
            True(!Ok(rig.Send("{\"id\":1,\"cmd\":\"set_mode\",\"args\":{\"mode\":\"ludicrous\"}}")), "unknown mode");
            True(!Ok(rig.Send("{\"id\":2,\"cmd\":\"set_mode\",\"args\":{}}")), "missing mode");
            True(!Ok(rig.Send("{\"id\":3,\"cmd\":\"set_mode\",\"args\":{\"mode\":\"turbo\",\"spl\":20}}")), "limits only with manual");
            True(!Ok(rig.Send("{\"id\":4,\"cmd\":\"set_mode\",\"args\":{\"mode\":\"manual\",\"spl\":20}}")), "manual needs all three");
            Equal(0, rig.Acpi.Calls.Count, "no hardware call");
        }

        // ---------- fps ----------

        public static void Fps_OnlyAllowedSteps()
        {
            var rig = new Rig();
            var r = rig.Send("{\"id\":1,\"cmd\":\"set_fps_limit\",\"args\":{\"fps\":33}}");
            True(!Ok(r), "33 refused");
            Equal(0, rig.Adl.Sets.Count, "nothing written for 33");
            True(Ok(rig.Send("{\"id\":2,\"cmd\":\"set_fps_limit\",\"args\":{\"fps\":0}}")), "0 accepted");
            True(Ok(rig.Send("{\"id\":3,\"cmd\":\"set_fps_limit\",\"args\":{\"fps\":60}}")), "60 accepted");
            Equal("0,60", string.Join(",", rig.Adl.Sets), "writes");
            foreach (int f in new[] { 30, 40, 45, 50, 60, 75, 90, 120 }) True(Validation.IsAllowedFps(f), f + " allowed");
            foreach (int f in new[] { -1, 1, 29, 144, 240 }) True(!Validation.IsAllowedFps(f), f + " refused");
        }

        public static void Fps_AdlMissing_ClearError()
        {
            var rig = new Rig();
            rig.Adl.Missing = true;
            var r = rig.Send("{\"id\":1,\"cmd\":\"get_fps_limit\"}");
            True(!Ok(r), "missing adapter is an error");
            True(Msg(r).Length > 0, "has a message");
        }

        // ---------- fan curve ----------

        public static void Curve_AcceptsGoodCurve()
        {
            string reason;
            True(Validation.CheckCurve(GoodTemps(), GoodSpeeds(), out reason), "good curve: " + reason);
            Equal("", reason, "no reason");
        }

        public static void Curve_RejectsWrongPointCount()
        {
            string reason;
            True(!Validation.CheckCurve(T(30, 40), T(0, 10), out reason), "2 points refused");
            True(!Validation.CheckCurve(null, GoodSpeeds(), out reason), "null refused");
            Equal("The fan curve needs 8 points.", reason, "reason");
        }

        public static void Curve_RejectsDecreasingFan()
        {
            string reason;
            int[] s = GoodSpeeds(); s[4] = 25;   // 30 then 25
            True(!Validation.CheckCurve(GoodTemps(), s, out reason), "decreasing fan refused");
            Equal("Point 5: fan speed can't be lower than point 4.", reason, "reason");
        }

        public static void Curve_RejectsFallingTemps_AndUnequalSpeedsAtOneTemp()
        {
            string reason;
            int[] t = GoodTemps(); t[1] = 25;
            True(!Validation.CheckCurve(t, GoodSpeeds(), out reason), "falling temps refused");
            Equal("Point 2: temperature can't be lower than point 1.", reason, "reason");
            t = GoodTemps(); t[3] = 50;          // equal to point 3, but 30 % against 20 %
            True(!Validation.CheckCurve(t, GoodSpeeds(), out reason), "equal temps with different speeds refused");
            Equal("Point 4: same temperature as point 3, so the fan speed must be the same.", reason, "reason");
            int[] sp = GoodSpeeds(); sp[3] = 20;
            True(Validation.CheckCurve(t, sp, out reason), "equal temps with equal speeds accepted: " + reason);
        }

        // The Ally's own curves (read-only probe on the device, 2026-10-23) repeat their last temperature.
        public static void Curve_AcceptsAllyFirmwareCurves()
        {
            string reason;
            True(Validation.CheckCurve(T(48, 54, 59, 62, 62, 62, 62, 62), T(1, 9, 18, 22, 22, 22, 22, 22), out reason), "balanced: " + reason);
            True(Validation.CheckCurve(T(48, 55, 58, 62, 66, 73, 73, 94), T(9, 26, 39, 48, 56, 65, 65, 78), out reason), "turbo: " + reason);
            True(Validation.CheckCurve(T(47, 49, 54, 54, 54, 54, 54, 54), T(1, 9, 13, 13, 13, 13, 13, 13), out reason), "silent: " + reason);
            // Quiet (-10, at least 0) and Cool (+15) as the plugin derives them
            True(Validation.CheckCurve(T(48, 55, 58, 62, 66, 73, 73, 94), T(0, 16, 29, 38, 46, 55, 55, 68), out reason), "turbo quiet: " + reason);
            True(Validation.CheckCurve(T(48, 55, 58, 62, 66, 73, 73, 94), T(24, 41, 54, 63, 71, 80, 80, 93), out reason), "turbo cool: " + reason);
        }

        // G-Helper app/Fans.cs (commit in helper\GHELPER_COMMIT): tempMin = 20, tempMax = 110, fansMax = 100
        // (lines 31-34), and the editor raises every point to dymin = (temp - 70) * 1.2 (lines 1516-1517 for
        // keyboard edits, 1614-1616 for mouse drags). G-Helper has no other per-point speed minimum.
        public static void Curve_RejectsBelowGHelperMinimums()
        {
            string reason;
            int[] t = T(30, 40, 50, 60, 70, 75, 80, 100);
            int[] s = T(0, 0, 0, 0, 0, 6, 12, 35);   // 100 C needs (100-70)*1.2 = 36 %
            True(!Validation.CheckCurve(t, s, out reason), "35 % at 100 C refused");
            Equal("Point 8: at 100 °C the fan must be at least 36 %.", reason, "reason");
            s[7] = 36;
            True(Validation.CheckCurve(t, s, out reason), "36 % at 100 C accepted: " + reason);

            // fractional minimum: 91 C -> 25.2 %, so 25 is below it and 26 is enough
            t = T(30, 40, 50, 60, 70, 80, 90, 91);
            s = T(0, 0, 0, 0, 0, 12, 24, 25);
            True(!Validation.CheckCurve(t, s, out reason), "25 % at 91 C refused");
            Equal("Point 8: at 91 °C the fan must be at least 26 %.", reason, "reason rounds up");
            s[7] = 26;
            True(Validation.CheckCurve(t, s, out reason), "26 % at 91 C accepted: " + reason);

            // at or below 70 C any speed from 0 is allowed (G-Helper's minimum is negative there)
            True(Validation.CheckCurve(T(20, 30, 40, 50, 60, 65, 70, 75), T(0, 0, 0, 0, 0, 0, 0, 6), out reason), "zeros up to 70 C: " + reason);

            // range limits from the editor axes
            True(!Validation.CheckCurve(T(19, 30, 40, 50, 60, 70, 80, 90), GoodSpeeds(), out reason), "below 20 C refused");
            Equal("Point 1: temperature must be between 20 and 110 °C.", reason, "temp range reason");
            True(!Validation.CheckCurve(T(30, 40, 50, 60, 70, 80, 90, 111), T(0, 10, 20, 30, 40, 50, 60, 100), out reason), "above 110 C refused");
            True(!Validation.CheckCurve(GoodTemps(), T(0, 10, 20, 30, 40, 50, 60, 101), out reason), "above 100 % refused");
            Equal("Point 8: fan speed must be between 0 and 100 %.", reason, "speed range reason");
            True(!Validation.CheckCurve(GoodTemps(), T(-1, 10, 20, 30, 40, 50, 60, 70), out reason), "negative speed refused");
        }

        public static void Curve_Set_WritesGHelperByteLayout()
        {
            var rig = new Rig();
            var r = rig.Send("{\"id\":1,\"cmd\":\"set_fans\",\"args\":{\"temps\":[30,40,50,60,70,80,90,100],\"speeds\":[0,10,20,30,40,50,60,70]}}");
            True(Ok(r), "set_fans ok: " + Msg(r));
            SeqEqual(new[] { "setcurve 0 1E-28-32-3C-46-50-5A-64-00-0A-14-1E-28-32-3C-46" }, rig.Acpi.Calls, "temps bytes 0-7, speeds 8-15");
        }

        public static void Curve_Set_InvalidNeverWrites()
        {
            var rig = new Rig();
            var r = rig.Send("{\"id\":1,\"cmd\":\"set_fans\",\"args\":{\"temps\":[30,40,50,60,70,75,80,100],\"speeds\":[0,0,0,0,0,6,12,30]}}");
            True(!Ok(r), "refused");
            Equal("Point 8: at 100 °C the fan must be at least 36 %.", Msg(r), "reason passed on");
            Equal(0, rig.Acpi.Calls.Count, "no hardware call");
        }

        public static void Curve_Get_SplitsBytes()
        {
            var rig = new Rig();
            rig.Acpi.Curves[0] = new byte[] { 30, 40, 50, 60, 70, 80, 90, 100, 0, 10, 20, 30, 40, 50, 60, 70 };
            var r = rig.Send("{\"id\":1,\"cmd\":\"get_fans\"}");
            True(Ok(r), "get_fans ok: " + Msg(r));
            var data = (Dictionary<string, object>)r["data"];
            Equal("30,40,50,60,70,80,90,100", string.Join(",", (object[])data["temps"]), "temps");
            Equal("0,10,20,30,40,50,60,70", string.Join(",", (object[])data["speeds"]), "speeds");

            rig.Acpi.Curves.Clear();
            True(!Ok(rig.Send("{\"id\":2,\"cmd\":\"get_fans\"}")), "all-zero curve is invalid (G-Helper IsInvalidCurve)");
        }

        // ---------- sensors (Task 3) ----------

        // G-Helper @ c09017453d9ed0c52261b67a48d02bd55232e6ac:
        //   CPU temperature: app/HardwareControl.cs 455 `cpuTemp = Program.acpi.DeviceGet(AsusACPI.Temp_CPU);`, used as °C;
        //     457 `if (cpuTemp < 0)` = not reported by ACPI (G-Helper then tries a Windows thermal zone, not ported).
        //   Fan: app/AsusACPI.cs 559-561 GetFan `int fan = raw & 0xFFFF; if (fan > 120 || (fan == 0 && raw < 0)) fan = -1;`
        //     (raw = DeviceGet(CPU_Fan), already minus 65536, AsusACPI.cs 486),
        //     RPM = value * 100: app/HardwareControl.cs 859 `cpuFanRPM = Program.acpi.GetFan(AsusFan.CPU) * 100;`
        //     and app/Fan/FanSensorControl.cs 127/132 (value < 0 -> null; `(value * 100) + "RPM"`).
        public static void Sensors_ConvertLikeGHelper()
        {
            Equal((int?)45, GHelperSensors.CpuTempC(45), "temp is DeviceGet value in °C");
            Equal((int?)0, GHelperSensors.CpuTempC(0), "0 °C is a reading");
            Equal((int?)null, GHelperSensors.CpuTempC(-1), "negative = not reported");
            Equal((int?)null, GHelperSensors.CpuTempC(-65536), "empty DSTS reply = not reported");

            Equal((int?)3500, GHelperSensors.FanRpm(35), "35 -> 3500 RPM");
            Equal((int?)0, GHelperSensors.FanRpm(0), "stopped fan");
            Equal((int?)12000, GHelperSensors.FanRpm(120), "120 is still valid");
            Equal((int?)null, GHelperSensors.FanRpm(121), "above 120 = invalid");
            Equal((int?)3500, GHelperSensors.FanRpm(0x00010023), "only the low 16 bits count");
            Equal((int?)null, GHelperSensors.FanRpm(-65536), "zero with a negative raw = not reported");

            var rig = new Rig();
            rig.Acpi.Values[DeviceIds.TempCpu] = 61;
            rig.Acpi.Values[DeviceIds.CpuFan] = 42;
            var r = rig.Send("{\"id\":1,\"cmd\":\"sensors\"}");
            True(Ok(r), "sensors ok");
            var d = (Dictionary<string, object>)r["data"];
            Equal(61, d["cpuTempC"], "sensors cpuTempC");
            Equal(4200, d["fanRpm"], "sensors fanRpm");
            Equal(0, ((object[])d["errors"]).Length, "no errors");

            r = rig.Send("{\"id\":2,\"cmd\":\"status\"}");
            d = (Dictionary<string, object>)r["data"];
            Equal(61, d["cpuTempC"], "status cpuTempC");
            Equal(4200, d["fanRpm"], "status fanRpm");
        }

        public static void Sensors_NotReported_NullWithError()
        {
            var rig = new Rig();
            rig.Acpi.Values[DeviceIds.TempCpu] = -65536;
            rig.Acpi.Values[DeviceIds.CpuFan] = -65536;
            var r = rig.Send("{\"id\":1,\"cmd\":\"sensors\"}");
            True(Ok(r), "sensors ok");
            var d = (Dictionary<string, object>)r["data"];
            True(d["cpuTempC"] == null, "temp null");
            True(d["fanRpm"] == null, "fan null");
            Equal(2, ((object[])d["errors"]).Length, "one error per missing sensor");
        }

        // G-Helper app/AsusACPI.cs 629-635: "because it's asus, and modes are swapped here".
        public static void FanCurve_ModeSwapLikeGHelper()
        {
            Equal(0u, Overdrive.Helper.GHelper.AsusACPI.FanCurveMode(0), "balanced");
            Equal(2u, Overdrive.Helper.GHelper.AsusACPI.FanCurveMode(1), "turbo -> 2");
            Equal(1u, Overdrive.Helper.GHelper.AsusACPI.FanCurveMode(2), "silent -> 1");
            Equal(0u, Overdrive.Helper.GHelper.AsusACPI.FanCurveMode(7), "anything else -> 0");
        }

        // ---------- status / sensors shapes, non-Ally refusal, reset_fans (Task 2 review) ----------

        public static void Status_Shape()
        {
            var rig = new Rig();
            rig.Acpi.Values[DeviceIds.PerformanceMode] = 0;
            rig.Acpi.Values[DeviceIds.Spl] = 15;
            rig.Acpi.Values[DeviceIds.Sppt] = 20;
            rig.Acpi.Values[DeviceIds.Fppt] = 25;
            rig.Acpi.Values[DeviceIds.TempCpu] = 40;
            rig.Acpi.Values[DeviceIds.CpuFan] = 20;
            rig.Adl.Limit = 60;
            var r = rig.Send("{\"id\":1,\"cmd\":\"status\"}");
            True(Ok(r), "status ok");
            var d = (Dictionary<string, object>)r["data"];
            SeqEqual(new[] { "modeRaw", "mode", "spl", "sppt", "fppt", "lastMode", "fpsLimit", "cpuTempC", "fanRpm", "errors" },
                new List<string>(d.Keys), "status keys");
            Equal(0, d["modeRaw"], "modeRaw");
            Equal("balanced", d["mode"], "mode name");
            Equal(15, d["spl"], "spl");
            Equal(20, d["sppt"], "sppt");
            Equal(25, d["fppt"], "fppt");
            True(d["lastMode"] == null, "no mode set yet");
            Equal(60, d["fpsLimit"], "fps");
            Equal(40, d["cpuTempC"], "temp");
            Equal(2000, d["fanRpm"], "fan");
            Equal(0, ((object[])d["errors"]).Length, "no errors");
            foreach (string c in rig.Acpi.Calls) True(c.StartsWith("get "), "status only reads: " + c);
        }

        // Probe on the Ally: mode 7 (an Armoury Crate mode) and power limits 0 while the firmware's limits apply.
        public static void Status_OtherModeAndZeroPower_ReportedNotErrors()
        {
            var rig = new Rig();
            rig.Acpi.Values[DeviceIds.PerformanceMode] = 7;
            rig.Acpi.Values[DeviceIds.Spl] = 0;
            rig.Acpi.Values[DeviceIds.Sppt] = 0;
            rig.Acpi.Values[DeviceIds.Fppt] = 0;
            rig.Acpi.Values[DeviceIds.TempCpu] = 60;
            rig.Acpi.Values[DeviceIds.CpuFan] = 42;
            var d = (Dictionary<string, object>)rig.Send("{\"id\":1,\"cmd\":\"status\"}")["data"];
            Equal(7, d["modeRaw"], "raw kept");
            Equal("other", d["mode"], "mode other");
            True(d["spl"] == null && d["sppt"] == null && d["fppt"] == null, "0 = not reported");
            Equal(0, ((object[])d["errors"]).Length, "no errors");
            foreach (string c in rig.Acpi.Calls) True(c.StartsWith("get "), "status only reads: " + c);
            Equal("silent", HelperCore.ModeName(2), "2 silent");
            Equal("turbo", HelperCore.ModeName(1), "1 turbo");
            True(HelperCore.ModeName(null) == null, "null stays null");

            var acpi = new FakeAcpi();
            acpi.ThrowOnWrite = true;
            acpi.Values[DeviceIds.PerformanceMode] = 7;
            acpi.Values[DeviceIds.Spl] = 0;
            acpi.Values[DeviceIds.Sppt] = 0;
            acpi.Values[DeviceIds.Fppt] = 0;
            acpi.Values[DeviceIds.TempCpu] = 60;
            acpi.Values[DeviceIds.CpuFan] = 42;
            foreach (int m in new[] { 0, 1, 2 })
                acpi.Curves[m] = new byte[] { 48, 54, 59, 62, 62, 62, 62, 62, 1, 9, 18, 22, 22, 22, 22, 22 };
            var p = (Dictionary<string, object>)Json.DeserializeObject(Probe.Run(acpi, "RC71L"));
            Equal(7, p["mode"], "probe raw mode");
            Equal("other", p["modeName"], "probe mode name");
            True(p["spl"] == null && p["sppt"] == null && p["fppt"] == null, "probe 0 = not reported");
            Equal(0, ((object[])p["errors"]).Length, "probe has no errors");
        }

        public static void Status_ReadFailures_InErrors()
        {
            var rig = new Rig();
            rig.Acpi.ThrowOnRead = true;
            rig.Adl.Missing = true;
            var r = rig.Send("{\"id\":1,\"cmd\":\"status\"}");
            True(Ok(r), "status still answers");
            var d = (Dictionary<string, object>)r["data"];
            True(d["spl"] == null && d["cpuTempC"] == null && d["fpsLimit"] == null, "nulls");
            Equal(7, ((object[])d["errors"]).Length, "mode, spl, sppt, fppt, fps, cpuTemp, cpuFan");
        }

        public static void Sensors_Shape()
        {
            var rig = new Rig();
            rig.Acpi.Values[DeviceIds.TempCpu] = 50;
            rig.Acpi.Values[DeviceIds.CpuFan] = 30;
            var d = (Dictionary<string, object>)rig.Send("{\"id\":1,\"cmd\":\"sensors\"}")["data"];
            SeqEqual(new[] { "cpuTempC", "fanRpm", "errors" }, new List<string>(d.Keys), "sensors keys");
        }

        public static void NotAlly_RefusesEveryWrite()
        {
            var rig = new Rig(false);
            string[] writes =
            {
                "{\"id\":1,\"cmd\":\"set_mode\",\"args\":{\"mode\":\"turbo\"}}",
                "{\"id\":2,\"cmd\":\"set_power\",\"args\":{\"spl\":20,\"sppt\":25,\"fppt\":30}}",
                "{\"id\":3,\"cmd\":\"set_fans\",\"args\":{\"temps\":[30,40,50,60,70,80,90,100],\"speeds\":[0,10,20,30,40,50,60,70]}}",
                "{\"id\":4,\"cmd\":\"reset_fans\"}",
                "{\"id\":5,\"cmd\":\"set_fps_limit\",\"args\":{\"fps\":60}}",
                "{\"id\":6,\"cmd\":\"armoury_stop\"}",
                "{\"id\":7,\"cmd\":\"armoury_restore\",\"args\":{\"recorded\":{},\"running\":[]}}",
            };
            foreach (string w in writes)
            {
                var r = rig.Send(w);
                True(!Ok(r), "refused: " + w);
                Equal(HelperCore.NotAllyMessage, Msg(r), "message for " + w);
            }
            Equal("Overdrive supports the ROG Ally family only", HelperCore.NotAllyMessage, "message text");
            Equal(0, rig.Acpi.Calls.Count, "no ACPI call");
            Equal(0, rig.Adl.Sets.Count, "no ADL call");
            Equal(0, rig.Services.Calls.Count, "no service call");

            True(Ok(rig.Send("{\"id\":8,\"cmd\":\"hello\"}")), "hello still works");
            True(Ok(rig.Send("{\"id\":9,\"cmd\":\"sensors\"}")), "reads still work");
        }

        public static void ResetFans_NeverSwitchesMode()
        {
            var rig = new Rig();
            var r = rig.Send("{\"id\":1,\"cmd\":\"reset_fans\"}");
            True(!Ok(r), "refused before any mode is set");
            r = rig.Send("{\"id\":2,\"cmd\":\"reset_fans\",\"args\":{\"mode\":\"turbo\"}}");
            True(!Ok(r), "refused: would switch to turbo");
            Equal(0, rig.Acpi.Calls.Count, "no hardware call");

            True(Ok(rig.Send("{\"id\":3,\"cmd\":\"set_mode\",\"args\":{\"mode\":\"silent\"}}")), "set silent");
            rig.Acpi.Calls.Clear();
            r = rig.Send("{\"id\":4,\"cmd\":\"reset_fans\",\"args\":{\"mode\":\"turbo\"}}");
            True(!Ok(r), "other mode refused");
            True(Msg(r).Contains("silent"), "names the current mode: " + Msg(r));
            Equal(0, rig.Acpi.Calls.Count, "no hardware call");

            r = rig.Send("{\"id\":5,\"cmd\":\"reset_fans\"}");
            True(Ok(r), "reset current mode: " + Msg(r));
            SeqEqual(new[] { "set 0x00120075=2" }, rig.Acpi.Calls, "rewrites silent only");
            Equal("silent", ((Dictionary<string, object>)r["data"])["mode"], "mode in reply");
            True(Ok(rig.Send("{\"id\":6,\"cmd\":\"reset_fans\",\"args\":{\"mode\":\"silent\"}}")), "same mode explicitly is fine");
        }

        public static void ResetFans_Manual_ReappliesPower()
        {
            var rig = new Rig();
            True(Ok(rig.Send("{\"id\":1,\"cmd\":\"set_mode\",\"args\":{\"mode\":\"manual\",\"spl\":20,\"sppt\":25,\"fppt\":30}}")), "manual");
            rig.Acpi.Calls.Clear();
            var r = rig.Send("{\"id\":2,\"cmd\":\"reset_fans\"}");
            True(Ok(r), "reset ok: " + Msg(r));
            SeqEqual(new[] { "set 0x00120075=0", "set 0x001200A3=20", "set 0x001200A0=25", "set 0x001200C1=30" },
                rig.Acpi.Calls, "balanced base then the manual limits again");
            Equal(true, ((Dictionary<string, object>)r["data"])["powerReapplied"], "powerReapplied");
        }

        // ---------- probe (Task 3) ----------

        public static void Probe_UsesOnlyReads()
        {
            var acpi = new FakeAcpi();
            acpi.ThrowOnWrite = true;
            acpi.Values[DeviceIds.PerformanceMode] = DeviceIds.ModeTurbo;
            acpi.Values[DeviceIds.Spl] = 25;
            acpi.Values[DeviceIds.Sppt] = 30;
            acpi.Values[DeviceIds.Fppt] = 35;
            acpi.Values[DeviceIds.TempCpu] = 58;
            acpi.Values[DeviceIds.CpuFan] = 31;
            acpi.Curves[DeviceIds.ModeTurbo] = new byte[] { 30, 40, 50, 60, 70, 80, 90, 100, 10, 20, 30, 40, 50, 60, 70, 80 };

            string json = Probe.Run(acpi, "ROG Xbox Ally X RC73XA_RC73XA");
            True(json.IndexOf('\n') < 0, "one line");
            var p = (Dictionary<string, object>)Json.DeserializeObject(json);
            SeqEqual(new[] { "product", "mode", "modeName", "spl", "sppt", "fppt", "cpuTempC", "fanRpm", "fanCurve", "fanCurves", "errors" },
                new List<string>(p.Keys), "keys in brief order, fanCurves added by ruling P7");
            Equal("ROG Xbox Ally X RC73XA_RC73XA", p["product"], "product");
            Equal(1, p["mode"], "mode raw");
            Equal(25, p["spl"], "spl");
            Equal(30, p["sppt"], "sppt");
            Equal(35, p["fppt"], "fppt");
            Equal(58, p["cpuTempC"], "temp converted");
            Equal(3100, p["fanRpm"], "fan converted");
            var curve = (Dictionary<string, object>)p["fanCurve"];
            Equal("30,40,50,60,70,80,90,100", string.Join(",", (object[])curve["temps"]), "curve temps");
            Equal("10,20,30,40,50,60,70,80", string.Join(",", (object[])curve["speeds"]), "curve speeds");
            // fanCurves: balanced and silent are not in the fake, so they are null with an error each
            var curves = (Dictionary<string, object>)p["fanCurves"];
            SeqEqual(new[] { "balanced", "turbo", "silent" }, new List<string>(curves.Keys), "fanCurves keys");
            True(curves["balanced"] == null && curves["silent"] == null, "missing curves are null");
            Equal("10,20,30,40,50,60,70,80", string.Join(",", (object[])((Dictionary<string, object>)curves["turbo"])["speeds"]), "turbo speeds");
            Equal(2, ((object[])p["errors"]).Length, "one error per missing curve: " + json);
            foreach (string c in acpi.Calls)
                True(c.StartsWith("get ") || c.StartsWith("getcurve "), "only reads: " + c);
            True(acpi.Calls.Contains("getcurve 0 1"), "curve of the current mode, CPU fan");
        }

        // Ruling P6/P7: one probe run records the default curve of every mode (reads only).
        public static void Probe_ReadsCurvesForAllModes()
        {
            var acpi = new FakeAcpi();
            acpi.ThrowOnWrite = true;
            acpi.Values[DeviceIds.PerformanceMode] = DeviceIds.ModeBalanced;
            acpi.Values[DeviceIds.TempCpu] = 50;
            acpi.Values[DeviceIds.CpuFan] = 30;
            acpi.Curves[DeviceIds.ModeBalanced] = new byte[] { 30, 40, 50, 60, 70, 80, 90, 100, 1, 2, 3, 4, 5, 6, 7, 8 };
            acpi.Curves[DeviceIds.ModeTurbo] = new byte[] { 30, 40, 50, 60, 70, 80, 90, 100, 11, 12, 13, 14, 15, 16, 17, 18 };
            acpi.Curves[DeviceIds.ModeSilent] = new byte[] { 30, 40, 50, 60, 70, 80, 90, 100, 21, 22, 23, 24, 25, 26, 27, 28 };
            var p = (Dictionary<string, object>)Json.DeserializeObject(Probe.Run(acpi, "RC73XA"));
            var curves = (Dictionary<string, object>)p["fanCurves"];
            Equal("1,2,3,4,5,6,7,8", string.Join(",", (object[])((Dictionary<string, object>)curves["balanced"])["speeds"]), "balanced");
            Equal("11,12,13,14,15,16,17,18", string.Join(",", (object[])((Dictionary<string, object>)curves["turbo"])["speeds"]), "turbo");
            Equal("21,22,23,24,25,26,27,28", string.Join(",", (object[])((Dictionary<string, object>)curves["silent"])["speeds"]), "silent");
            Equal("30,40,50,60,70,80,90,100", string.Join(",", (object[])((Dictionary<string, object>)curves["silent"])["temps"]), "silent temps");
            True(acpi.Calls.Contains("getcurve 0 0") && acpi.Calls.Contains("getcurve 0 1") && acpi.Calls.Contains("getcurve 0 2"), "each mode read");
            Equal(0, ((object[])p["errors"]).Length, "no errors");
        }

        // ---------- ruling P7 hardening (Task 4) ----------

        // IOCTL failure value: G-Helper DeviceGet returns raw - 65536 (AsusACPI.cs 486), so an empty DSTS reply is -65536.
        public static void Status_NotReportedSettings_NullWithError()
        {
            var rig = new Rig();
            foreach (uint id in new[] { DeviceIds.PerformanceMode, DeviceIds.Spl, DeviceIds.Sppt, DeviceIds.Fppt })
                rig.Acpi.Values[id] = -65536;
            rig.Acpi.Values[DeviceIds.TempCpu] = 40;
            rig.Acpi.Values[DeviceIds.CpuFan] = 20;
            var d = (Dictionary<string, object>)rig.Send("{\"id\":1,\"cmd\":\"status\"}")["data"];
            True(d["modeRaw"] == null && d["spl"] == null && d["sppt"] == null && d["fppt"] == null, "nulls");
            var errors = (object[])d["errors"];
            Equal(4, errors.Length, "one error each");
            Equal("mode: not reported by the device (raw -65536).", errors[0], "error text");

            var acpi = new FakeAcpi();
            foreach (uint id in new[] { DeviceIds.PerformanceMode, DeviceIds.Spl, DeviceIds.Sppt, DeviceIds.Fppt })
                acpi.Values[id] = -65536;
            var p = (Dictionary<string, object>)Json.DeserializeObject(Probe.Run(acpi, "X"));
            True(p["mode"] == null && p["spl"] == null && p["sppt"] == null && p["fppt"] == null, "probe nulls");
            var pe = new List<string>();
            foreach (object o in (object[])p["errors"]) pe.Add((string)o);
            True(pe.Contains("spl: not reported by the device (raw -65536)."), "probe error: " + string.Join(" | ", pe));
            True(acpi.Calls.Contains("getcurve 0 0"), "unknown mode -> balanced curve");
        }

        public static void NotAlly_RefusesAllButReadAllowList()
        {
            SeqEqual(new[] { "hello", "status", "sensors", "get_fans", "get_fps_limit", "armoury_state" },
                new List<string>(HelperCore.ReadCommands), "read allow-list");
            var rig = new Rig(false);
            int n = 0, refused = 0;
            foreach (string cmd in HelperCore.CommandNames)
            {
                var r = rig.Send("{\"id\":" + (++n) + ",\"cmd\":\"" + cmd + "\"}");
                bool read = Array.IndexOf(HelperCore.ReadCommands, cmd) >= 0;
                if (read) True(Msg(r) != HelperCore.NotAllyMessage, cmd + " is a read and must not be refused as non-Ally");
                else { True(!Ok(r) && Msg(r) == HelperCore.NotAllyMessage, cmd + " refused: " + Msg(r)); refused++; }
            }
            Equal(7, refused, "every non-read command refused");
            foreach (string c in rig.Acpi.Calls) True(c.StartsWith("get"), "only reads reached ACPI: " + c);
            Equal(0, rig.Adl.Sets.Count, "no ADL write");
            Equal(0, rig.Services.Changes().Count, "no service change");
        }

        // Spec: never touch the battery charge limit (0x00120057). The real ACPI layer only writes allow-listed ids.
        public static void Acpi_DeviceSet_AllowListBlocksBatteryLimit()
        {
            SeqEqual(new[] { "0x00120075", "0x001200A3", "0x001200A0", "0x001200C1", "0x00110024" },
                new List<uint>(AcpiHardware.WritableIds).ConvertAll(i => "0x" + i.ToString("X8")), "writable ids");
            True(!AcpiHardware.IsWritable(0x00120057), "battery limit id not writable");

            // No device is opened: the null device makes any call that passes the allow-list fail with "Can't open".
            var hw = new AcpiHardware((Overdrive.Helper.GHelper.AsusACPI)null);
            string msg = null;
            try { hw.DeviceSet(0x00120057, 80); } catch (InvalidOperationException ex) { msg = ex.Message; }
            True(msg != null && msg.Contains("0x00120057") && msg.StartsWith("Refused"), "battery id refused before the device: " + msg);
            msg = null;
            try { hw.DeviceSet(0x00110013, 1); } catch (InvalidOperationException ex) { msg = ex.Message; }
            True(msg != null && msg.StartsWith("Refused"), "any other id refused too: " + msg);
            msg = null;
            try { hw.DeviceSet(DeviceIds.PerformanceMode, 0); } catch (InvalidOperationException ex) { msg = ex.Message; }
            True(msg != null && msg.StartsWith("Can't open"), "allowed id passes the list, then needs the device: " + msg);
            hw.Dispose();
        }

        // ---------- FPS limit (Task 4) ----------

        public static void Fps_Set_Then_Get_RoundTrips()
        {
            var rig = new Rig();
            True(Ok(rig.Send("{\"id\":1,\"cmd\":\"set_fps_limit\",\"args\":{\"fps\":45}}")), "set 45");
            var r = rig.Send("{\"id\":2,\"cmd\":\"get_fps_limit\"}");
            True(Ok(r), "get ok");
            Equal(45, ((Dictionary<string, object>)r["data"])["fps"], "round trip through the protocol");

            // Through the real IAdl wrapper with a fake control in place of the ported AmdGpuControl.
            var control = new FakeFpsControl();
            int created = 0;
            var adl = new AdlHardware(() => { created++; return control; });
            Equal(1, adl.SetFpsLimit(60), "set ok");
            Equal(60, control.Ac, "AC value");
            Equal(60, control.Dc, "DC value (G-Helper sets both)");
            Equal(60, adl.GetFpsLimit(), "get returns what was set");
            Equal(1, adl.SetFpsLimit(0), "0 (off) accepted");
            Equal(240, control.Ac, "off is written as G-Helper's 240");
            Equal(0, adl.GetFpsLimit(), "off reads back 0");
            True(adl.Unavailable == null, "available");
            Equal(1, created, "ADL set up once and reused");
            control.Refuse = true;
            Equal(0, adl.SetFpsLimit(30), "driver refusal passed on");
            adl.Dispose();
            True(control.Disposed, "ADL context released on dispose");

            var core = new HelperCore(new FakeAcpi(), new AdlHardware(() => new FakeFpsControl()), new FakeServices(), true);
            True(core.HandleLine("{\"id\":3,\"cmd\":\"set_fps_limit\",\"args\":{\"fps\":90}}").Contains("\"ok\":true"), "protocol set via AdlHardware");
            True(core.HandleLine("{\"id\":4,\"cmd\":\"get_fps_limit\"}").Contains("\"fps\":90"), "protocol get via AdlHardware");
        }

        // G-Helper app/Ally/AllyControl.cs ToggleFPSLimit: OFF is written as 240 (`case 120: fpsLimit = 240`) and
        // any value outside 1..120 is shown as "OFF" (`(fpsLimit > 0 && fpsLimit <= 120) ? fpsLimit : "OFF"`).
        public static void Fps_OffFollowsGHelper()
        {
            var control = new FakeFpsControl();
            var adl = new AdlHardware(() => control);
            Equal(1, adl.SetFpsLimit(0), "off accepted");
            Equal(240, control.Ac, "AC off written as 240");
            Equal(240, control.Dc, "DC off written as 240");
            foreach (int f in new[] { 30, 40, 45, 50, 60, 75, 90, 120 })
            {
                adl.SetFpsLimit(f);
                Equal(f, control.Ac, f + " written as is");
            }
            control.Ac = 240; Equal(0, adl.GetFpsLimit(), "read 240 -> 0 (off)");
            control.Ac = 0; Equal(0, adl.GetFpsLimit(), "read 0 -> 0");
            control.Ac = 60; Equal(60, adl.GetFpsLimit(), "read 60 -> 60");
            control.Ac = 1; Equal(1, adl.GetFpsLimit(), "read 1 -> 1 (inside 1..120)");
            control.Ac = 120; Equal(120, adl.GetFpsLimit(), "read 120 -> 120");
            control.Ac = 121; Equal(0, adl.GetFpsLimit(), "read 121 -> 0");
            control.Ac = 144; Equal(0, adl.GetFpsLimit(), "read 144 -> 0");
            Equal(240, AdlHardware.ToDriver(0), "ToDriver(0)");
            Equal(0, AdlHardware.FromDriver(-1), "FromDriver(-1) -> 0");

            // Through the protocol: set 0 writes 240, get after an external 240 reports 0.
            control = new FakeFpsControl();
            var core = new HelperCore(new FakeAcpi(), new AdlHardware(() => control), new FakeServices(), true);
            True(core.HandleLine("{\"id\":1,\"cmd\":\"set_fps_limit\",\"args\":{\"fps\":0}}").Contains("\"ok\":true"), "set 0 ok");
            Equal(240, control.Ac, "protocol off -> 240");
            True(core.HandleLine("{\"id\":2,\"cmd\":\"get_fps_limit\"}").Contains("\"fps\":0"), "get reports 0");
            True(core.HandleLine("{\"id\":3,\"cmd\":\"status\"}").Contains("\"fpsLimit\":0"), "status reports 0");
        }

        public static void Adl_Missing_ReturnsClearError()
        {
            var adl = new AdlHardware(() => { throw new DllNotFoundException("Unable to load DLL 'atiadlxx.dll'"); });
            Equal(-1, adl.GetFpsLimit(), "get unavailable");
            Equal(-1, adl.SetFpsLimit(60), "set unavailable");
            True(adl.Unavailable != null && adl.Unavailable.Contains("atiadlxx.dll"), "reason names the library: " + adl.Unavailable);

            var rig = new Rig();
            var core = new HelperCore(rig.Acpi, adl, rig.Services, true);
            var r = (Dictionary<string, object>)Json.DeserializeObject(core.HandleLine("{\"id\":1,\"cmd\":\"get_fps_limit\"}"));
            True(!Ok(r), "get refused");
            Equal("AMD frame rate control is unavailable: the AMD display library (atiadlxx.dll) is not installed.", Msg(r), "message");
            r = (Dictionary<string, object>)Json.DeserializeObject(core.HandleLine("{\"id\":2,\"cmd\":\"set_fps_limit\",\"args\":{\"fps\":60}}"));
            True(!Ok(r) && Msg(r).Contains("atiadlxx.dll"), "set refused with the reason: " + Msg(r));
            r = (Dictionary<string, object>)Json.DeserializeObject(core.HandleLine("{\"id\":3,\"cmd\":\"status\"}"));
            var errors = new List<string>();
            foreach (object o in (object[])((Dictionary<string, object>)r["data"])["errors"]) errors.Add((string)o);
            True(errors.Contains("fps: AMD frame rate control is unavailable: the AMD display library (atiadlxx.dll) is not installed."), "status error: " + string.Join(" | ", errors));

            // A control that set up but found no AMD integrated GPU: disposed, reason passed on.
            var control = new FakeFpsControl();
            control.ProblemText = "no AMD integrated GPU was found";
            adl = new AdlHardware(() => control);
            Equal(-1, adl.GetFpsLimit(), "unavailable");
            Equal("no AMD integrated GPU was found", adl.Unavailable, "reason");
            True(control.Disposed, "half-built control released");

            rig.Adl.Missing = true;
            r = rig.Send("{\"id\":4,\"cmd\":\"get_fps_limit\"}");
            Equal("AMD frame rate control is unavailable: fake ADL missing.", Msg(r), "fake reason passed on");
        }

        // ---------- Armoury Crate services (Task 4) ----------

        // G-Helper app/Helpers/AsusService.cs lines 10-25 (services) and 29-33 (servicesAC), commit in helper\GHELPER_COMMIT;
        // "AsusPTPService" is commented out upstream (line 27) and stays out.
        public static void Armoury_ListsMatchGHelper()
        {
            SeqEqual(new[] { "ArmouryCrateControlInterface", "ArmouryCrateProArtService", "AsHidService", "ASUSOptimization",
                "AsusAppService", "ASUSLinkNear", "ASUSLinkRemote", "ASUSSoftwareManager", "ASUSLiveUpdateAgent", "ASUSSwitch",
                "ASUSSystemAnalysis", "ASUSSystemDiagnosis", "ASUSXGMobileService", "AsusCertService" },
                ArmouryServices.Services, "services");
            SeqEqual(new[] { "ArmouryCrateSEService", "ArmouryCrateService", "LightingService" }, ArmouryServices.ServicesAC, "servicesAC");
            Equal(17, ArmouryServices.All.Count, "all");
            True(!ArmouryServices.IsListed("AsusPTPService"), "AsusPTPService not listed");
            True(!ArmouryServices.IsListed("armourycrateseservice"), "names are exact");
        }

        // State before Armoury is replaced: four listed services running, one stopped, one unrelated service.
        private static Rig ArmouryRig()
        {
            var rig = new Rig();
            var s = rig.Services;
            s.Types["ArmouryCrateControlInterface"] = "Automatic"; s.RunningNames.Add("ArmouryCrateControlInterface");
            s.Types["ASUSOptimization"] = "Automatic";              // installed but stopped
            s.Types["AsusAppService"] = "Automatic"; s.RunningNames.Add("AsusAppService");
            s.Types["ArmouryCrateSEService"] = "AutomaticDelayedStart"; s.RunningNames.Add("ArmouryCrateSEService");
            s.Types["LightingService"] = "Manual"; s.RunningNames.Add("LightingService");
            s.Types["Spooler"] = "Automatic"; s.RunningNames.Add("Spooler");
            return rig;
        }

        private static string Snapshot(FakeServices s)
        {
            var names = new List<string>(s.Types.Keys);
            names.Sort(StringComparer.Ordinal);
            var parts = new List<string>();
            foreach (string n in names) parts.Add(n + "=" + s.Types[n] + (s.RunningNames.Contains(n) ? "+run" : ""));
            return string.Join(", ", parts);
        }

        public static void Armoury_State_Shape()
        {
            var rig = ArmouryRig();
            var r = rig.Send("{\"id\":1,\"cmd\":\"armoury_state\"}");
            True(Ok(r), "ok: " + Msg(r));
            var d = (Dictionary<string, object>)r["data"];
            SeqEqual(new[] { "installed", "startTypes", "running" }, new List<string>(d.Keys), "keys");
            Equal(true, d["installed"], "installed");
            var types = (Dictionary<string, object>)d["startTypes"];
            Equal(5, types.Count, "listed services only");
            Equal("AutomaticDelayedStart", types["ArmouryCrateSEService"], "delayed start kept");
            True(!types.ContainsKey("Spooler"), "unrelated service not reported");
            Equal("ArmouryCrateControlInterface,AsusAppService,ArmouryCrateSEService,LightingService",
                string.Join(",", (object[])d["running"]), "running in list order");
            Equal(0, rig.Services.Changes().Count, "state only reads");

            var bare = new Rig();
            bare.Services.Types["LightingService"] = "Manual";
            d = (Dictionary<string, object>)bare.Send("{\"id\":2,\"cmd\":\"armoury_state\"}")["data"];
            Equal(false, d["installed"], "installed means ArmouryCrateSEService or ArmouryCrateService exists");
            bare.Services.Types["ArmouryCrateService"] = "Automatic";
            d = (Dictionary<string, object>)bare.Send("{\"id\":3,\"cmd\":\"armoury_state\"}")["data"];
            Equal(true, d["installed"], "ArmouryCrateService counts");
        }

        public static void Armoury_Stop_RecordsBeforeChanging()
        {
            var rig = ArmouryRig();
            var r = rig.Send("{\"id\":1,\"cmd\":\"armoury_stop\"}");
            True(Ok(r), "ok: " + Msg(r));
            var d = (Dictionary<string, object>)r["data"];
            var types = (Dictionary<string, object>)d["startTypes"];
            Equal(5, types.Count, "every installed listed service recorded");
            Equal("Automatic", types["ArmouryCrateControlInterface"], "type before the change");
            Equal("AutomaticDelayedStart", types["ArmouryCrateSEService"], "type before the change");
            Equal("Manual", types["LightingService"], "type before the change");
            Equal("Automatic", types["ASUSOptimization"], "stopped service recorded too");
            Equal("ArmouryCrateControlInterface,AsusAppService,ArmouryCrateSEService,LightingService",
                string.Join(",", (object[])d["running"]), "running before the change");
            // Both reads happen before the first change.
            Equal("query types", rig.Services.Calls[0], "types read first");
            Equal("query running", rig.Services.Calls[1], "running read second");
            True(rig.Services.Calls[2].StartsWith("stop "), "then changes");
        }

        // G-Helper AsusService.StopAsusServices (lines 89-94): every running service is stopped and set to
        // "Manual" when it is in servicesAC, else "Disabled" (ProcessHelper.StopDisableService: Stop-Service, then
        // Set-Service -StartupType). Services that are not running are left alone.
        public static void Armoury_Stop_SetsTypesLikeGHelper()
        {
            var rig = ArmouryRig();
            True(Ok(rig.Send("{\"id\":1,\"cmd\":\"armoury_stop\"}")), "ok");
            SeqEqual(new[] {
                "stop ArmouryCrateControlInterface Disabled",
                "stop AsusAppService Disabled",
                "stop ArmouryCrateSEService Manual",
                "stop LightingService Manual" }, rig.Services.Changes(), "changes");
            Equal("ASUSOptimization=Automatic, ArmouryCrateControlInterface=Disabled, ArmouryCrateSEService=Manual, AsusAppService=Disabled, LightingService=Manual, Spooler=Automatic+run",
                Snapshot(rig.Services), "end state");
            Equal("Manual", ArmouryServices.StopStartType("ArmouryCrateService"), "AC list -> Manual");
            Equal("Disabled", ArmouryServices.StopStartType("AsHidService"), "other list -> Disabled");
        }

        // Review focus 4: after a reboot the helper is new and the current state differs from what was recorded;
        // restore applies the recorded start types, not the current ones, and starts the recorded running services.
        public static void restore_uses_recorded_state_after_restart()
        {
            var rig = new Rig();   // fresh helper process
            var s = rig.Services;
            s.Types["ArmouryCrateControlInterface"] = "Disabled";
            s.Types["ASUSOptimization"] = "Automatic";            // recorded as Manual below
            s.Types["AsusAppService"] = "Disabled";
            s.Types["ArmouryCrateSEService"] = "Manual";
            s.Types["LightingService"] = "Manual"; s.RunningNames.Add("LightingService");   // something started it
            var r = rig.Send("{\"id\":1,\"cmd\":\"armoury_restore\",\"args\":{\"recorded\":{"
                + "\"ArmouryCrateSEService\":\"AutomaticDelayedStart\",\"ArmouryCrateControlInterface\":\"Automatic\","
                + "\"ASUSOptimization\":\"Manual\",\"AsusAppService\":\"Automatic\",\"LightingService\":\"Manual\"},"
                + "\"running\":[\"ArmouryCrateControlInterface\",\"AsusAppService\",\"ArmouryCrateSEService\",\"LightingService\"]}}");
            True(Ok(r), "restore ok: " + Msg(r));
            Equal("ASUSOptimization=Manual, ArmouryCrateControlInterface=Automatic+run, ArmouryCrateSEService=AutomaticDelayedStart+run, AsusAppService=Automatic+run, LightingService=Manual+run",
                Snapshot(s), "recorded state applied");
            SeqEqual(new[] {
                "type ArmouryCrateControlInterface Automatic",
                "type ASUSOptimization Manual",
                "type AsusAppService Automatic",
                "type ArmouryCrateSEService AutomaticDelayedStart",
                "type LightingService Manual",
                "start ArmouryCrateControlInterface Automatic",
                "start AsusAppService Automatic",
                "start ArmouryCrateSEService AutomaticDelayedStart",
                "start LightingService Manual" }, s.Changes(), "every start type first (list order), then starts (recorded order)");
        }

        // Final review I4: a running service early in the list that depends on a listed service later in the list
        // starts only after every start type is back, so the dependency is no longer Disabled.
        public static void Armoury_Restore_StartsAfterEveryStartTypeIsBack()
        {
            var rig = new Rig();
            var s = rig.Services;
            s.Types["ArmouryCrateControlInterface"] = "Disabled";
            s.Types["AsHidService"] = "Disabled";
            s.Types["LightingService"] = "Manual";
            s.DependsOn["ArmouryCrateControlInterface"] = "LightingService";   // earlier in the list than its dependency
            s.Types["LightingService"] = "Disabled";
            var r = rig.Send("{\"id\":1,\"cmd\":\"armoury_restore\",\"args\":{\"recorded\":{"
                + "\"ArmouryCrateControlInterface\":\"Automatic\",\"AsHidService\":\"Automatic\",\"LightingService\":\"Manual\"},"
                + "\"running\":[\"LightingService\",\"ArmouryCrateControlInterface\"]}}");
            True(Ok(r), "restore ok: " + Msg(r));
            SeqEqual(new[] {
                "type ArmouryCrateControlInterface Automatic",
                "type AsHidService Automatic",
                "type LightingService Manual",
                "start LightingService Manual",
                "start ArmouryCrateControlInterface Automatic" }, s.Changes(), "types, then starts in the recorded order");
            Equal("ArmouryCrateControlInterface=Automatic+run, AsHidService=Automatic, LightingService=Manual+run", Snapshot(s), "end state");

            // The fake refuses to start a service whose dependency is Disabled, like the service manager.
            var bare = new FakeServices();
            bare.Types["A"] = "Automatic"; bare.Types["B"] = "Disabled"; bare.DependsOn["A"] = "B";
            bool threw = false;
            try { bare.Start("A", "Automatic"); } catch (InvalidOperationException) { threw = true; }
            True(threw, "fake enforces the dependency");
        }

        public static void Armoury_Restore_SkipsUninstalledServices()
        {
            var rig = new Rig();
            rig.Services.Types["ArmouryCrateSEService"] = "Manual";
            var r = rig.Send("{\"id\":1,\"cmd\":\"armoury_restore\",\"args\":{\"recorded\":{\"ArmouryCrateSEService\":\"Automatic\",\"AsHidService\":\"Automatic\"},\"running\":[\"ArmouryCrateSEService\",\"AsHidService\"]}}");
            True(Ok(r), "ok: " + Msg(r));
            Equal("AsHidService", string.Join(",", (object[])((Dictionary<string, object>)r["data"])["skipped"]), "skipped");
            SeqEqual(new[] { "type ArmouryCrateSEService Automatic", "start ArmouryCrateSEService Automatic" }, rig.Services.Changes(), "only the installed one");
        }

        public static void Armoury_Restore_RefusesBadInputBeforeAnyCall()
        {
            var rig = ArmouryRig();
            string[] bad =
            {
                "{\"id\":1,\"cmd\":\"armoury_restore\",\"args\":{\"recorded\":{\"Spooler\":\"Disabled\"},\"running\":[]}}",
                "{\"id\":2,\"cmd\":\"armoury_restore\",\"args\":{\"recorded\":{\"AsHidService\":\"Boot\"},\"running\":[]}}",
                "{\"id\":3,\"cmd\":\"armoury_restore\",\"args\":{\"recorded\":{\"AsHidService\":\"Manual\"},\"running\":[\"LightingService\"]}}",
                "{\"id\":4,\"cmd\":\"armoury_restore\",\"args\":{\"recorded\":{\"AsHidService\":7},\"running\":[]}}",
                "{\"id\":5,\"cmd\":\"armoury_restore\",\"args\":{\"recorded\":{},\"running\":[\"x\\\" & sc delete\"]}}",
            };
            foreach (string b in bad)
            {
                var r = rig.Send(b);
                True(!Ok(r) && Msg(r).StartsWith("Refused"), "refused: " + b + " -> " + Msg(r));
            }
            Equal(0, rig.Services.Calls.Count, "no service call at all");
        }

        public static void Armoury_PartialFailure_ReportsServiceName_AndRollsBack()
        {
            var rig = ArmouryRig();
            string before = Snapshot(rig.Services);
            rig.Services.FailStop.Add("ArmouryCrateSEService");   // third stop: stops it, then fails to set the type
            var r = rig.Send("{\"id\":1,\"cmd\":\"armoury_stop\"}");
            True(!Ok(r), "failure reported");
            True(Msg(r).Contains("ArmouryCrateSEService") && Msg(r).Contains("access denied"), "names the service and the reason: " + Msg(r));
            Equal("ArmouryCrateSEService", ((Dictionary<string, object>)r["data"])["failed"], "data.failed");
            Equal(true, ((Dictionary<string, object>)r["data"])["rolledBack"], "data.rolledBack");
            Equal(before, Snapshot(rig.Services), "every service back as it was");
            SeqEqual(new[] {
                "stop ArmouryCrateControlInterface Disabled",
                "stop AsusAppService Disabled",
                "stop ArmouryCrateSEService Manual",
                "start ArmouryCrateSEService AutomaticDelayedStart",
                "start AsusAppService Automatic",
                "start ArmouryCrateControlInterface Automatic" }, rig.Services.Changes(), "rolled back in reverse");

            // Restore failing half-way rolls back to the state before the restore (the switch stays on).
            rig = new Rig();
            var s = rig.Services;
            s.Types["ArmouryCrateControlInterface"] = "Disabled";
            s.Types["ASUSOptimization"] = "Automatic";
            s.Types["ArmouryCrateSEService"] = "Manual";
            before = Snapshot(s);
            s.FailStart.Add("ArmouryCrateSEService");
            r = rig.Send("{\"id\":2,\"cmd\":\"armoury_restore\",\"args\":{\"recorded\":{\"ArmouryCrateControlInterface\":\"Automatic\",\"ASUSOptimization\":\"Manual\",\"ArmouryCrateSEService\":\"Automatic\"},\"running\":[\"ArmouryCrateControlInterface\",\"ArmouryCrateSEService\"]}}");
            True(!Ok(r) && Msg(r).Contains("ArmouryCrateSEService"), "restore failure names the service: " + Msg(r));
            Equal(before, Snapshot(s), "restore rolled back");

            // A rollback that fails too is reported with its service name.
            rig = ArmouryRig();
            rig.Services.FailStop.Add("ArmouryCrateSEService");
            rig.Services.FailStart.Add("AsusAppService");
            r = rig.Send("{\"id\":3,\"cmd\":\"armoury_stop\"}");
            True(!Ok(r) && Msg(r).Contains("Rollback also failed for AsusAppService"), "rollback failure listed: " + Msg(r));
            Equal(false, ((Dictionary<string, object>)r["data"])["rolledBack"], "data.rolledBack false");
            True(rig.Services.RunningNames.Contains("ArmouryCrateControlInterface"), "rollback continues past a failure");
        }

        public static void Probe_ReadOnlyWrapperRefusesWrites()
        {
            var acpi = new FakeAcpi();
            var ro = new ReadOnlyAcpi(acpi);
            bool threw = false;
            try { ro.DeviceSet(DeviceIds.PerformanceMode, 0); } catch (InvalidOperationException) { threw = true; }
            True(threw, "DeviceSet throws");
            threw = false;
            try { ro.SetFanCurve(0, new byte[16]); } catch (InvalidOperationException) { threw = true; }
            True(threw, "SetFanCurve throws");
            Equal(0, acpi.Calls.Count, "nothing reached the device");
            acpi.Values[DeviceIds.TempCpu] = 50;
            Equal(50, ro.DeviceGet(DeviceIds.TempCpu), "reads pass through");
        }

        public static void Probe_ReadFailures_ReportedNotThrown()
        {
            var acpi = new FakeAcpi();
            acpi.ThrowOnRead = true;
            string json = Probe.Run(acpi, null);
            var p = (Dictionary<string, object>)Json.DeserializeObject(json);
            True(p["product"] == null && p["mode"] == null && p["cpuTempC"] == null && p["fanCurve"] == null, "nulls");
            True(((object[])p["errors"]).Length >= 7, "every failed read listed: " + json);

            acpi = new FakeAcpi();   // device answers, but with an all-zero curve and no mode
            p = (Dictionary<string, object>)Json.DeserializeObject(Probe.Run(acpi, "X"));
            True(p["fanCurve"] == null, "invalid curve not reported as a curve");
            True(acpi.Calls.Contains("getcurve 0 0"), "falls back to the balanced curve when the mode is unknown");
        }
    }
}
