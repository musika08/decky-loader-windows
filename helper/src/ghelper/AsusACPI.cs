// G-Helper - https://github.com/seerge/g-helper - app/AsusACPI.cs
// Copyright seerge and contributors, licensed GPL-3.0. Commit c09017453d9ed0c52261b67a48d02bd55232e6ac.
// (The upstream file has no header of its own; these lines record its origin.)
// Modified for Overdrive (ported to C# 5 / .NET Framework 4.8), 2026
//
// Ported subset only: opening \\.\ATKACPI, the DSTS/DEVS IOCTL call, DeviceGet/DeviceSet,
// Get/SetFanCurve for the CPU and GPU fans, and the device ids Overdrive uses. Changes from upstream:
// C# 5 syntax; no logging or AppConfig (fan_scale fixed at its default 100); only the CPU and GPU fans (no Mid/XGM);
// the mode swap of GetFanCurve moved into FanCurveMode so it can be tested without a device;
// FanFromRaw is GetFan's conversion as a pure function; the Win32 error of a failed open is kept.
// Temp_GPU (0x00120097) is ported as a read-only DSTS id for the GPU temperature fallback.
// Nothing touching the battery charge limit (0x00120057) is ported.

using System;
using System.Linq;
using System.Runtime.InteropServices;

namespace Overdrive.Helper.GHelper
{
    public enum AsusFan
    {
        CPU = 0,
        GPU = 1
    }

    public class AsusACPI
    {

        const string FILE_NAME = @"\\.\\ATKACPI";
        const uint CONTROL_CODE = 0x0022240C;

        const uint DSTS = 0x53545344;
        const uint DEVS = 0x53564544;

        public const uint CPU_Fan = 0x00110013;
        public const uint GPU_Fan = 0x00110014;

        public const uint PerformanceMode = 0x00120075; // Performance modes

        public const uint DevsCPUFanCurve = 0x00110024;
        public const uint DevsGPUFanCurve = 0x00110025;

        public const int Temp_CPU = 0x00120094;
        public const int Temp_GPU = 0x00120097;

        public const int PPT_APUA0 = 0x001200A0;  // sPPT (slow boost limit) / PL2
        public const int PPT_APUA3 = 0x001200A3;  // SPL (sustained limit) / PL1
        public const int PPT_APUC1 = 0x001200C1;  // fPPT (fast boost limit)

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern IntPtr CreateFile(
            string lpFileName,
            uint dwDesiredAccess,
            uint dwShareMode,
            IntPtr lpSecurityAttributes,
            uint dwCreationDisposition,
            uint dwFlagsAndAttributes,
            IntPtr hTemplateFile
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool DeviceIoControl(
            IntPtr hDevice,
            uint dwIoControlCode,
            byte[] lpInBuffer,
            uint nInBufferSize,
            byte[] lpOutBuffer,
            uint nOutBufferSize,
            ref uint lpBytesReturned,
            IntPtr lpOverlapped
        );

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr hObject);

        private const uint GENERIC_READ = 0x80000000;
        private const uint GENERIC_WRITE = 0x40000000;
        private const uint OPEN_EXISTING = 3;
        private const uint FILE_ATTRIBUTE_NORMAL = 0x80;
        private const uint FILE_SHARE_READ = 1;
        private const uint FILE_SHARE_WRITE = 2;

        private IntPtr handle;

        private bool _connected = false;
        private string _connectError = null;

        public bool IsConnected()
        {
            return _connected;
        }

        // Why the device could not be opened (null when connected).
        public string ConnectError()
        {
            return _connectError;
        }

        public AsusACPI()
        {
            try
            {
                handle = CreateFile(
                    FILE_NAME,
                    GENERIC_READ | GENERIC_WRITE,
                    FILE_SHARE_READ | FILE_SHARE_WRITE,
                    IntPtr.Zero,
                    OPEN_EXISTING,
                    FILE_ATTRIBUTE_NORMAL,
                    IntPtr.Zero
                );

                _connected = handle != new IntPtr(-1);
                if (!_connected) _connectError = "Win32 error " + Marshal.GetLastWin32Error();

            }
            catch (Exception ex)
            {
                _connectError = ex.Message;
            }
        }

        public void Control(uint dwIoControlCode, byte[] lpInBuffer, byte[] lpOutBuffer)
        {

            uint lpBytesReturned = 0;
            DeviceIoControl(
                handle,
                dwIoControlCode,
                lpInBuffer,
                (uint)lpInBuffer.Length,
                lpOutBuffer,
                (uint)lpOutBuffer.Length,
                ref lpBytesReturned,
                IntPtr.Zero
            );
        }

