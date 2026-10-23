using System;
using Overdrive.Helper.GHelper;

namespace Overdrive.Helper
{
    // The real IAcpi: the ATKACPI device through the ported G-Helper AsusACPI.
    // The device is opened once (as G-Helper does); if that failed, every call throws with the reason.
    public class AcpiHardware : IAcpi, IDisposable
    {
        private readonly AsusACPI acpi;

        public AcpiHardware()
        {
            acpi = new AsusACPI();
        }

        public int DeviceGet(uint id)
        {
            EnsureConnected();
            return acpi.DeviceGet(id);
        }

        public int DeviceSet(uint id, int value)
        {
            EnsureConnected();
            return acpi.DeviceSet(id, value);
        }

        public byte[] GetFanCurve(int fan, int mode)
        {
            CheckFan(fan);
            EnsureConnected();
            return acpi.GetFanCurve(AsusFan.CPU, mode);
        }

        public int SetFanCurve(int fan, byte[] curve)
        {
            CheckFan(fan);
            if (curve == null) throw new ArgumentNullException("curve");
            EnsureConnected();
            // AsusACPI.SetFanCurve clamps the speed bytes in place; keep the caller's array untouched.
            return acpi.SetFanCurve(AsusFan.CPU, (byte[])curve.Clone());
        }

        public void Dispose()
        {
            acpi.Close();
        }

        private void EnsureConnected()
        {
            if (!acpi.IsConnected())
                throw new InvalidOperationException("Can't open the ASUS ACPI device (\\\\.\\ATKACPI): "
                    + (acpi.ConnectError() ?? "unknown error") + ".");
        }

        private static void CheckFan(int fan)
        {
            if (fan != DeviceIds.FanCpu) throw new ArgumentException("Only the CPU fan (0) is supported.");
        }
    }
}
