using System;
using System.Collections.Generic;

namespace Overdrive.Helper.Tests
{
    // In-memory stand-ins for the hardware interfaces. Nothing here touches a device, a driver or a service.
    public class FakeAcpi : IAcpi
    {
        public readonly Dictionary<uint, int> Values = new Dictionary<uint, int>();
        public readonly List<string> Calls = new List<string>();
        public readonly Dictionary<int, byte[]> Curves = new Dictionary<int, byte[]>();
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
            return Curves.TryGetValue(mode, out c) ? (byte[])c.Clone() : new byte[16];
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
        public readonly List<int> Sets = new List<int>();

        public int GetFpsLimit() { return Missing ? -1 : Limit; }

        public int SetFpsLimit(int fps)
        {
            if (Missing) return -1;
            Sets.Add(fps);
            Limit = fps;
            return 1;
        }
    }

    public class FakeServices : IServices
    {
        public readonly Dictionary<string, string> Types = new Dictionary<string, string>();
        public readonly List<string> RunningNames = new List<string>();
        public readonly List<string> Calls = new List<string>();

        public Dictionary<string, string> StartTypes(IEnumerable<string> names)
        {
            var d = new Dictionary<string, string>();
            foreach (string n in names) { string t; if (Types.TryGetValue(n, out t)) d[n] = t; }
            return d;
        }

        public List<string> Running(IEnumerable<string> names)
        {
            var l = new List<string>();
            foreach (string n in names) if (RunningNames.Contains(n)) l.Add(n);
            return l;
        }

        public void Stop(string name, string startType) { Calls.Add("stop " + name + " " + startType); }
        public void Start(string name, string startType) { Calls.Add("start " + name + " " + startType); }
    }
}
