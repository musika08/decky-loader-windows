using System;
using System.Collections.Generic;

namespace Overdrive.Helper.Tests
{
    // In-memory stand-ins for the hardware interfaces. Nothing here touches a device, a driver or a service.
    public class FakeAcpi : IAcpi
    {
        public readonly Dictionary<uint, int> Values = new Dictionary<uint, int>();
        public readonly List<string> Calls = new List<string>();
        public readonly Dictionary<int, byte[]> Curves = new Dictionary<int, byte[]>();       // CPU fan, by mode
        public readonly Dictionary<int, byte[]> GpuCurves = new Dictionary<int, byte[]>();    // GPU fan, by mode
        public int SetResult = 1;          // G-Helper treats DeviceSet result 1 as OK
        public bool ThrowOnWrite;
        public bool ThrowOnRead;

        public int DeviceGet(uint id)
        {
            if (ThrowOnRead) throw new InvalidOperationException("read failed");
            Calls.Add("get 0x" + id.ToString("X8"));
            int v;
            return Values.TryGetValue(id, out v) ? v : -1;
        }

        public int DeviceSet(uint id, int value)
        {
            if (ThrowOnWrite) throw new InvalidOperationException("write attempted");
            Calls.Add("set 0x" + id.ToString("X8") + "=" + value);
            Values[id] = value;
            return SetResult;
        }

        public byte[] GetFanCurve(int fan, int mode)
        {
            if (ThrowOnRead) throw new InvalidOperationException("read failed");
            Calls.Add("getcurve " + fan + " " + mode);
            byte[] c;
            var table = fan == DeviceIds.FanGpu ? GpuCurves : Curves;
            return table.TryGetValue(mode, out c) ? (byte[])c.Clone() : new byte[16];
        }

        public int SetFanCurve(int fan, byte[] curve)
        {
            if (ThrowOnWrite) throw new InvalidOperationException("write attempted");
            Calls.Add("setcurve " + fan + " " + BitConverter.ToString(curve));
            return SetResult;
        }
    }

    public class FakeAdl : IAdl
    {
        public int Limit;
        public bool Missing;
        public string Problem = "fake ADL missing";
        public readonly List<int> Sets = new List<int>();

        public int GetFpsLimit() { return Missing ? -1 : Limit; }

        public int SetFpsLimit(int fps)
        {
            if (Missing) return -1;
            Sets.Add(fps);
            Limit = fps;
            return 1;
        }

        public string Unavailable { get { return Missing ? Problem : null; } }

        // The iGPU readings the fake driver reports; null = the driver reports no sensors.
        public GpuSensors Gpu = new GpuSensors { TempC = 45, Use = 30, GfxPowerW = 5, CpuPowerW = 8, AsicPowerW = 14 };
        public GpuSensors GetGpuSensors() { return Missing ? null : Gpu; }

        // The frame rate the fake driver reports; the metrics start on the first sample (like the real AdlHardware).
        public float? Fps = 60f;
        public bool FrameSupported = true;
        public int FrameStarts;
        private bool frameStarted;
        public bool FrameRateSupported { get { return FrameSupported; } }

        public float? GetFrameRate()
        {
            if (Missing || !FrameSupported) return null;
            if (!frameStarted) { frameStarted = true; FrameStarts++; }
            return Fps;
        }

        // A restarted helper: the fake's started flag is cleared, so the next sample starts the metrics again.
        public void Reset() { frameStarted = false; }
    }

    // Stands in for the ported AmdGpuControl behind AdlHardware: same get/set contract as G-Helper
    // (SetFPSLimit writes the AC and DC value, GetFPSLimit returns the AC value; 1 OK, 0 refused, -1 unavailable).
    public class FakeFpsControl : IFpsControl
    {
        public string ProblemText;
        public int Ac, Dc;
        public bool Refuse;
        public bool Disposed;

        public string Problem { get { return ProblemText; } }
        public int GetFPSLimit() { return Ac; }

        public int SetFPSLimit(int limit)
        {
            if (Refuse) return 0;
            Ac = limit;
            Dc = limit;
            return 1;
        }

        public GpuSensors Gpu;
        public GpuSensors GetiGpuSensors() { return Gpu; }

