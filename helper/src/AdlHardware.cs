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
    }

    // The real IAdl. ADL is set up on first use (not at helper start) and kept for the life of the process; a
    // set-up that failed is retried on the next call, so a driver that comes up late is picked up.
    public class AdlHardware : IAdl, IDisposable
    {
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
            return v < 0 ? -1 : v;
        }

        public int SetFpsLimit(int fps)
        {
            if (!Ready()) return -1;
            int r = control.SetFPSLimit(fps);
            unavailable = r == -1 ? "the graphics driver's frame rate target control is not available" : null;
            return r;
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
