// G-Helper - https://github.com/seerge/g-helper - app/Gpu/AMD/AmdGpuControl.cs
// Copyright seerge and contributors, licensed GPL-3.0. Commit c09017453d9ed0c52261b67a48d02bd55232e6ac.
// (The upstream file has no header of its own; these lines record its origin.)
// Modified for Overdrive (ported to C# 5 / .NET Framework 4.8), 2026
//
// Ported subset only: the ADL set-up and teardown the FPS limit needs and the FPS limit get/set.
// FindByType (lines 22-70), the constructor (72-95), GetFPSLimit (384-393), SetFPSLimit (395-408),
// ReleaseUnmanagedResources, Dispose and the finalizer (482-501).
// Changes from upstream: C# 5 syntax; no IGpuControl, Logger or AppConfig (the AppConfig.NoGpu check is dropped:
// the FPS limit always needs ADL); the discrete-GPU lookup and _isReady are dropped (the Ally has only the
// integrated GPU, which is the one the FPS limit uses); each early return of the constructor records why in
// Problem (Overdrive's IFpsControl); SetFPSLimit sizes ulReserved to its declared 6 zeros instead of leaving it
// null for the marshaller. Read-only iGPU sensors: Sensor (196-200) and GetiGpuSensors (202-212), returning a
// GpuSensors object (C# 5 has no tuples) or null; upstream's 500 ms PMLog cache (GetPMLogiGpu, 179-194) is dropped
// (the panel polls every 2 s) and a driver without the PMLog entry point gives null.
// Frame rate (read only): StartFPS, GetFPS and StopFPS become FrameMetricsStart, FrameMetricsGet and
// FrameMetricsStop, which return ADL's result code and leave "started" tracking to AdlHardware.
// Discrete-GPU sensors, clocks, OLED/VariBright and app killing are not ported.

using System;
using System.Linq;
using System.Runtime.InteropServices;

namespace Overdrive.Helper.GHelper
{
    // Reference: https://github.com/GPUOpen-LibrariesAndSDKs/display-library/blob/master/Sample-Managed/Program.cs
    public class AmdGpuControl : IFpsControl
    {
        private IntPtr _adlContextHandle;

        private readonly ADLAdapterInfo? _iGPU;

        private string _problem;

        public string Problem { get { return _problem; } }

        private ADLAdapterInfo? FindByType(ADLAsicFamilyType type)
        {
            int numberOfAdapters;
            Adl2.NativeMethods.ADL2_Adapter_NumberOfAdapters_Get(_adlContextHandle, out numberOfAdapters);
            if (numberOfAdapters <= 0)
                return null;

            ADLAdapterInfoArray osAdapterInfoData = new ADLAdapterInfoArray();
            int osAdapterInfoDataSize = Marshal.SizeOf(osAdapterInfoData);
            IntPtr AdapterBuffer = Marshal.AllocCoTaskMem(osAdapterInfoDataSize);
            try
            {
                Marshal.StructureToPtr(osAdapterInfoData, AdapterBuffer, false);
                if (Adl2.NativeMethods.ADL2_Adapter_AdapterInfo_Get(_adlContextHandle, AdapterBuffer, osAdapterInfoDataSize) != Adl2.ADL_SUCCESS)
                    return null;

                osAdapterInfoData = (ADLAdapterInfoArray)Marshal.PtrToStructure(AdapterBuffer, osAdapterInfoData.GetType());
            }
            finally
            {
                Marshal.FreeCoTaskMem(AdapterBuffer);
            }

            const int amdVendorId = 1002;

            // Determine which GPU is internal discrete AMD GPU
            ADLAdapterInfo internalDiscreteAdapter =
                osAdapterInfoData.ADLAdapterInfo
                    .FirstOrDefault(adapter =>
                    {
                        if (adapter.Exist == 0 || adapter.Present == 0)
                            return false;

                        if (adapter.VendorID != amdVendorId)
                            return false;

                        ADLAsicFamilyType asicFamilyType;
                        int asicFamilyTypeValids;
                        if (Adl2.NativeMethods.ADL2_Adapter_ASICFamilyType_Get(_adlContextHandle, adapter.AdapterIndex, out asicFamilyType, out asicFamilyTypeValids) != Adl2.ADL_SUCCESS)
                            return false;

                        asicFamilyType = (ADLAsicFamilyType)((int)asicFamilyType & asicFamilyTypeValids);

                        return (asicFamilyType & type) != 0;
                    });

            if (internalDiscreteAdapter.Exist == 0)
                return null;

            return internalDiscreteAdapter;
        }

