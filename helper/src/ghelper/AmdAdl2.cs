// G-Helper - https://github.com/seerge/g-helper - app/Gpu/AMD/AmdAdl2.cs
// Copyright seerge and contributors, licensed GPL-3.0. Commit c09017453d9ed0c52261b67a48d02bd55232e6ac.
// (The upstream file has no header of its own; these lines record its origin.)
// Modified for Overdrive (ported to C# 5 / .NET Framework 4.8), 2026
//
// Ported subset only: what the FPS limit (frame rate target control) needs. ADLAsicFamilyType (lines 61-73),
// ADLAdapterInfo and ADLAdapterInfoArray (242-309), the Adl2 constants (370-405), ADL2_Main_Control_Create,
// Load and the memory callbacks (409-461), and from NativeMethods: ADL_Main_Memory_Alloc, ADL2_Main_Control_Create,
// ADL2_Main_Control_Destroy, ADL2_Adapter_NumberOfAdapters_Get, ADL2_Adapter_AdapterInfo_Get,
// ADL2_Adapter_ASICFamilyType_Get, ADL2_FPS_Settings_Get/Set and their two structs (463-493, 547-548, 595-629).
// Changes from upstream: C# 5 syntax (nint -> IntPtr, no file-scoped namespace, no exception filter);
// Load prelinks NativeMethods (upstream prelinks Adl2, which holds no imports, so a missing DLL only showed up at
// the first call) and LoadError keeps the reason; the unused private Size field of ADLAdapterInfo has its
// "never used" warnings silenced (the build treats warnings as errors).
// For the integrated GPU's sensors (read only): ADLSingleSensorData and ADLPMLogDataOutput (lines 35-49), the
// ADLSensorType values GetiGpuSensors reads (from lines 76-140: EDGE, ACTIVITY_GFX, ASIC_POWER, TEMPERATURE_GFX,
// TEMPERATURE_SOC, GFX_POWER, CPU_POWER), ADL_PMLOG_MAX_SENSORS (405) and ADL2_New_QueryPMLogData_Get (545). The
// PMLog import sits in its own class (PMLogMethods) that Load does not prelink, so a driver without it can never
// stop the FPS limit from loading; the private Size field's "never used" warning is silenced.
// Nothing else (overdrive clocks, VariBright, display, switchable graphics) is ported.

using System;
using System.Runtime.InteropServices;

namespace Overdrive.Helper.GHelper
{
    [Flags]
    public enum ADLAsicFamilyType
    {
        Undefined = 0,
        Discrete = 1 << 0,
        Integrated = 1 << 1,
        Workstation = 1 << 2,
        FireMV = 1 << 3,
        Xgp = 1 << 4,
        Fusion = 1 << 5,
        Firestream = 1 << 6,
        Embedded = 1 << 7,
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct ADLSingleSensorData
    {
        public int Supported;
        public int Value;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct ADLPMLogDataOutput
    {
#pragma warning disable 169
        int Size;
#pragma warning restore 169

        [MarshalAs(UnmanagedType.ByValArray, SizeConst = Adl2.ADL_PMLOG_MAX_SENSORS)]
        public ADLSingleSensorData[] Sensors;
    }

    // Subset of upstream ADLSensorType: the sensors GetiGpuSensors reads (same values).
    public enum ADLSensorType
    {
        PMLOG_TEMPERATURE_EDGE = 8, // Current edge of the die temperature value in C
        PMLOG_INFO_ACTIVITY_GFX = 19, // Current graphic activity level in percentage
        PMLOG_ASIC_POWER = 23, // Current ASIC power draw in Watt
        PMLOG_TEMPERATURE_GFX = 28,
        PMLOG_TEMPERATURE_SOC = 29,
        PMLOG_GFX_POWER = 30,
        PMLOG_CPU_POWER = 33,
    }

    /// <summary> ADLAdapterInfo Structure</summary>
    [StructLayout(LayoutKind.Sequential)]
    public struct ADLAdapterInfo
    {
        /// <summary>The size of the structure</summary>
#pragma warning disable 169, 649
        int Size;
#pragma warning restore 169, 649

        /// <summary> Adapter Index</summary>
        public int AdapterIndex;

        /// <summary> Adapter UDID</summary>
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = Adl2.ADL_MAX_PATH)]
        public string UDID;

