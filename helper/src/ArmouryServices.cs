// Service lists from G-Helper - https://github.com/seerge/g-helper - app/Helpers/AsusService.cs (lines 10-33)
// Copyright seerge and contributors, licensed GPL-3.0. Commit c09017453d9ed0c52261b67a48d02bd55232e6ac.
// (The upstream file has no header of its own; these lines record its origin.)
// Modified for Overdrive (ported to C# 5 / .NET Framework 4.8), 2026
//
// Ported: the `services` and `servicesAC` lists and the start type StopAsusServices gives each
// (line 93: servicesAC -> "Manual", the rest -> "Disabled"). "AsusPTPService" is commented out upstream
// (line 27) and stays out. The lists are the only service names the helper will ever touch.

using System;
using System.Collections.Generic;

namespace Ally.Shared
{
    public static class ArmouryServices
    {
        // AsusService.cs `services`: stopped and set to Disabled.
        public static readonly string[] Services =
        {
            "ArmouryCrateControlInterface",
            "ArmouryCrateProArtService",
            "AsHidService",
            "ASUSOptimization",
            "AsusAppService",
            "ASUSLinkNear",
            "ASUSLinkRemote",
            "ASUSSoftwareManager",
            "ASUSLiveUpdateAgent",
            "ASUSSwitch",
            "ASUSSystemAnalysis",
            "ASUSSystemDiagnosis",
            "ASUSXGMobileService",
            "AsusCertService"
        };

        // AsusService.cs `servicesAC`: stopped and set to Manual.
        public static readonly string[] ServicesAC =
        {
            "ArmouryCrateSEService",
            "ArmouryCrateService",
            "LightingService",
        };

        // services then servicesAC (G-Helper: services.Concat(servicesAC) when stopping Armoury Crate too).
        public static readonly IList<string> All = Array.AsReadOnly(Concat(Services, ServicesAC));

        // "installed" in armoury_state: one of these exists.
        public static readonly string[] InstalledMarkers = { "ArmouryCrateSEService", "ArmouryCrateService" };

        // Start types as the helper reports and accepts them. AutomaticDelayedStart is Automatic with the
        // delayed-start flag; Boot and System never apply to these services and are refused.
        public static readonly string[] StartTypeNames = { "Automatic", "AutomaticDelayedStart", "Manual", "Disabled" };

        // Exact, case-sensitive match against the fixed lists.
        public static bool IsListed(string name)
        {
            return name != null && All.Contains(name);
        }

        public static bool IsStartType(string type)
        {
            return type != null && Array.IndexOf(StartTypeNames, type) >= 0;
        }

        // The start type G-Helper's StopAsusServices sets (AsusService.cs line 93).
        public static string StopStartType(string name)
        {
            if (Array.IndexOf(ServicesAC, name) >= 0) return "Manual";
            if (Array.IndexOf(Services, name) >= 0) return "Disabled";
            throw new ArgumentException("Refused: '" + name + "' is not one of the Armoury Crate services this plugin manages.");
        }

        private static string[] Concat(string[] a, string[] b)
        {
            var r = new string[a.Length + b.Length];
            a.CopyTo(r, 0);
            b.CopyTo(r, a.Length);
            return r;
        }
    }
}