        public AmdGpuControl()
        {
            if (!Adl2.Load())
            {
                _problem = "the AMD display library (atiadlxx.dll) is not installed (" + (Adl2.LoadError() ?? "load failed") + ")";
                return;
            }

            try
            {
                int created = Adl2.ADL2_Main_Control_Create(1, out _adlContextHandle);
                if (created != Adl2.ADL_SUCCESS)
                {
                    _adlContextHandle = IntPtr.Zero;
                    _problem = "the AMD display library did not start (ADL error " + created + ")";
                    return;
                }
            }
            catch (Exception ex)
            {
                _problem = "the AMD display library did not start (" + ex.Message + ")";
                return;
            }

            _iGPU = FindByType(ADLAsicFamilyType.Integrated);
            if (_iGPU == null) _problem = "no AMD integrated GPU was found";
        }

        public int GetFPSLimit()
        {
            if (_adlContextHandle == IntPtr.Zero || _iGPU == null) return -1;
            Adl2.NativeMethods.ADLFPSSettingsOutput settings;
            if (Adl2.NativeMethods.ADL2_FPS_Settings_Get(_adlContextHandle, ((ADLAdapterInfo)_iGPU).AdapterIndex, out settings) != Adl2.ADL_SUCCESS) return -1;

            return settings.ulACFPSCurrent;
        }

        public int SetFPSLimit(int limit)
        {
            if (_adlContextHandle == IntPtr.Zero || _iGPU == null) return -1;

            Adl2.NativeMethods.ADLFPSSettingsInput settings = new Adl2.NativeMethods.ADLFPSSettingsInput();

            settings.ulACFPSCurrent = limit;
            settings.ulDCFPSCurrent = limit;
            settings.bGlobalSettings = 1;
            settings.ulReserved = new int[6];

            if (Adl2.NativeMethods.ADL2_FPS_Settings_Set(_adlContextHandle, ((ADLAdapterInfo)_iGPU).AdapterIndex, settings) != Adl2.ADL_SUCCESS) return 0;

            return 1;
        }

        private static int? Sensor(ADLPMLogDataOutput log, ADLSensorType type)
        {
            if (log.Sensors == null || (int)type >= log.Sensors.Length) return null;
            ADLSingleSensorData sensor = log.Sensors[(int)type];
            return sensor.Supported != 0 ? sensor.Value : (int?)null;
        }

        public GpuSensors GetiGpuSensors()
        {
            if (_adlContextHandle == IntPtr.Zero || _iGPU == null) return null;
            ADLPMLogDataOutput log;
            try
            {
                if (Adl2.PMLogMethods.ADL2_New_QueryPMLogData_Get(_adlContextHandle, ((ADLAdapterInfo)_iGPU).AdapterIndex, out log) != Adl2.ADL_SUCCESS) return null;
            }
            catch (EntryPointNotFoundException) { return null; }

            var s = new GpuSensors();
            s.TempC = Sensor(log, ADLSensorType.PMLOG_TEMPERATURE_EDGE)
                ?? Sensor(log, ADLSensorType.PMLOG_TEMPERATURE_GFX)
                ?? Sensor(log, ADLSensorType.PMLOG_TEMPERATURE_SOC);
            s.Use = Sensor(log, ADLSensorType.PMLOG_INFO_ACTIVITY_GFX);
            s.GfxPowerW = Sensor(log, ADLSensorType.PMLOG_GFX_POWER);
            s.CpuPowerW = Sensor(log, ADLSensorType.PMLOG_CPU_POWER);
            s.AsicPowerW = Sensor(log, ADLSensorType.PMLOG_ASIC_POWER);
            return s;
        }

        // ADL result codes (0 = ADL_SUCCESS); -1 when ADL or the adapter is unavailable.
        // A driver without the entry points throws EntryPointNotFoundException (AdlHardware turns that into "not supported").
        public int FrameMetricsStart()
        {
            if (_adlContextHandle == IntPtr.Zero || _iGPU == null) return -1;
            return Adl2.FrameMetricsMethods.ADL2_Adapter_FrameMetrics_Start(_adlContextHandle, ((ADLAdapterInfo)_iGPU).AdapterIndex, 0);
        }

        public int FrameMetricsGet(out float fps)
        {
            fps = 0;
            if (_adlContextHandle == IntPtr.Zero || _iGPU == null) return -1;
            return Adl2.FrameMetricsMethods.ADL2_Adapter_FrameMetrics_Get(_adlContextHandle, ((ADLAdapterInfo)_iGPU).AdapterIndex, 0, out fps);
        }

        public int FrameMetricsStop()
        {
            if (_adlContextHandle == IntPtr.Zero || _iGPU == null) return -1;
            return Adl2.FrameMetricsMethods.ADL2_Adapter_FrameMetrics_Stop(_adlContextHandle, ((ADLAdapterInfo)_iGPU).AdapterIndex, 0);
        }

        private void ReleaseUnmanagedResources()
        {
            if (_adlContextHandle != IntPtr.Zero)
            {
                Adl2.NativeMethods.ADL2_Main_Control_Destroy(_adlContextHandle);
                _adlContextHandle = IntPtr.Zero;
            }
        }

        public void Dispose()
        {
            ReleaseUnmanagedResources();
            GC.SuppressFinalize(this);
        }

        ~AmdGpuControl()
        {
            ReleaseUnmanagedResources();
        }
    }
}