        /// <summary> Adapter Bus Number</summary>
        public int BusNumber;

        /// <summary> Adapter Driver Number</summary>
        public int DriverNumber;

        /// <summary> Adapter Function Number</summary>
        public int FunctionNumber;

        /// <summary> Adapter Vendor ID</summary>
        public int VendorID;

        /// <summary> Adapter Adapter name</summary>
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = Adl2.ADL_MAX_PATH)]
        public string AdapterName;

        /// <summary> Adapter Display name</summary>
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = Adl2.ADL_MAX_PATH)]
        public string DisplayName;

        /// <summary> Adapter Present status</summary>
        public int Present;

        /// <summary> Adapter Exist status</summary>
        public int Exist;

        /// <summary> Adapter Driver Path</summary>
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = Adl2.ADL_MAX_PATH)]
        public string DriverPath;

        /// <summary> Adapter Driver Ext Path</summary>
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = Adl2.ADL_MAX_PATH)]
        public string DriverPathExt;

        /// <summary> Adapter PNP String</summary>
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = Adl2.ADL_MAX_PATH)]
        public string PNPString;

        /// <summary> OS Display Index</summary>
        public int OSDisplayIndex;
    }

    /// <summary> ADLAdapterInfo Array</summary>
    [StructLayout(LayoutKind.Sequential)]
    public struct ADLAdapterInfoArray
    {
        /// <summary> ADLAdapterInfo Array </summary>
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = Adl2.ADL_MAX_ADAPTERS)]
        public ADLAdapterInfo[] ADLAdapterInfo;
    }

    public class Adl2
    {
        public const string Atiadlxx_FileName = "atiadlxx.dll";

        /// <summary> Define the maximum path</summary>
        public const int ADL_MAX_PATH = 256;

        /// <summary> Define the maximum adapters</summary>
        public const int ADL_MAX_ADAPTERS = 40 /* 150 */;

        /// <summary> Define the successful</summary>
        public const int ADL_SUCCESS = 0;

        /// <summary> Define the failure</summary>
        public const int ADL_FAIL = -1;

        /// <summary> Performance Metrics Log max sensors number </summary>
        public const int ADL_PMLOG_MAX_SENSORS = 256;

        // ///// <summary> ADL Create Function to create ADL Data</summary>
        /// <param name="enumConnectedAdapters">If it is 1, then ADL will only return the physical exist adapters </param>
        ///// <returns> retrun ADL Error Code</returns>
        public static int ADL2_Main_Control_Create(int enumConnectedAdapters, out IntPtr adlContextHandle)
        {
            return NativeMethods.ADL2_Main_Control_Create(ADL_Main_Memory_Alloc_Impl_Reference, enumConnectedAdapters, out adlContextHandle);
        }

        private static bool? isDllLoaded;
        private static string loadError;

        public static bool Load()
        {
            if (isDllLoaded != null)
                return isDllLoaded.Value;

            try
            {
                Marshal.PrelinkAll(typeof(NativeMethods));
                isDllLoaded = true;
            }
            catch (DllNotFoundException e)
            {
                loadError = e.Message;
                isDllLoaded = false;
            }
            catch (EntryPointNotFoundException e)
            {
                loadError = e.Message;
                isDllLoaded = false;
            }

            return isDllLoaded.Value;
        }

        // Why Load returned false (Overdrive addition, for the error message).
        public static string LoadError() { return loadError; }

        // Kept in a static field so the delegate ADL calls back into is never collected (as upstream).
        private static NativeMethods.ADL_Main_Memory_Alloc ADL_Main_Memory_Alloc_Impl_Reference = Memory_Alloc_Impl;

        /// <summary> Build in memory allocation function</summary>
        /// <param name="size">input size</param>
        /// <returns>return the memory buffer</returns>
        private static IntPtr Memory_Alloc_Impl(int size)
        {
            return Marshal.AllocCoTaskMem(size);
        }

        // Not prelinked by Load (see the header): a missing entry point throws EntryPointNotFoundException at the call.
        public static class PMLogMethods
        {
            [DllImport(Atiadlxx_FileName)]
            public static extern int ADL2_New_QueryPMLogData_Get(IntPtr adlContextHandle, int adapterIndex, out ADLPMLogDataOutput adlpmLogDataOutput);
        }

        public static class NativeMethods
        {
            /// <summary> ADL Memory allocation function allows ADL to callback for memory allocation</summary>
            /// <param name="size">input size</param>
            /// <returns> retrun ADL Error Code</returns>
            public delegate IntPtr ADL_Main_Memory_Alloc(int size);

            // ///// <summary> ADL Create Function to create ADL Data</summary>
            /// <param name="callback">Call back functin pointer which is ised to allocate memeory </param>
            /// <param name="enumConnectedAdapters">If it is 1, then ADL will only retuen the physical exist adapters </param>
            ///// <returns> retrun ADL Error Code</returns>
            [DllImport(Atiadlxx_FileName)]
            public static extern int ADL2_Main_Control_Create(ADL_Main_Memory_Alloc callback, int enumConnectedAdapters, out IntPtr adlContextHandle);

            /// <summary> ADL Destroy Function to free up ADL Data</summary>
            /// <returns> retrun ADL Error Code</returns>
            [DllImport(Atiadlxx_FileName)]
            public static extern int ADL2_Main_Control_Destroy(IntPtr adlContextHandle);

            /// <summary> ADL Function to get the number of adapters</summary>
            /// <param name="numAdapters">return number of adapters</param>
            /// <returns> retrun ADL Error Code</returns>
            [DllImport(Atiadlxx_FileName)]
            public static extern int ADL2_Adapter_NumberOfAdapters_Get(IntPtr adlContextHandle, out int numAdapters);

            /// <summary> ADL Function to get the GPU adapter information</summary>
            /// <param name="info">return GPU adapter information</param>
            /// <param name="inputSize">the size of the GPU adapter struct</param>
            /// <returns> retrun ADL Error Code</returns>
            [DllImport(Atiadlxx_FileName)]
            public static extern int ADL2_Adapter_AdapterInfo_Get(IntPtr adlContextHandle, IntPtr info, int inputSize);

            [DllImport(Atiadlxx_FileName)]
            public static extern int ADL2_Adapter_ASICFamilyType_Get(IntPtr adlContextHandle, int adapterIndex, out ADLAsicFamilyType asicFamilyType, out int asicFamilyTypeValids);

            // FPS

            [DllImport(Atiadlxx_FileName)]
            public static extern int ADL2_FPS_Settings_Get(IntPtr context, int iAdapterIndex, out ADLFPSSettingsOutput lpFPSSettings);

            [DllImport(Atiadlxx_FileName)]
            public static extern int ADL2_FPS_Settings_Set(IntPtr context, int iAdapterIndex, ADLFPSSettingsInput lpFPSSettings);

            [StructLayout(LayoutKind.Sequential)]
            public struct ADLFPSSettingsOutput
            {
                public int ulSize;
                public int bACFPSEnabled;
                public int bDCFPSEnabled;
                public int ulACFPSCurrent;
                public int ulDCFPSCurrent;
                public int ulACFPSMaximum;
                public int ulACFPSMinimum;
                public int ulDCFPSMaximum;
                public int ulDCFPSMinimum;
            }

            [StructLayout(LayoutKind.Sequential)]
            public struct ADLFPSSettingsInput
            {
                public int ulSize;
                public int bGlobalSettings;
                public int ulACFPSCurrent;
                public int ulDCFPSCurrent;

                // Assuming ulReserved is an array of 6 integers
                [MarshalAs(UnmanagedType.ByValArray, SizeConst = 6)]
                public int[] ulReserved;
            }
        }
    }
}
