// G-Helper - https://github.com/seerge/g-helper - app/Helpers/TouchscreenHelper.cs
// Copyright seerge and contributors, licensed GPL-3.0. Commit c09017453d9ed0c52261b67a48d02bd55232e6ac.
// (The upstream file has no header of its own; these lines record its origin.)
// Modified for Overdrive (ported to C# 5 / .NET Framework 4.8), 2026
//
// Ported from TouchscreenHelper: the PowerShell commands (Get-PnpDevice -FriendlyName '*touch*screen*', and
// Enable-PnpDevice / Disable-PnpDevice on its InstanceId with -Confirm:$false).
// Changes from upstream: the set command is started without waiting and killed after 15 s (upstream waits for it);
// the status read is capped at 5 s and also returns the InstanceId; no RunAsAdmin relaunch; errors are returned.
// Not from G-Helper: the Auto SR registry value, the Auto SR package check and the foreground window lookup.

using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32;

namespace Overdrive.Helper
{
    public sealed class SystemControls : ISystemControls
    {
        private const string TouchFilter = "*touch*screen*";
        private const int ReadTimeoutMs = 5000;
        private const int SetTimeoutMs = 15000;

        private readonly object gate = new object();
        private Process setProcess;
        private Timer killTimer;

        // ---------- touchscreen ----------

        public TouchInfo GetTouchscreen()
        {
            var info = new TouchInfo();
            string cmd = "Get-PnpDevice -FriendlyName '" + TouchFilter + "' | Select-Object -First 1 | ForEach-Object { $_.Status + '|' + $_.InstanceId }";
            try
            {
                bool timedOut = false;
                using (Process p = Start(cmd, true))
                {
                    // Read stdout on this thread; a hung PowerShell is killed by the timer so the read ends.
                    using (var t = new Timer(delegate { timedOut = true; Kill(p); }, null, ReadTimeoutMs, Timeout.Infinite))
                    {
                        string line = null;
                        string l;
                        while ((l = p.StandardOutput.ReadLine()) != null)
                            if (l.Trim().Length > 0 && line == null) line = l.Trim();
                        p.WaitForExit(1000);
                        if (!p.HasExited) { Kill(p); }
                        if (timedOut && line == null)
                        {
                            info.Error = "PowerShell did not answer in 5 seconds.";
                            return info;
                        }
                        if (line == null) return info;   // no matching device
                        int bar = line.IndexOf('|');
                        if (bar < 0) return info;
                        info.Found = true;
                        info.Enabled = line.Substring(0, bar).Trim() == "OK";
                        info.InstanceId = line.Substring(bar + 1).Trim();
                        return info;
                    }
                }
            }
            catch (Exception ex)
            {
                info.Error = "Could not read the touchscreen: " + ex.Message;
                return info;
            }
        }

        public bool StartSetTouchscreen(bool enabled, out string err)
        {
            err = null;
            lock (gate)
            {
                if (setProcess != null && !setProcess.HasExited)
                {
                    err = "A touchscreen change is already running.";
                    return false;
                }
                string verb = enabled ? "Enable-PnpDevice" : "Disable-PnpDevice";
                string cmd = "Get-PnpDevice -FriendlyName '" + TouchFilter + "' | ForEach-Object { " + verb + " -InstanceId $_.InstanceId -Confirm:$false }";
                try
                {
                    Process p = Start(cmd, false);
                    setProcess = p;
                    if (killTimer != null) killTimer.Dispose();
                    // Kills the process if it is still running after 15 s. Touches nothing but the process.
                    killTimer = new Timer(delegate { Kill(p); }, null, SetTimeoutMs, Timeout.Infinite);
                    return true;
                }
                catch (Exception ex)
                {
                    err = "Could not start PowerShell: " + ex.Message;
                    return false;
                }
            }
        }

        private static Process Start(string command, bool redirectOut)
        {
            var psi = new ProcessStartInfo("powershell.exe");
            // The command has no double quotes; it only uses single quotes.
            psi.Arguments = "-NoProfile -NonInteractive -Command \"" + command + "\"";
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.WindowStyle = ProcessWindowStyle.Hidden;
            psi.RedirectStandardOutput = redirectOut;
            return Process.Start(psi);
        }

        private static void Kill(Process p)
        {
            try { if (!p.HasExited) p.Kill(); }
            catch (Exception) { }
        }

        // ---------- Auto SR ----------

        private const string GpuKey = @"Software\Microsoft\DirectX\UserGpuPreferences";
        private const string GpuValue = "DirectXUserGlobalSettings";

        public string ReadGpuSettings()
        {
            try
            {
                using (RegistryKey k = Registry.CurrentUser.OpenSubKey(GpuKey, false))
                {
                    if (k == null) return null;
                    return k.GetValue(GpuValue) as string;
                }
            }
            catch (Exception) { return null; }
        }

        public bool WriteGpuSettings(string value, out string err)
        {
            err = null;
            try
            {
                using (RegistryKey k = Registry.CurrentUser.CreateSubKey(GpuKey))
                {
                    if (k == null) { err = "Could not open the graphics settings key."; return false; }
                    k.SetValue(GpuValue, value, RegistryValueKind.String);
                    return true;
                }
            }
            catch (Exception ex)
            {
                err = "Could not write the graphics setting: " + ex.Message;
                return false;
            }
        }

        public bool AutoSrInstalled()
        {
            try
            {
                string pf = Environment.GetEnvironmentVariable("ProgramFiles");
                if (string.IsNullOrEmpty(pf)) return false;
                string[] dirs = Directory.GetDirectories(Path.Combine(pf, "WindowsApps"), "Microsoft.AutoSuperResolution2_*");
                return dirs.Length > 0;
            }
            catch (Exception) { return false; }   // an access error counts as not installed
        }

        // ---------- foreground app ----------

        [DllImport("user32.dll")]
        private static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll")]
        private static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool CloseHandle(IntPtr h);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder name, ref uint size);

        private const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;

        public string ForegroundExe()
        {
            try
            {
                IntPtr hwnd = GetForegroundWindow();
                if (hwnd == IntPtr.Zero) return "";
                uint pid;
                GetWindowThreadProcessId(hwnd, out pid);
                if (pid == 0) return "";
                IntPtr h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid);
                if (h == IntPtr.Zero) return "";
                try
                {
                    var sb = new StringBuilder(1024);
                    uint size = (uint)sb.Capacity;
                    if (!QueryFullProcessImageName(h, 0, sb, ref size)) return "";
                    return Path.GetFileName(sb.ToString()).ToLowerInvariant();
                }
                finally { CloseHandle(h); }
            }
            catch (Exception) { return ""; }
        }
    }
}