        public int StartResult, GetResult, StopResult;
        public float FpsValue;
        public bool NoEntryPoint;
        public int Starts, Stops;
        public int FrameMetricsStart() { if (NoEntryPoint) throw new EntryPointNotFoundException(); Starts++; return StartResult; }
        public int FrameMetricsGet(out float fps) { if (NoEntryPoint) throw new EntryPointNotFoundException(); fps = FpsValue; return GetResult; }
        public int FrameMetricsStop() { Stops++; return StopResult; }

        public void Dispose() { Disposed = true; }
    }

    // Windows power stand-in: boost AC/DC values and the active overlay, with every call recorded.
    public class FakeWinPower : IWinPower
    {
        public int Ac = 3, Dc = 3;
        public string Overlay = "00000000-0000-0000-0000-000000000000";
        public bool Fail;
        public readonly List<string> Calls = new List<string>();

        public int[] GetCpuBoost()
        {
            Calls.Add("get boost");
            return Fail ? null : new[] { Ac, Dc };
        }

        public bool SetCpuBoost(int value, out string err)
        {
            Calls.Add("set boost " + value);
            err = null;
            if (Fail) { err = "boost write failed"; return false; }
            Ac = value;
            Dc = value;
            return true;
        }

        public string GetOverlay()
        {
            Calls.Add("get overlay");
            return Fail ? null : Overlay;
        }

        public bool SetOverlay(string guid, out string err)
        {
            Calls.Add("set overlay " + guid);
            err = null;
            if (Fail) { err = "overlay write failed"; return false; }
            Overlay = guid;
            return true;
        }

        public List<string> Writes()
        {
            var l = new List<string>();
            foreach (string c in Calls) if (c.StartsWith("set ")) l.Add(c);
            return l;
        }
    }

    // Service manager stand-in with state: start types, which services run, and injectable failures.
    // A service that is not in Types does not exist.
    public class FakeServices : IServices
    {
        public readonly Dictionary<string, string> Types = new Dictionary<string, string>();
        public readonly List<string> RunningNames = new List<string>();
        public readonly List<string> Calls = new List<string>();      // every call, reads included, in order
        public readonly List<string> FailStop = new List<string>();   // stops the service, then throws before setting the type
        public readonly List<string> FailStart = new List<string>();  // throws before changing anything
        // name -> a service it depends on; starting it fails while that one is Disabled (like the service manager)
        public readonly Dictionary<string, string> DependsOn = new Dictionary<string, string>();

        public Dictionary<string, string> StartTypes(IEnumerable<string> names)
        {
            Calls.Add("query types");
            var d = new Dictionary<string, string>();
            foreach (string n in names) { string t; if (Types.TryGetValue(n, out t)) d[n] = t; }
            return d;
        }

        public List<string> Running(IEnumerable<string> names)
        {
            Calls.Add("query running");
            var l = new List<string>();
            foreach (string n in names) if (RunningNames.Contains(n)) l.Add(n);
            return l;
        }

        public void Stop(string name, string startType)
        {
            Calls.Add("stop " + name + " " + startType);
            Exists(name);
            RunningNames.Remove(name);
            if (FailStop.Contains(name)) throw new InvalidOperationException("access denied");
            Types[name] = startType;
        }

        public void Start(string name, string startType)
        {
            Calls.Add("start " + name + " " + startType);
            Exists(name);
            if (FailStart.Contains(name)) throw new InvalidOperationException("the service did not start");
            string dep;
            if (DependsOn.TryGetValue(name, out dep) && Types.ContainsKey(dep) && Types[dep] == "Disabled")
                throw new InvalidOperationException("a dependency service is disabled");
            Types[name] = startType;
            if (!RunningNames.Contains(name)) RunningNames.Add(name);
        }

        public void SetStartType(string name, string startType)
        {
            Calls.Add("type " + name + " " + startType);
            Exists(name);
            Types[name] = startType;
        }

        private void Exists(string name)
        {
            if (!Types.ContainsKey(name)) throw new InvalidOperationException("service " + name + " was not found");
        }

        // Mutating calls only (stop/start/type), for asserting what changed.
        public List<string> Changes()
        {
            var l = new List<string>();
            foreach (string c in Calls) if (!c.StartsWith("query ")) l.Add(c);
            return l;
        }
    }
}
