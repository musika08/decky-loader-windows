using System;
using System.Collections.Generic;
using Overdrive.Helper.GHelper;
using Ally.Shared;

namespace Overdrive.Helper
{
    // The real IAcpi: the ATKACPI device through the ported G-Helper AsusACPI.
    // The device is opened once (as G-Helper does); if that failed, every call throws with the reason.
    // Writes are limited to an allow-list of device ids, checked before the device is touched, so ids such as the
    // battery charge limit (0x00120057, owned by Windows Quick Settings) can never be written by this helper.
    public class AcpiHardware : IAcpi, IDisposable
    {
        // Performance mode, SPL, sPPT, fPPT, and the CPU and GPU fan curves (written by SetFanCurve).
        public static readonly IList<uint> WritableIds = Array.AsReadOnly(new[]
        {
            DeviceIds.PerformanceMode, DeviceIds.Spl, DeviceIds.Sppt, DeviceIds.Fppt, DeviceIds.CpuFanCurve,
            DeviceIds.GpuFanCurve
        });

        private readonly AsusACPI acpi;

        public AcpiHardware() : this(new AsusACPI()) { }

        // For tests: null means "no device", so any call that passes the checks fails with "Can't open ...".
        internal AcpiHardware(AsusACPI device)
        {
            acpi = device;
        }

        public static bool IsWritable(uint id)
        {
            return WritableIds.Contains(id);
        }

        public int DeviceGet(uint id)
        {
            EnsureConnected();
            return acpi.DeviceGet(id);
        }

        public int DeviceSet(uint id, int value)
        {
            CheckWritable(id);
            EnsureConnected();
            return acpi.DeviceSet(id, value);
        }

        public byte[] GetFanCurve(int fan, int mode)
        {
            CheckFan(fan);
            EnsureConnected();
            return acpi.GetFanCurve((AsusFan)fan, mode);
        }

        public int SetFanCurve(int fan, byte[] curve)
        {
            CheckFan(fan);
            if (curve == null) throw new ArgumentNullException("curve");
            // AsusACPI.SetFanCurve writes DevsCPUFanCurve or DevsGPUFanCurve; keep it under the same allow-list.
            CheckWritable(AsusACPI.FanCurveDevice((AsusFan)fan));
            EnsureConnected();
            // AsusACPI.SetFanCurve clamps the speed bytes in place; keep the caller's array untouched.
            return acpi.SetFanCurve((AsusFan)fan, (byte[])curve.Clone());
        }

        public void Dispose()
        {
            if (acpi != null) acpi.Close();
        }

        private static void CheckWritable(uint id)
        {
            if (!IsWritable(id))
                throw new InvalidOperationException("Refused: device id 0x" + id.ToString("X8") + " is not writable by Overdrive.");
        }

        private void EnsureConnected()
        {
            if (acpi == null || !acpi.IsConnected())
                throw new InvalidOperationException("Can't open the ASUS ACPI device (\\\\.\\ATKACPI): "
                    + (acpi == null ? "no device" : (acpi.ConnectError() ?? "unknown error")) + ".");
        }

        private static void CheckFan(int fan)
        {
            if (fan != DeviceIds.FanCpu && fan != DeviceIds.FanGpu)
                throw new ArgumentException("Only the CPU fan (0) and the GPU fan (1) are supported.");
        }
    }
}
