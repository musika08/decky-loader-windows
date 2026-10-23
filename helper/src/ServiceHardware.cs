using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.ServiceProcess;
using Microsoft.Win32;

namespace Overdrive.Helper
{
    // The real IServices: the Windows service manager, for the Armoury Crate services only.
    // G-Helper (app/Helpers/ProcessHelper.cs StopDisableService/StartEnableService) runs PowerShell
    // `Stop-Service -Force | Set-Service -StartupType X` and `Set-Service -Status running`. Overdrive does the same
    // steps in-process: ServiceController for stop/start/state, ChangeServiceConfig for the start type. No shell,
    // no command strings, and every name is checked against ArmouryServices before anything is opened.
    public class ServiceHardware : IServices
    {
        private static readonly TimeSpan Wait = TimeSpan.FromSeconds(30);

        public Dictionary<string, string> StartTypes(IEnumerable<string> names)
        {
            var d = new Dictionary<string, string>();
            foreach (KeyValuePair<string, ServiceController> kv in Find(names))
            {
                using (ServiceController sc = kv.Value)
                {
                    ServiceStartMode mode = sc.StartType;
                    string type = mode.ToString();
                    if (mode == ServiceStartMode.Automatic && IsDelayed(kv.Key)) type = "AutomaticDelayedStart";
                    d[kv.Key] = type;
                }
            }
            return d;
        }

        // Running = any state but Stopped, as G-Helper's GetRunningServices (State != "Stopped").
        public List<string> Running(IEnumerable<string> names)
        {
            var l = new List<string>();
            foreach (KeyValuePair<string, ServiceController> kv in Find(names))
            {
                using (ServiceController sc = kv.Value)
                {
                    if (sc.Status != ServiceControllerStatus.Stopped) l.Add(kv.Key);
                }
            }
            return l;
        }

        // Stop (dependent services too, like Stop-Service -Force), then set the start type: G-Helper's order.
        public void Stop(string name, string startType)
        {
            Check(name, startType);
            using (var sc = new ServiceController(name))
            {
                sc.Refresh();
                if (sc.Status != ServiceControllerStatus.Stopped)
                {
                    if (sc.Status != ServiceControllerStatus.StopPending) sc.Stop();
                    sc.WaitForStatus(ServiceControllerStatus.Stopped, Wait);
                }
            }
            SetType(name, startType);
        }

        // Set the start type first (a Disabled service cannot start), then start it.
        public void Start(string name, string startType)
        {
            Check(name, startType);
            SetType(name, startType);
            using (var sc = new ServiceController(name))
            {
                sc.Refresh();
                if (sc.Status != ServiceControllerStatus.Running)
                {
                    if (sc.Status != ServiceControllerStatus.StartPending) sc.Start();
                    sc.WaitForStatus(ServiceControllerStatus.Running, Wait);
                }
            }
        }

        public void SetStartType(string name, string startType)
        {
            Check(name, startType);
            SetType(name, startType);
        }

        // ---------- internals ----------

        // Listed services that exist, by their exact listed name. Controllers not returned are disposed.
        private static List<KeyValuePair<string, ServiceController>> Find(IEnumerable<string> names)
        {
            var wanted = new List<string>();
            foreach (string n in names) if (ArmouryServices.IsListed(n) && !wanted.Contains(n)) wanted.Add(n);
            var found = new List<KeyValuePair<string, ServiceController>>();
            foreach (ServiceController sc in ServiceController.GetServices())
            {
                string match = null;
                foreach (string n in wanted)
                    if (string.Equals(n, sc.ServiceName, StringComparison.OrdinalIgnoreCase)) { match = n; break; }
                if (match == null) sc.Dispose();
                else found.Add(new KeyValuePair<string, ServiceController>(match, sc));
            }
            // Report in list order.
            found.Sort((a, b) => wanted.IndexOf(a.Key).CompareTo(wanted.IndexOf(b.Key)));
            return found;
        }

        private static void Check(string name, string startType)
        {
            if (!ArmouryServices.IsListed(name))
                throw new ArgumentException("Refused: '" + name + "' is not one of the Armoury Crate services Overdrive manages.");
            if (!ArmouryServices.IsStartType(startType))
                throw new ArgumentException("Refused: start type '" + startType + "' is not one of " + string.Join(", ", ArmouryServices.StartTypeNames) + ".");
        }