        public void Close()
        {
            if (_connected) CloseHandle(handle);
            _connected = false;
        }


        protected byte[] CallMethod(uint MethodID, byte[] args)
        {
            byte[] acpiBuf = new byte[8 + args.Length];
            byte[] outBuffer = new byte[16];

            BitConverter.GetBytes((uint)MethodID).CopyTo(acpiBuf, 0);
            BitConverter.GetBytes((uint)args.Length).CopyTo(acpiBuf, 4);
            Array.Copy(args, 0, acpiBuf, 8, args.Length);

            Control(CONTROL_CODE, acpiBuf, outBuffer);

            return outBuffer;

        }

        public int DeviceSet(uint DeviceID, int Status)
        {
            byte[] args = new byte[8];
            BitConverter.GetBytes((uint)DeviceID).CopyTo(args, 0);
            BitConverter.GetBytes((uint)Status).CopyTo(args, 4);

            byte[] status = CallMethod(DEVS, args);
            int result = BitConverter.ToInt32(status, 0);

            return result;
        }


        public int DeviceSet(uint DeviceID, byte[] Params)
        {
            byte[] args = new byte[4 + Params.Length];
            BitConverter.GetBytes((uint)DeviceID).CopyTo(args, 0);
            Params.CopyTo(args, 4);

            byte[] status = CallMethod(DEVS, args);

            return BitConverter.ToInt32(status, 0);
        }

        public int DeviceGet(uint DeviceID)
        {
            byte[] args = new byte[8];
            BitConverter.GetBytes((uint)DeviceID).CopyTo(args, 0);
            byte[] status = CallMethod(DSTS, args);

            return BitConverter.ToInt32(status, 0) - 65536;

        }

        public byte[] DeviceGetBuffer(uint DeviceID, uint Status = 0)
        {
            byte[] args = new byte[8];
            BitConverter.GetBytes((uint)DeviceID).CopyTo(args, 0);
            BitConverter.GetBytes((uint)Status).CopyTo(args, 4);

            return CallMethod(DSTS, args);
        }

        public int GetFan(AsusFan device)
        {
            return FanFromRaw(DeviceGet(FanEndpoint(device)));
        }

        // Upstream GetFan's endpoint switch (GPU -> GPU_Fan, anything else -> CPU_Fan; Mid not ported).
        public static uint FanEndpoint(AsusFan device)
        {
            switch (device)
            {
                case AsusFan.GPU: return GPU_Fan;
                default: return CPU_Fan;
            }
        }

        // Upstream SetFanCurve/GetFanCurve's device switch (GPU -> DevsGPUFanCurve, anything else -> DevsCPUFanCurve).
        public static uint FanCurveDevice(AsusFan device)
        {
            switch (device)
            {
                case AsusFan.GPU: return DevsGPUFanCurve;
                default: return DevsCPUFanCurve;
            }
        }

        // Body of upstream GetFan: raw is DeviceGet(CPU_Fan or GPU_Fan); -1 = no reading.
        public static int FanFromRaw(int raw)
        {
            int fan = raw & 0xFFFF;
            if (fan > 120 || (fan == 0 && raw < 0)) fan = -1;
            return fan;
        }

        // Note: like upstream, this clamps the speed bytes of the array it is given.
        public int SetFanCurve(AsusFan device, byte[] curve)
        {

            if (curve.Length != 16) return -1;
            if (curve.All(singleByte => singleByte == 0)) return -1;

            int result;

            int fanScale = 100;   // upstream: AppConfig.Get("fan_scale", 100)

            for (int i = 8; i < curve.Length; i++) curve[i] = (byte)(Math.Max((byte)0, Math.Min((byte)100, curve[i])) * fanScale / 100);

            result = DeviceSet(FanCurveDevice(device), curve);

            return result;
        }

        // because it's asus, and modes are swapped here
        public static uint FanCurveMode(int mode)
        {
            uint fan_mode;

            switch (mode)
            {
                case 1: fan_mode = 2; break;
                case 2: fan_mode = 1; break;
                default: fan_mode = 0; break;
            }

            return fan_mode;
        }

        public byte[] GetFanCurve(AsusFan device, int mode = 0)
        {
            uint fan_mode = FanCurveMode(mode);

            byte[] result;

            result = DeviceGetBuffer(FanCurveDevice(device), fan_mode);

            return result;

        }

        public static bool IsInvalidCurve(byte[] curve)
        {
            return curve.Length != 16 || IsEmptyCurve(curve);
        }

        public static bool IsEmptyCurve(byte[] curve)
        {
            return curve.All(singleByte => singleByte == 0);
        }

    }
}
