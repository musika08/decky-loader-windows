using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Web.Script.Serialization;
using Ally.Shared;

namespace Traction.Helper.Tests
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
            public FakeAdl Adl = new FakeAdl();
            public FakeServices Services = new FakeServices();
            public FakeHidBus Bus = new FakeHidBus();
            public FakeSystemControls Sys = new FakeSystemControls();
            public TractionCore Core;
            public Rig() : this(true) { }
            public Rig(bool ally)
            {
                Core = new TractionCore(Adl, Services, ally, ms => { });
                Core.Hid = new AsusHid(Bus);
                Core.SystemControls = Sys;
                Core.ProductName = "ROG Ally X RC72LA_RC72LA";
            }

            public Dictionary<string, object> Send(string line)
            {
                string reply = Core.HandleLine(line);
                True(reply != null && reply.IndexOf('\n') < 0, "reply must be one line");
                return (Dictionary<string, object>)Json.DeserializeObject(reply);
            }
        }

        private static bool Ok(Dictionary<string, object> r) { return (bool)r["ok"]; }
        private static string Msg(Dictionary<string, object> r) { return (string)r["message"]; }

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
            Equal(0, rig.Bus.Writes.Count, "no hardware call");
        }

        // The commands that stayed in Overdrive are unknown here.
        public static void OverdriveCommands_Refused()
        {
            var rig = new Rig();
            int n = 0;
            foreach (string cmd in new[] { "status", "sensors", "set_mode", "set_power", "get_fans", "set_fans", "reset_fans",
                "get_fps_limit", "set_fps_limit", "get_cpu_boost", "set_cpu_boost", "get_power_mode", "set_power_mode",
                "get_auto_sr", "set_auto_sr" })
            {
                var r = rig.Send("{\"id\":" + (++n) + ",\"cmd\":\"" + cmd + "\"}");
                True(!Ok(r) && Msg(r).Contains("unknown command"), cmd + ": " + Msg(r));
            }
            Equal(0, rig.Bus.Writes.Count, "no hardware call");
        }

        public static void UnknownArg_Refused()
        {
            var rig = new Rig();
            var r = rig.Send("{\"id\":2,\"cmd\":\"set_vibration\",\"args\":{\"strength\":20,\"tdp\":40}}");
            True(!Ok(r), "unknown arg must be refused");
            True(Msg(r).Contains("tdp"), "message names the arg: " + Msg(r));
            Equal(0, rig.Bus.Writes.Count, "no hardware call");

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
            r = rig.Send("{\"id\":5,\"cmd\":\"set_vibration\",\"args\":{\"strength\":\"20\"}}");
            True(!Ok(r), "string where int expected refused");
            Equal(5, r["id"], "id echoed on bad arg type");
            r = rig.Send("{\"id\":6,\"cmd\":\"set_vibration\",\"args\":{\"strength\":20.5}}");
            True(!Ok(r), "fraction where int expected refused");
            Equal(0, rig.Bus.Writes.Count, "no hardware call");
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
            rig.Core.SystemControls = new ThrowingSystemControls();
            var r = rig.Send("{\"id\":11,\"cmd\":\"get_touchscreen\"}");
            True(!Ok(r), "exception reported as failure");
            True(Msg(r).Contains("read attempted"), "exception message passed on: " + Msg(r));
        }

        private class ThrowingSystemControls : ISystemControls
        {
            public TouchInfo GetTouchscreen() { throw new InvalidOperationException("read attempted"); }
            public bool StartSetTouchscreen(bool enabled, out string err) { throw new InvalidOperationException("write attempted"); }
            public string ReadGpuSettings(out string err) { throw new InvalidOperationException("read attempted"); }
            public bool WriteGpuSettings(string value, out string err) { throw new InvalidOperationException("write attempted"); }
            public bool AutoSrInstalled() { return false; }
            public string ForegroundExe() { return ""; }
        }

        // ---------- frame_sample ----------

        public static void FrameSample_ReturnsFpsAndGfxPower()
        {
            var rig = new Rig();
            rig.Adl.Fps = 58.5f;
            rig.Adl.Gpu = new GpuSensors { TempC = 45, GfxPowerW = 7 };
            var r = rig.Send("{\"id\":1,\"cmd\":\"frame_sample\"}");
            True(Ok(r), "frame_sample ok");
            var d = (Dictionary<string, object>)r["data"];
            Equal(true, d["supported"], "supported");
            Equal(58.5, Convert.ToDouble(d["fps"]), "fps");
            Equal(7, d["gpuPowerW"], "gpuPowerW");
            Equal(0, ((object[])d["errors"]).Length, "no errors");
            Equal(0, rig.Adl.Sets.Count, "no ADL write");
        }

        public static void FrameSample_Unsupported()
        {
            var rig = new Rig();
            rig.Adl.FrameSupported = false;
            var r = rig.Send("{\"id\":1,\"cmd\":\"frame_sample\"}");
            True(Ok(r), "frame_sample ok");
            var d = (Dictionary<string, object>)r["data"];
            Equal(false, d["supported"], "supported");
            True(d["fps"] == null, "fps null");
            var errors = (object[])d["errors"];
            Equal(1, errors.Length, "one error");
            True(((string)errors[0]).Contains("frame rate"), "error names the frame rate: " + errors[0]);
        }

        public static void FrameSample_ZeroPowerIsNull()
        {
            var rig = new Rig();
            rig.Adl.Gpu = new GpuSensors { GfxPowerW = 0 };
            var d = (Dictionary<string, object>)rig.Send("{\"id\":1,\"cmd\":\"frame_sample\"}")["data"];
            True(d["gpuPowerW"] == null, "0 W = not reported");
            Equal(60.0, Convert.ToDouble(d["fps"]), "fps still reported");
        }

        public static void FrameSample_RestartsMetricsAfterReset()
        {
            var rig = new Rig();
            rig.Send("{\"id\":1,\"cmd\":\"frame_sample\"}");
            rig.Send("{\"id\":2,\"cmd\":\"frame_sample\"}");
            Equal(1, rig.Adl.FrameStarts, "two samples start the metrics once");
            rig.Adl.Reset();
            var core = new TractionCore(rig.Adl, rig.Services, true, ms => { });
            core.HandleLine("{\"id\":3,\"cmd\":\"frame_sample\"}");
            Equal(2, rig.Adl.FrameStarts, "a restarted helper starts the metrics again");
        }

        // AdlHardware (real IAdl) on the fake control: start once, stop on dispose, missing entry points = unsupported.
        public static void AdlHardware_FrameRate()
        {
            var c = new FakeFpsControl { FpsValue = 77.5f };
            var hw = new AdlHardware(() => c);
            Equal(77.5, Convert.ToDouble(hw.GetFrameRate()), "fps");
            hw.GetFrameRate();
            Equal(1, c.Starts, "started once");
            True(hw.FrameRateSupported, "supported");
            c.FpsValue = float.PositiveInfinity;
            Equal(0.0, Convert.ToDouble(hw.GetFrameRate()), "+Infinity fps = 0");
            c.FpsValue = float.NaN;
            Equal(0.0, Convert.ToDouble(hw.GetFrameRate()), "NaN fps = 0");
            hw.Dispose();
            Equal(1, c.Stops, "stopped on dispose");

            var c2 = new FakeFpsControl { NoEntryPoint = true };
            var hw2 = new AdlHardware(() => c2);
            True(hw2.GetFrameRate() == null, "no entry point = null");
            True(!hw2.FrameRateSupported, "marked unsupported");
            True(hw2.Unavailable != null && hw2.Unavailable.Contains("frame rate"), "reason");
            hw2.Dispose();
            Equal(0, c2.Stops, "never started, never stopped");

            var c3 = new FakeFpsControl { StartResult = -1 };
            var hw3 = new AdlHardware(() => c3);
            True(hw3.GetFrameRate() == null, "start refused = null");
            True(hw3.FrameRateSupported, "still supported (retried next sample)");
        }

        // ---------- ASUS controller (mode, grip vibration) ----------

        private const string XboxProduct = "ROG Xbox Ally X RC73XA_RC73XA";

        // The report as it must reach the device: the bytes, then zeros up to the collection's feature length.
        private static byte[] Padded(int length, params byte[] head)
        {
            var b = new byte[length];
            Array.Copy(head, b, head.Length);
            return b;
        }

        private static string Hex(byte[] b) { return b == null ? "null" : BitConverter.ToString(b); }

        private static void WritesAre(FakeHidBus bus, string what, params byte[][] reports)
        {
            Equal(reports.Length, bus.Writes.Count, what + ": write count");
            for (int i = 0; i < reports.Length; i++)
            {
                Equal(FakeHidBus.MatchPath, bus.Writes[i].Key, what + ": write " + i + " path");
                Equal(Hex(reports[i]), Hex(bus.Writes[i].Value), what + ": write " + i + " bytes");
            }
        }

        public static void Controller_ModeBytes()
        {
            var rig = new Rig();
            var r = rig.Send("{\"id\":1,\"cmd\":\"set_controller_mode\",\"args\":{\"mode\":\"gamepad\"}}");
            True(Ok(r), "gamepad: " + Msg(r));
            Equal("gamepad", ((Dictionary<string, object>)r["data"])["mode"], "echoed mode");
            WritesAre(rig.Bus, "gamepad", Padded(64, 0x5A, 0xD1, 0x01, 0x01, 0x01));

            rig.Bus.Writes.Clear();
            r = rig.Send("{\"id\":2,\"cmd\":\"set_controller_mode\",\"args\":{\"mode\":\"desktop\"}}");
            True(Ok(r), "desktop: " + Msg(r));
            Equal("desktop", ((Dictionary<string, object>)r["data"])["mode"], "echoed mode");
            WritesAre(rig.Bus, "desktop", Padded(64, 0x5A, 0xD1, 0x01, 0x01, 0x03));

            // The padding follows the collection's own feature report length.
            var rig48 = new Rig();
            rig48.Bus = new FakeHidBus(48);
            rig48.Core.Hid = new AsusHid(rig48.Bus);
            True(Ok(rig48.Send("{\"id\":3,\"cmd\":\"set_controller_mode\",\"args\":{\"mode\":\"desktop\"}}")), "48-byte collection");
            WritesAre(rig48.Bus, "desktop on 48", Padded(48, 0x5A, 0xD1, 0x01, 0x01, 0x03));

            rig.Bus.Writes.Clear();
            rig.Bus.Fail = true;
            r = rig.Send("{\"id\":4,\"cmd\":\"set_controller_mode\",\"args\":{\"mode\":\"gamepad\"}}");
            True(!Ok(r) && Msg(r).Contains("feature write failed"), "write error passed on: " + Msg(r));
        }

        public static void Controller_VibrationBytes()
        {
            var rig = new Rig();
            var r = rig.Send("{\"id\":1,\"cmd\":\"set_vibration\",\"args\":{\"strength\":100}}");
            True(Ok(r), "on: " + Msg(r));
            Equal(100, ((Dictionary<string, object>)r["data"])["strength"], "echoed strength");
            WritesAre(rig.Bus, "non-Xbox on", Padded(64, 0x5A, 0xD1, 0x06, 0x02, 100, 100));

            rig.Bus.Writes.Clear();
            True(Ok(rig.Send("{\"id\":2,\"cmd\":\"set_vibration\",\"args\":{\"strength\":0}}")), "off");
            WritesAre(rig.Bus, "non-Xbox off", Padded(64, 0x5A, 0xD1, 0x06, 0x02, 0, 0));

            var x = new Rig();
            x.Core.ProductName = XboxProduct;
            True(Ok(x.Send("{\"id\":3,\"cmd\":\"set_vibration\",\"args\":{\"strength\":55}}")), "Xbox");
            WritesAre(x.Bus, "Xbox", Padded(64, 0x5A, 0xD1, 0x06, 0x02, 55, 55), Padded(64, 0x5A, 0xD1, 0x1C, 0x02, 55, 55));

            // A failed first write stops there and is reported.
            x.Bus.Writes.Clear();
            x.Bus.Fail = true;
            r = x.Send("{\"id\":4,\"cmd\":\"set_vibration\",\"args\":{\"strength\":100}}");
            True(!Ok(r) && Msg(r).Contains("feature write failed"), "write error passed on: " + Msg(r));
            Equal(1, x.Bus.Writes.Count, "no second write after a failure");
        }

        public static void ControllerHid_OnlyMatchingCollection()
        {
            SeqEqual(new[] { "1A30", "1854", "1869", "1866", "19B6", "1822", "1837", "1854", "184A", "183D", "8502", "1807",
                "17E0", "1ABE", "1B4C", "1B6E", "1B2C", "8854", "1CE7", "1CE6", "1BF2", "1BF3", "1CD7", "1CD8" },
                new List<int>(AsusHid.MAIN_AURA_PIDS).ConvertAll(p => p.ToString("X4")), "PIDs copied from G-Helper MAIN_AURA_PIDS");
            Equal(0x0B05, AsusHid.ASUS_ID, "vendor id");
            Equal((byte)0x5A, AsusHid.INPUT_ID, "input report id");

            var bus = new FakeHidBus();
            var hid = new AsusHid(bus);
            HidInfo info = hid.Find();
            True(info.Found, "found");
            Equal(0x1B4C, info.Pid, "pid");
            Equal(64, info.FeatureLength, "feature length");

            // Every command writes to the one matching collection only.
            var rig = new Rig();
            rig.Core.ProductName = XboxProduct;
            rig.Send("{\"id\":1,\"cmd\":\"set_controller_mode\",\"args\":{\"mode\":\"desktop\"}}");
            rig.Send("{\"id\":2,\"cmd\":\"set_vibration\",\"args\":{\"strength\":100}}");
            Equal(3, rig.Bus.Writes.Count, "three writes");
            foreach (var w in rig.Bus.Writes) Equal(FakeHidBus.MatchPath, w.Key, "written path");

            // controller_info: found, PID as hex, Xbox from the product name. It writes nothing.
            var r = rig.Send("{\"id\":3,\"cmd\":\"controller_info\"}");
            True(Ok(r), "info: " + Msg(r));
            var d = (Dictionary<string, object>)r["data"];
            Equal(true, d["found"], "found");
            Equal("0x1B4C", d["pid"], "pid");
            Equal(true, d["xbox"], "xbox");
            Equal(3, rig.Bus.Writes.Count, "info wrote nothing");
            var plain = new Rig();
            Equal(false, ((Dictionary<string, object>)plain.Send("{\"id\":4,\"cmd\":\"controller_info\"}")["data"])["xbox"], "non-Xbox");
            plain.Core.ProductName = null;
            Equal(false, ((Dictionary<string, object>)plain.Send("{\"id\":5,\"cmd\":\"controller_info\"}")["data"])["xbox"], "unknown product");

            // AsusHid itself refuses a report that is not a padded 0x5A report, before anything is sent.
            string err;
            True(!hid.SetFeature(new byte[] { 0x5A, 0xD1, 0x01, 0x01, 0x01 }, out err), "unpadded refused");
            True(!hid.SetFeature(Padded(64, 0x5D, 0xD1), out err), "other report id refused");
            True(!hid.SetFeature(null, out err), "null refused");
            Equal(0, bus.Writes.Count, "nothing sent");

            // Without the 0x5A collection nothing is found, even with the controller's other collections present.
            bus.Devices.RemoveAll(c => c.Path == FakeHidBus.MatchPath);
            True(!hid.Find().Found, "no 0x5A collection, not found");
            True(!hid.SetFeature(Padded(64, 0x5A, 0xD1, 0x01, 0x01, 0x01), out err), "write without a controller refused");
            Equal(0, bus.Writes.Count, "nothing sent to the other collections");
        }

        // The Win32 structs as the x64 Windows SDK lays them out. Nothing is opened or listed.
        public static void ControllerHid_Win32LayoutsX64()
        {
            Equal(8, IntPtr.Size, "tests run as x64, like the helper");
            SeqEqual(new[] { "32", "12", "64", "8" }, new List<int>(Win32HidBus.LayoutSizes()).ConvertAll(n => n.ToString()),
                "SP_DEVICE_INTERFACE_DATA, HIDD_ATTRIBUTES, HIDP_CAPS, detail cbSize");
        }

        public static void Controller_NotFoundRefused()
        {
            var rig = new Rig();
            rig.Bus.Devices.RemoveAll(c => c.Path == FakeHidBus.MatchPath);
            foreach (string line in new[] {
                "{\"id\":1,\"cmd\":\"set_controller_mode\",\"args\":{\"mode\":\"gamepad\"}}",
                "{\"id\":2,\"cmd\":\"set_vibration\",\"args\":{\"strength\":100}}" })
            {
                var r = rig.Send(line);
                Equal("Controller not found", Msg(r), "refused: " + line);
                True(!Ok(r), "not ok");
            }
            var info = rig.Send("{\"id\":3,\"cmd\":\"controller_info\"}");
            True(Ok(info), "info still answers");
            var d = (Dictionary<string, object>)info["data"];
            Equal(false, d["found"], "found");
            True(d["pid"] == null, "no pid");
            Equal(0, rig.Bus.Writes.Count, "nothing written");

            // No HID layer at all (Hid left null): refused as not available.
            var core = new TractionCore(new FakeAdl(), new FakeServices(), true, ms => { });
            foreach (string line in new[] {
                "{\"id\":1,\"cmd\":\"controller_info\"}",
                "{\"id\":2,\"cmd\":\"set_controller_mode\",\"args\":{\"mode\":\"gamepad\"}}",
                "{\"id\":3,\"cmd\":\"set_vibration\",\"args\":{\"strength\":0}}" })
            {
                var r = (Dictionary<string, object>)Json.DeserializeObject(core.HandleLine(line));
                True(!Ok(r) && Msg(r) == TractionCore.ControllerUnavailable, "not available: " + Msg(r));
            }
        }

        public static void Controller_InvalidArgsRefused()
        {
            var rig = new Rig();
            foreach (string m in new[] { "\"Gamepad\"", "\"mouse\"", "\"auto\"", "\"\"", "1", "3", "null", "true" })
                True(!Ok(rig.Send("{\"id\":1,\"cmd\":\"set_controller_mode\",\"args\":{\"mode\":" + m + "}}")), "mode " + m + " refused");
            True(!Ok(rig.Send("{\"id\":2,\"cmd\":\"set_controller_mode\"}")), "mode required");
            foreach (string v in new[] { "-1", "101", "255", "\"50\"", "1.5", "null", "true" })
                True(!Ok(rig.Send("{\"id\":3,\"cmd\":\"set_vibration\",\"args\":{\"strength\":" + v + "}}")), "strength " + v + " refused");
            True(!Ok(rig.Send("{\"id\":4,\"cmd\":\"set_vibration\"}")), "strength required");
            True(!Ok(rig.Send("{\"id\":5,\"cmd\":\"set_vibration\",\"args\":{\"strength\":50,\"x\":1}}")), "unknown arg refused");
            True(!Ok(rig.Send("{\"id\":6,\"cmd\":\"controller_info\",\"args\":{\"x\":1}}")), "info takes no args");
            Equal(0, rig.Bus.Lists, "refusals never reached the HID layer");
            Equal(0, rig.Bus.Writes.Count, "nothing written");
        }

        // ---------- touchscreen and foreground ----------

        public static void Touchscreen_SetReturnsAtOnce()
        {
            var rig = new Rig();
            var r = rig.Send("{\"id\":1,\"cmd\":\"set_touchscreen\",\"args\":{\"enabled\":false}}");
            True(Ok(r), "set accepted: " + Msg(r));
            Equal(false, ((Dictionary<string, object>)r["data"])["enabled"], "echo");
            SeqEqual(new[] { "False" }, rig.Sys.TouchSets.ConvertAll(b => b.ToString()), "started once, disabling");
            Equal(0, rig.Sys.TouchReads, "set never waits on a read");
            // a second change while the first runs is refused
            var r2 = rig.Send("{\"id\":2,\"cmd\":\"set_touchscreen\",\"args\":{\"enabled\":true}}");
            True(!Ok(r2) && Msg(r2).Contains("already running"), "busy refused: " + Msg(r2));
            Equal(1, rig.Sys.TouchSets.Count, "second not started");
            // arguments are checked
            True(!Ok(rig.Send("{\"id\":3,\"cmd\":\"set_touchscreen\",\"args\":{\"enabled\":1}}")), "enabled must be a boolean");
            True(!Ok(rig.Send("{\"id\":4,\"cmd\":\"set_touchscreen\"}")), "enabled required");
            rig.Sys.TouchBusy = false;
            rig.Sys.TouchStartFails = true;
            var r3 = rig.Send("{\"id\":5,\"cmd\":\"set_touchscreen\",\"args\":{\"enabled\":true}}");
            True(!Ok(r3) && Msg(r3) == "Could not start PowerShell.", "start failure reported: " + Msg(r3));
        }

        public static void Touchscreen_GetReportsState()
        {
            var rig = new Rig();
            var r = rig.Send("{\"id\":1,\"cmd\":\"get_touchscreen\"}");
            True(Ok(r), Msg(r));
            var d = (Dictionary<string, object>)r["data"];
            Equal(true, d["found"], "found");
            Equal(true, d["enabled"], "enabled");
            Equal(@"HID\NVTK0603&COL01\5&1234&0&0000", d["instanceId"], "instance id");
            rig.Sys.Touch = new TouchInfo { Found = true, Enabled = false, InstanceId = "x" };
            Equal(false, ((Dictionary<string, object>)rig.Send("{\"id\":2,\"cmd\":\"get_touchscreen\"}")["data"])["enabled"], "disabled");
            rig.Sys.Touch = new TouchInfo { Error = "PowerShell did not answer in 5 seconds." };
            var e = rig.Send("{\"id\":3,\"cmd\":\"get_touchscreen\"}");
            True(!Ok(e) && Msg(e) == "PowerShell did not answer in 5 seconds.", "read timeout reported: " + Msg(e));
        }

        public static void Touchscreen_PickDevice_PresentOnlyFixedText()
        {
            string cmd = SystemControls.PickDevice;
            True(cmd.StartsWith("Get-PnpDevice -PresentOnly -FriendlyName '*touch*screen*' |"), "present devices only: " + cmd);
            True(cmd.IndexOf("{0}") < 0 && cmd.IndexOf('"') < 0, "fixed text, no placeholder for client input");
        }

        public static void Touchscreen_NotFound()
        {
            var rig = new Rig();
            rig.Sys.Touch = new TouchInfo { Found = false };
            var r = rig.Send("{\"id\":1,\"cmd\":\"get_touchscreen\"}");
            True(Ok(r), "not found is a normal answer: " + Msg(r));
            var d = (Dictionary<string, object>)r["data"];
            Equal(false, d["found"], "found");
            Equal(false, d["enabled"], "enabled");
            Equal(null, d["instanceId"], "no instance id");
        }

        public static void System_NotAvailableWithoutSystemControls()
        {
            var rig = new Rig();
            rig.Core.SystemControls = null;
            foreach (string c in new[] { "get_touchscreen", "foreground" })
            {
                var r = rig.Send("{\"id\":1,\"cmd\":\"" + c + "\"}");
                True(!Ok(r) && Msg(r) == TractionCore.SystemUnavailable, c + ": " + Msg(r));
            }
            var s = rig.Send("{\"id\":2,\"cmd\":\"set_touchscreen\",\"args\":{\"enabled\":true}}");
            True(!Ok(s) && Msg(s) == TractionCore.SystemUnavailable, "set_touchscreen: " + Msg(s));
        }

        public static void Foreground_Lowercase()
        {
            var rig = new Rig();
            rig.Sys.Foreground = "steam.exe";
            Equal("steam.exe", ((Dictionary<string, object>)rig.Send("{\"id\":1,\"cmd\":\"foreground\"}")["data"])["exe"], "exe");
            rig.Sys.Foreground = "Steam.EXE";
            Equal("steam.exe", ((Dictionary<string, object>)rig.Send("{\"id\":2,\"cmd\":\"foreground\"}")["data"])["exe"], "lowercased by the protocol too");
            rig.Sys.Foreground = null;
            Equal("", ((Dictionary<string, object>)rig.Send("{\"id\":3,\"cmd\":\"foreground\"}")["data"])["exe"], "none gives empty");
        }

        public static void Foreground_AllowedOffAlly()
        {
            var rig = new Rig(false);
            rig.Sys.Foreground = "game.exe";
            True(Ok(rig.Send("{\"id\":1,\"cmd\":\"foreground\"}")), "read");
            True(Ok(rig.Send("{\"id\":2,\"cmd\":\"get_touchscreen\"}")), "read");
            True(Ok(rig.Send("{\"id\":3,\"cmd\":\"controller_info\"}")), "read");
            True(!Ok(rig.Send("{\"id\":4,\"cmd\":\"set_touchscreen\",\"args\":{\"enabled\":false}}")), "write refused");
            Equal(0, rig.Sys.TouchSets.Count, "no touch change");
        }

        public static void NotAlly_RefusesAllButReadAllowList()
        {
            SeqEqual(new[] { "hello", "frame_sample", "foreground", "armoury_state", "controller_info", "get_touchscreen" },
                new List<string>(TractionCore.ReadCommands), "read allow-list");
            var rig = new Rig(false);
            int n = 0, refused = 0;
            foreach (string cmd in TractionCore.CommandNames)
            {
                var r = rig.Send("{\"id\":" + (++n) + ",\"cmd\":\"" + cmd + "\"}");
                bool read = Array.IndexOf(TractionCore.ReadCommands, cmd) >= 0;
                if (read) True(Msg(r) != TractionCore.NotAllyMessage, cmd + " is a read and must not be refused as non-Ally");
                else { True(!Ok(r) && Msg(r) == TractionCore.NotAllyMessage, cmd + " refused: " + Msg(r)); refused++; }
            }
            Equal(5, refused, "every non-read command refused");
            Equal(0, rig.Bus.Writes.Count, "no HID write");
            Equal(0, rig.Adl.Sets.Count, "no ADL write");
            Equal(0, rig.Services.Changes().Count, "no service change");
            Equal(0, rig.Sys.TouchSets.Count, "no touch change");
        }

        // ---------- Armoury Crate ----------

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
    }
}