        // Read only: the delayed auto-start flag the service manager keeps in the service's registry key.
        private static bool IsDelayed(string name)
        {
            try
            {
                using (RegistryKey k = Registry.LocalMachine.OpenSubKey(@"SYSTEM\CurrentControlSet\Services\" + name, false))
                {
                    if (k == null) return false;
                    object v = k.GetValue("DelayedAutostart");
                    return v is int && (int)v != 0;
                }
            }
            catch (Exception) { return false; }
        }

        private const uint SC_MANAGER_CONNECT = 0x0001;
        private const uint SERVICE_QUERY_CONFIG = 0x0001;
        private const uint SERVICE_CHANGE_CONFIG = 0x0002;
        private const uint SERVICE_NO_CHANGE = 0xFFFFFFFF;
        private const uint SERVICE_AUTO_START = 0x00000002;
        private const uint SERVICE_DEMAND_START = 0x00000003;
        private const uint SERVICE_DISABLED = 0x00000004;
        private const uint SERVICE_CONFIG_DELAYED_AUTO_START_INFO = 3;

        [StructLayout(LayoutKind.Sequential)]
        private struct SERVICE_DELAYED_AUTO_START_INFO
        {
            public int fDelayedAutostart;   // BOOL
        }

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern IntPtr OpenSCManagerW(string machineName, string databaseName, uint desiredAccess);

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern IntPtr OpenServiceW(IntPtr scManager, string serviceName, uint desiredAccess);

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool ChangeServiceConfigW(IntPtr service, uint serviceType, uint startType, uint errorControl,
            string binaryPathName, string loadOrderGroup, IntPtr tagId, string dependencies,
            string serviceStartName, string password, string displayName);

        [DllImport("advapi32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern bool ChangeServiceConfig2W(IntPtr service, uint infoLevel, ref SERVICE_DELAYED_AUTO_START_INFO info);

        [DllImport("advapi32.dll", SetLastError = true)]
        private static extern bool CloseServiceHandle(IntPtr handle);

        // ChangeServiceConfig with only the start type changed (what Set-Service -StartupType does), plus the
        // delayed flag when the type is Automatic or AutomaticDelayedStart so a recorded type comes back exactly.
        private static void SetType(string name, string startType)
        {
            uint start;
            switch (startType)
            {
                case "Automatic":
                case "AutomaticDelayedStart": start = SERVICE_AUTO_START; break;
                case "Manual": start = SERVICE_DEMAND_START; break;
                case "Disabled": start = SERVICE_DISABLED; break;
                default: throw new ArgumentException("Refused: start type '" + startType + "'.");
            }

            IntPtr scm = OpenSCManagerW(null, null, SC_MANAGER_CONNECT);
            if (scm == IntPtr.Zero) throw Win32("open the service manager");
            try
            {
                IntPtr svc = OpenServiceW(scm, name, SERVICE_CHANGE_CONFIG | SERVICE_QUERY_CONFIG);
                if (svc == IntPtr.Zero) throw Win32("open " + name);
                try
                {
                    if (!ChangeServiceConfigW(svc, SERVICE_NO_CHANGE, start, SERVICE_NO_CHANGE,
                            null, null, IntPtr.Zero, null, null, null, null))
                        throw Win32("set the start type of " + name);
                    if (start == SERVICE_AUTO_START)
                    {
                        var info = new SERVICE_DELAYED_AUTO_START_INFO();
                        info.fDelayedAutostart = startType == "AutomaticDelayedStart" ? 1 : 0;
                        if (!ChangeServiceConfig2W(svc, SERVICE_CONFIG_DELAYED_AUTO_START_INFO, ref info))
                            throw Win32("set the delayed start of " + name);
                    }
                }
                finally { CloseServiceHandle(svc); }
            }
            finally { CloseServiceHandle(scm); }
        }

        private static Exception Win32(string what)
        {
            var e = new Win32Exception(Marshal.GetLastWin32Error());
            return new InvalidOperationException("Can't " + what + ": " + e.Message);
        }
    }
}
