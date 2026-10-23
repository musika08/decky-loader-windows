// G-Helper - https://github.com/seerge/g-helper - app/Mode/PowerNative.cs
// Copyright seerge and contributors, licensed GPL-3.0. Commit c09017453d9ed0c52261b67a48d02bd55232e6ac.
// (The upstream file has no header of its own; these lines record its origin.)
// Modified for Overdrive (ported to C# 5 / .NET Framework 4.8), 2026
//
// Ported subset: the PowrProf P/Invokes and GetCPUBoost/SetCPUBoost, GetPowerMode/SetPowerMode (overlay part only).
// Changes from upstream: the scheme pointer from PowerGetActiveScheme is freed with LocalFree; the value index is read
// into a uint (upstream reads into an IntPtr); errors are returned instead of ignored; the overlay is set without
// upstream's power plan and Battery Saver handling.

using System;
using System.Runtime.InteropServices;

namespace Overdrive.Helper
{
    public sealed class WinPower : IWinPower
    {
        [DllImport("PowrProf.dll", CharSet = CharSet.Unicode)]
        private static extern uint PowerWriteDCValueIndex(IntPtr RootPowerKey,
            [MarshalAs(UnmanagedType.LPStruct)] Guid SchemeGuid,
            [MarshalAs(UnmanagedType.LPStruct)] Guid SubGroupOfPowerSettingsGuid,
            [MarshalAs(UnmanagedType.LPStruct)] Guid PowerSettingGuid,
            int DcValueIndex);

        [DllImport("PowrProf.dll", CharSet = CharSet.Unicode)]
        private static extern uint PowerWriteACValueIndex(IntPtr RootPowerKey,
            [MarshalAs(UnmanagedType.LPStruct)] Guid SchemeGuid,
            [MarshalAs(UnmanagedType.LPStruct)] Guid SubGroupOfPowerSettingsGuid,
            [MarshalAs(UnmanagedType.LPStruct)] Guid PowerSettingGuid,
            int AcValueIndex);

        [DllImport("PowrProf.dll", CharSet = CharSet.Unicode)]
        private static extern uint PowerReadACValueIndex(IntPtr RootPowerKey,
            [MarshalAs(UnmanagedType.LPStruct)] Guid SchemeGuid,
            [MarshalAs(UnmanagedType.LPStruct)] Guid SubGroupOfPowerSettingsGuid,
            [MarshalAs(UnmanagedType.LPStruct)] Guid PowerSettingGuid,
            out uint AcValueIndex);

        [DllImport("PowrProf.dll", CharSet = CharSet.Unicode)]
        private static extern uint PowerReadDCValueIndex(IntPtr RootPowerKey,
            [MarshalAs(UnmanagedType.LPStruct)] Guid SchemeGuid,
            [MarshalAs(UnmanagedType.LPStruct)] Guid SubGroupOfPowerSettingsGuid,
            [MarshalAs(UnmanagedType.LPStruct)] Guid PowerSettingGuid,
            out uint DcValueIndex);

        [DllImport("PowrProf.dll", CharSet = CharSet.Unicode)]
        private static extern uint PowerSetActiveScheme(IntPtr RootPowerKey,
            [MarshalAs(UnmanagedType.LPStruct)] Guid SchemeGuid);

        [DllImport("PowrProf.dll", CharSet = CharSet.Unicode)]
        private static extern uint PowerGetActiveScheme(IntPtr UserPowerKey, out IntPtr ActivePolicyGuid);

        [DllImport("powrprof.dll", EntryPoint = "PowerGetEffectiveOverlayScheme")]
        private static extern uint PowerGetEffectiveOverlayScheme(out Guid EffectiveOverlayGuid);

        [DllImport("powrprof.dll", EntryPoint = "PowerSetActiveOverlayScheme")]
        private static extern uint PowerSetActiveOverlayScheme(Guid OverlaySchemeGuid);

        [DllImport("kernel32.dll")]
        private static extern IntPtr LocalFree(IntPtr hMem);

        // PowerNative GUID_CPU and GUID_BOOST.
        private static readonly Guid GuidCpu = new Guid("54533251-82be-4824-96c1-47b60b740d00");
        private static readonly Guid GuidBoost = new Guid("be337238-0d82-4146-a960-4f3749d470c7");

        // PowerNative GetActiveScheme, with the buffer PowerGetActiveScheme allocates freed with LocalFree.
        private static bool TryGetActiveScheme(out Guid scheme, out string err)
        {
            scheme = Guid.Empty;
            err = null;
            IntPtr p;
            uint hr = PowerGetActiveScheme(IntPtr.Zero, out p);
            if (hr != 0 || p == IntPtr.Zero)
            {
                err = "Windows would not report the active power plan (error " + hr + ").";
                return false;
            }
            try { scheme = (Guid)Marshal.PtrToStructure(p, typeof(Guid)); }
            finally { LocalFree(p); }
            return true;
        }

        // PowerNative GetCPUBoost reads the AC value; the DC value is read the same way.
        public int[] GetCpuBoost()
        {
            Guid scheme;
            string err;
            if (!TryGetActiveScheme(out scheme, out err)) return null;
            uint ac, dc;
            if (PowerReadACValueIndex(IntPtr.Zero, scheme, GuidCpu, GuidBoost, out ac) != 0) return null;
            if (PowerReadDCValueIndex(IntPtr.Zero, scheme, GuidCpu, GuidBoost, out dc) != 0) return null;
            return new[] { (int)ac, (int)dc };
        }

        // PowerNative SetCPUBoost: AC write, PowerSetActiveScheme, DC write, PowerSetActiveScheme.
        public bool SetCpuBoost(int value, out string err)
        {
            Guid scheme;
            if (!TryGetActiveScheme(out scheme, out err)) return false;
            uint hr = PowerWriteACValueIndex(IntPtr.Zero, scheme, GuidCpu, GuidBoost, value);
            if (hr != 0) { err = "Windows refused the AC CPU Boost value (error " + hr + ")."; return false; }
            hr = PowerSetActiveScheme(IntPtr.Zero, scheme);
            if (hr != 0) { err = "Windows would not apply the power plan (error " + hr + ")."; return false; }
            hr = PowerWriteDCValueIndex(IntPtr.Zero, scheme, GuidCpu, GuidBoost, value);
            if (hr != 0) { err = "Windows refused the DC CPU Boost value (error " + hr + ")."; return false; }
            hr = PowerSetActiveScheme(IntPtr.Zero, scheme);
            if (hr != 0) { err = "Windows would not apply the power plan (error " + hr + ")."; return false; }
            err = null;
            return true;
        }

        // PowerNative GetPowerMode (overlay part).
        public string GetOverlay()
        {
            Guid g;
            if (PowerGetEffectiveOverlayScheme(out g) != 0) return null;
            return g.ToString("D");
        }

        // PowerNative SetPowerMode (overlay part): PowerSetActiveOverlayScheme.
        public bool SetOverlay(string guid, out string err)
        {
            err = null;
            Guid g;
            if (!Guid.TryParse(guid, out g)) { err = "Not a power mode GUID."; return false; }
            uint hr = PowerSetActiveOverlayScheme(g);
            if (hr != 0) { err = "Windows refused the power mode (error " + hr + ")."; return false; }
            return true;
        }
    }
}
