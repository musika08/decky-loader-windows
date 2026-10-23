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
            public HelperCore Core;
            public Rig() { Core = new HelperCore(Acpi, Adl, Services, true); }

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

        public static void Curve_RejectsNonRisingTemps()
        {
            string reason;
            int[] t = GoodTemps(); t[3] = 50;    // equal to point 3
            True(!Validation.CheckCurve(t, GoodSpeeds(), out reason), "equal temps refused");
            Equal("Point 4: temperature must be higher than point 3.", reason, "reason");
            t = GoodTemps(); t[1] = 25;
            True(!Validation.CheckCurve(t, GoodSpeeds(), out reason), "falling temps refused");
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
    }
}
