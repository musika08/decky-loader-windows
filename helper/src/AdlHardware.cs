using System;
using Overdrive.Helper.GHelper;

namespace Overdrive.Helper
{
    // The FPS-limit part of the ported G-Helper AmdGpuControl, as a seam so AdlHardware can be tested without ADL.
    public interface IFpsControl : IDisposable
    {
        // Why the control is not usable, or null when it set up.
        string Problem { get; }
        // Current limit (0 = off), -1 when unavailable. G-Helper returns ulACFPSCurrent.
        int GetFPSLimit();
        // 1 = OK, 0 = refused by the driver, -1 = unavailable.
        int SetFPSLimit(int limit);
        // Integrated GPU sensors (read only), or null when the driver has no sensor log.
        GpuSensors GetiGpuSensors();
    }

    // The real IAdl. ADL is set up on first use (not at helper start) and kept for the life of the process; a
    // set-up that failed is retried on the next call, so a driver that comes up late is picked up.
    //
    // "Off" follows G-Helper (app/Ally/AllyControl.cs ToggleFPSLimit and its toast, commit in helper\GHELPER_COMMIT):
    // G-Helper never writes 0; its OFF is 240 (`case 120: fpsLimit = 240`), and any value outside 1..120 shows as
    // "OFF" (`(fpsLimit > 0 && fpsLimit <= 120) ? fpsLimit : "OFF"`). So the protocol's 0 is written as 240, and a
    // read outside 1..120 (240, 0, anything else) is reported as 0.
    public class AdlHardware : IAdl, IDisposable
    {
        public const int GHelperFpsOff = 240;

        // Protocol value -> value written to the driver.
        public static int ToDriver(int fps) { return fps == 0 ? GHelperFpsOff : fps; }

        // Driver value -> protocol value (0 = off).
        public static int FromDriver(int value) { return (value > 0 && value <= 120) ? value : 0; }

        private readonly Func<IFpsControl> factory;
        private IFpsControl control;
        private string unavailable;

        public AdlHardware() : this(() => new AmdGpuControl()) { }

        public AdlHardware(Func<IFpsControl> factory)
        {
            if (factory == null) throw new ArgumentNullException("factory");
            this.factory = factory;
        }

        public string Unavailable { get { return unavailable; } }

        public int GetFpsLimit()
        {
            if (!Ready()) return -1;
            int v = control.GetFPSLimit();
            unavailable = v < 0 ? "the graphics driver did not report its frame rate target" : null;
            return v < 0 ? -1 : FromDriver(v);
        }

        public int SetFpsLimit(int fps)
        {
            if (!Ready()) return -1;
            int r = control.SetFPSLimit(ToDriver(fps));
            unavailable = r == -1 ? "the graphics driver's frame rate target control is not available" : null;
            return r;
        }

        public GpuSensors GetGpuSensors()
        {
            if (!Ready()) return null;
            // Unavailable is left alone here: it describes the frame rate control.
            return control.GetiGpuSensors();
        }

        public void Dispose()
        {
            if (control != null) { control.Dispose(); control = null; }
        }

        private bool Ready()
        {
            if (control != null) return true;
            IFpsControl c = null;
            try
            {
                c = factory();
                if (c.Problem != null)
                {
                    unavailable = c.Problem;
                    c.Dispose();
                    return false;
                }
            }
            catch (DllNotFoundException)
            {
                unavailable = "the AMD display library (atiadlxx.dll) is not installed";
                if (c != null) c.Dispose();
                return false;
            }
            catch (Exception ex)
            {
                unavailable = "the AMD display library did not start (" + ex.Message + ")";
                if (c != null) c.Dispose();
                return false;
            }
            control = c;
            unavailable = null;
            return true;
        }
    }
}
