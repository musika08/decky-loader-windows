// G-Helper - https://github.com/seerge/g-helper - app/USB/AsusHid.cs
// Copyright seerge and contributors, licensed GPL-3.0. Commit c09017453d9ed0c52261b67a48d02bd55232e6ac.
// (The upstream file has no header of its own; these lines record its origin.)
// Modified for Overdrive (ported to C# 5 / .NET Framework 4.8), 2026
//
// Ported subset: ASUS_ID, INPUT_ID, MAIN_AURA_PIDS, and the device choice of FindDevices / FindHidStream for INPUT_ID
// (VID 0x0B05, a PID in MAIN_AURA_PIDS, a feature report length above 0, the report descriptor has feature report
// 0x5A), and WriteInput's write (the report padded to the feature report length, sent with SetFeature).
// Changes from upstream: HidSharp is not available in the in-box .NET Framework, so the HID layer (Win32HidBus) calls
// the Win32 SetupAPI / hid.dll functions directly and reads the feature report ids from the collection's preparsed
// data (HidP_GetValueCaps / HidP_GetButtonCaps) instead of HidSharp's report descriptor parser; only the first
// matching collection is written (upstream WriteInput writes every match, FindHidStream opens the first); devices are
// listed with query-only handles and only the matching collection is opened for writing; the model-specific choices
// (Z13, S17, DUO) are left out; errors are returned instead of logged.

using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace Overdrive.Helper
{
    public sealed class AsusHid : IAsusHid
    {
        public const int ASUS_ID = 0x0b05;
        public const byte INPUT_ID = 0x5a;

        // G-Helper AsusHid.MAIN_AURA_PIDS, copied as is (0x1854 is listed twice upstream).
        public static readonly int[] MAIN_AURA_PIDS = { 0x1a30, 0x1854, 0x1869, 0x1866, 0x19b6, 0x1822, 0x1837, 0x1854, 0x184a, 0x183d, 0x8502, 0x1807, 0x17e0, 0x1abe, 0x1b4c, 0x1b6e, 0x1b2c, 0x8854, 0x1CE7, 0x1ce6, 0x1bf2, 0x1bf3, 0x1cd7, 0x1cd8 };

        public const string NotFound = "Controller not found";

        private readonly IHidBus bus;

        public AsusHid() : this(new Win32HidBus()) { }

        public AsusHid(IHidBus bus) { this.bus = bus; }

        // The first collection that is the ASUS controller's input collection, or null.
        public static HidCollection Select(IEnumerable<HidCollection> collections)
        {
            if (collections == null) return null;
            foreach (HidCollection c in collections)
            {
                if (c == null || c.Vid != ASUS_ID || Array.IndexOf(MAIN_AURA_PIDS, c.Pid) < 0) continue;
                if (c.FeatureLength <= 0 || c.FeatureReportIds == null) continue;
                if (Array.IndexOf(c.FeatureReportIds, INPUT_ID) < 0) continue;
                return c;
            }
            return null;
        }

        public HidInfo Find()
        {
            HidCollection c = Select(bus.List());
            var info = new HidInfo();
            if (c == null) return info;
            info.Found = true;
            info.Pid = c.Pid;
            info.FeatureLength = c.FeatureLength;
            return info;
        }

        // The collection is looked up again for every write (as upstream WriteInput does), so a controller that was
        // re-enumerated after sleep is still found, and nothing else can be written.
        public bool SetFeature(byte[] report, out string err)
        {
            err = null;
            if (report == null || report.Length == 0 || report[0] != INPUT_ID)
            {
                err = "Refused: only feature report 0x5A is sent to the controller.";
                return false;
            }
            HidCollection c = Select(bus.List());
            if (c == null) { err = NotFound; return false; }
            if (report.Length != c.FeatureLength)
            {
                err = "Refused: the controller report must be " + c.FeatureLength + " bytes, not " + report.Length + ".";
                return false;
            }
            return bus.SetFeature(c.Path, report, out err);
        }
    }

    // Raw Win32 HID (SetupAPI and hid.dll). x64 only, as the helper is built /platform:x64.
    public sealed class Win32HidBus : IHidBus
    {
        private const uint DIGCF_PRESENT = 0x02;
        private const uint DIGCF_DEVICEINTERFACE = 0x10;
        private const uint GENERIC_READ = 0x80000000;
        private const uint GENERIC_WRITE = 0x40000000;
        private const uint FILE_SHARE_READ = 0x01;
        private const uint FILE_SHARE_WRITE = 0x02;
        private const uint OPEN_EXISTING = 3;
        private const int ERROR_NO_MORE_ITEMS = 259;
        private const int HIDP_STATUS_SUCCESS = 0x00110000;
        private const int HidP_Feature = 2;
        // HIDP_BUTTON_CAPS and HIDP_VALUE_CAPS are both 72 bytes with ReportID (UCHAR) at offset 2, after UsagePage.
        private const int CapsSize = 72;
        private const int CapsReportIdOffset = 2;
        private static readonly IntPtr InvalidHandle = new IntPtr(-1);

        // Marshalled sizes of SP_DEVICE_INTERFACE_DATA, HIDD_ATTRIBUTES and HIDP_CAPS, and the
        // SP_DEVICE_INTERFACE_DETAIL_DATA_W cbSize used, for the layout test (x64: 32, 12, 64, 8).
        public static int[] LayoutSizes()
        {
            return new[] { Marshal.SizeOf(typeof(SP_DEVICE_INTERFACE_DATA)), Marshal.SizeOf(typeof(HIDD_ATTRIBUTES)),
                Marshal.SizeOf(typeof(HIDP_CAPS)), DetailCbSize };
        }

        private static int DetailCbSize { get { return IntPtr.Size == 8 ? 8 : 6; } }

        [StructLayout(LayoutKind.Sequential)]
        private struct SP_DEVICE_INTERFACE_DATA
        {
            public int cbSize;
            public Guid InterfaceClassGuid;
            public int Flags;
            public IntPtr Reserved;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct HIDD_ATTRIBUTES
        {
            public int Size;
            public ushort VendorID;
            public ushort ProductID;
            public ushort VersionNumber;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct HIDP_CAPS
        {
            public ushort Usage;
            public ushort UsagePage;
            public ushort InputReportByteLength;
            public ushort OutputReportByteLength;
            public ushort FeatureReportByteLength;
            [MarshalAs(UnmanagedType.ByValArray, SizeConst = 17)]
            public ushort[] Reserved;
            public ushort NumberLinkCollectionNodes;
            public ushort NumberInputButtonCaps;
            public ushort NumberInputValueCaps;
            public ushort NumberInputDataIndices;
            public ushort NumberOutputButtonCaps;
            public ushort NumberOutputValueCaps;
            public ushort NumberOutputDataIndices;
            public ushort NumberFeatureButtonCaps;
            public ushort NumberFeatureValueCaps;
            public ushort NumberFeatureDataIndices;
        }

        [DllImport("hid.dll")]
        private static extern void HidD_GetHidGuid(out Guid hidGuid);

        [DllImport("hid.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.U1)]
        private static extern bool HidD_GetAttributes(SafeFileHandle device, ref HIDD_ATTRIBUTES attributes);

        [DllImport("hid.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.U1)]
        private static extern bool HidD_GetPreparsedData(SafeFileHandle device, out IntPtr preparsedData);

        [DllImport("hid.dll")]
        [return: MarshalAs(UnmanagedType.U1)]
        private static extern bool HidD_FreePreparsedData(IntPtr preparsedData);

        [DllImport("hid.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.U1)]
        private static extern bool HidD_SetFeature(SafeFileHandle device, byte[] reportBuffer, int reportBufferLength);

        [DllImport("hid.dll")]
        private static extern int HidP_GetCaps(IntPtr preparsedData, out HIDP_CAPS capabilities);

        [DllImport("hid.dll")]
        private static extern int HidP_GetValueCaps(int reportType, [Out] byte[] valueCaps, ref ushort valueCapsLength, IntPtr preparsedData);

        [DllImport("hid.dll")]
        private static extern int HidP_GetButtonCaps(int reportType, [Out] byte[] buttonCaps, ref ushort buttonCapsLength, IntPtr preparsedData);

        [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr SetupDiGetClassDevs(ref Guid classGuid, IntPtr enumerator, IntPtr hwndParent, uint flags);

        [DllImport("setupapi.dll", SetLastError = true)]
        private static extern bool SetupDiEnumDeviceInterfaces(IntPtr deviceInfoSet, IntPtr deviceInfoData,
            ref Guid interfaceClassGuid, uint memberIndex, ref SP_DEVICE_INTERFACE_DATA deviceInterfaceData);

        [DllImport("setupapi.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool SetupDiGetDeviceInterfaceDetail(IntPtr deviceInfoSet,
            ref SP_DEVICE_INTERFACE_DATA deviceInterfaceData, IntPtr deviceInterfaceDetailData,
            int deviceInterfaceDetailDataSize, out int requiredSize, IntPtr deviceInfoData);

        [DllImport("setupapi.dll", SetLastError = true)]
        private static extern bool SetupDiDestroyDeviceInfoList(IntPtr deviceInfoSet);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFile(string fileName, uint desiredAccess, uint shareMode,
            IntPtr securityAttributes, uint creationDisposition, uint flagsAndAttributes, IntPtr templateFile);

        // Lists every present HID collection. Each is opened query-only (access 0), which is enough for the
        // attributes and the preparsed data; a collection that cannot be opened or read is left out.
        public List<HidCollection> List()
        {
            var result = new List<HidCollection>();
            foreach (string path in DevicePaths())
            {
                HidCollection c = Describe(path);
                if (c != null) result.Add(c);
            }
            return result;
        }

        // Opens the one path read/write and sends the report (the caller has already padded it).
        public bool SetFeature(string path, byte[] report, out string err)
        {
            err = null;
            using (SafeFileHandle h = CreateFile(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                IntPtr.Zero, OPEN_EXISTING, 0, IntPtr.Zero))
            {
                if (h.IsInvalid)
                {
                    err = "Could not open the controller (Windows error " + Marshal.GetLastWin32Error() + ").";
                    return false;
                }
                if (!HidD_SetFeature(h, report, report.Length))
                {
                    err = "The controller refused the report (Windows error " + Marshal.GetLastWin32Error() + ").";
                    return false;
                }
            }
            return true;
        }

        private static List<string> DevicePaths()
        {
            var paths = new List<string>();
            Guid hidGuid;
            HidD_GetHidGuid(out hidGuid);
            IntPtr set = SetupDiGetClassDevs(ref hidGuid, IntPtr.Zero, IntPtr.Zero, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
            if (set == InvalidHandle || set == IntPtr.Zero) return paths;
            try
            {
                for (uint i = 0; ; i++)
                {
                    var data = new SP_DEVICE_INTERFACE_DATA();
                    data.cbSize = Marshal.SizeOf(typeof(SP_DEVICE_INTERFACE_DATA));
                    if (!SetupDiEnumDeviceInterfaces(set, IntPtr.Zero, ref hidGuid, i, ref data))
                    {
                        if (Marshal.GetLastWin32Error() == ERROR_NO_MORE_ITEMS) break;
                        if (i > 4096) break;   // never loop forever on an unexpected error
                        continue;
                    }
                    string path = DetailPath(set, ref data);
                    if (path != null) paths.Add(path);
                }
            }
            finally
            {
                SetupDiDestroyDeviceInfoList(set);
            }
            return paths;
        }

        // SP_DEVICE_INTERFACE_DETAIL_DATA_W: DWORD cbSize, then WCHAR DevicePath[]. cbSize is the size of the fixed
        // part as the struct is declared: 8 on x64 (4 + 2, padded to the 8-byte pack), 6 on x86.
        private static string DetailPath(IntPtr set, ref SP_DEVICE_INTERFACE_DATA data)
        {
            int required;
            SetupDiGetDeviceInterfaceDetail(set, ref data, IntPtr.Zero, 0, out required, IntPtr.Zero);
            if (required <= 4) return null;
            IntPtr buf = Marshal.AllocHGlobal(required);
            try
            {
                Marshal.WriteInt32(buf, DetailCbSize);
                if (!SetupDiGetDeviceInterfaceDetail(set, ref data, buf, required, out required, IntPtr.Zero)) return null;
                return Marshal.PtrToStringUni(new IntPtr(buf.ToInt64() + 4));
            }
            finally
            {
                Marshal.FreeHGlobal(buf);
            }
        }

        private static HidCollection Describe(string path)
        {
            using (SafeFileHandle h = CreateFile(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, IntPtr.Zero, OPEN_EXISTING, 0, IntPtr.Zero))
            {
                if (h.IsInvalid) return null;
                var attr = new HIDD_ATTRIBUTES();
                attr.Size = Marshal.SizeOf(typeof(HIDD_ATTRIBUTES));
                if (!HidD_GetAttributes(h, ref attr)) return null;
                var c = new HidCollection { Path = path, Vid = attr.VendorID, Pid = attr.ProductID, FeatureReportIds = new byte[0] };
                // Only the controller's collections need their feature reports read.
                if (c.Vid != AsusHid.ASUS_ID) return c;

                IntPtr pp;
                if (!HidD_GetPreparsedData(h, out pp)) return c;
                try
                {
                    HIDP_CAPS caps;
                    if (HidP_GetCaps(pp, out caps) != HIDP_STATUS_SUCCESS) return c;
                    c.FeatureLength = caps.FeatureReportByteLength;
                    var ids = new List<byte>();
                    AddReportIds(ids, caps.NumberFeatureValueCaps, pp, true);
                    AddReportIds(ids, caps.NumberFeatureButtonCaps, pp, false);
                    c.FeatureReportIds = ids.ToArray();
                    return c;
                }
                finally
                {
                    HidD_FreePreparsedData(pp);
                }
            }
        }

        private static void AddReportIds(List<byte> ids, ushort count, IntPtr pp, bool values)
        {
            if (count == 0) return;
            var buf = new byte[count * CapsSize];
            ushort n = count;
            int status = values ? HidP_GetValueCaps(HidP_Feature, buf, ref n, pp) : HidP_GetButtonCaps(HidP_Feature, buf, ref n, pp);
            if (status != HIDP_STATUS_SUCCESS) return;
            for (int i = 0; i < n && i < count; i++)
            {
                byte id = buf[i * CapsSize + CapsReportIdOffset];
                if (!ids.Contains(id)) ids.Add(id);
            }
        }
    }
}
