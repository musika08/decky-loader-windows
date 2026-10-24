# Third-party notices

Traction is licensed GPL-3.0-only (see `LICENSE`). It is an unofficial plugin and is not affiliated with ASUS.

## Bundled in `dist/index.js`

- `@decky/api` (LGPL-2.1): Decky Loader frontend API.
- `react-icons` (MIT): the plugin icon (Font Awesome icons by Fonticons, Inc., CC BY 4.0, via react-icons).

`react`, `react-dom` and `@decky/ui` are provided by Decky Loader at run time and are not bundled.

## G-Helper (ported into `bin/traction-helper.exe`)

- Project: G-Helper, https://github.com/seerge/g-helper
- Authors: seerge and contributors
- License: GPL-3.0
- Commit used: c09017453d9ed0c52261b67a48d02bd55232e6ac (also recorded in `helper/GHELPER_COMMIT`)
- Ported (subset, rewritten for C# 5 / .NET Framework 4.8; each file is marked "Modified for Overdrive (ported to C# 5 / .NET Framework 4.8), 2026"). The files under `helper/src/shared/` are copies of files from the Overdrive plugin (same author, GPL-3.0), taken at the commit named in `shared/SOURCE`:
  - `app/Gpu/AMD/AmdAdl2.cs` -> `helper/src/shared/ghelper/AmdAdl2.cs`: the AMD ADL declarations (library load, context create/destroy, adapter info and ASIC family lookup, the PMLog sensor structs and query, `ADL2_Adapter_FrameMetrics_Start/Get/Stop`).
  - `app/Gpu/AMD/AmdGpuControl.cs` -> `helper/src/shared/ghelper/AmdGpuControl.cs`: ADL set-up and teardown, integrated-GPU lookup, the read-only integrated-GPU sensors (`GetiGpuSensors`) and the frame rate (`StartFPS`/`GetFPS`/`StopFPS` as `FrameMetricsStart/Get/Stop`).
  - `app/Helpers/AsusService.cs` -> `helper/src/shared/ArmouryServices.cs`: the `services` and `servicesAC` service lists and the start type each gets when stopped (servicesAC Manual, the rest Disabled).
  - `app/USB/AsusHid.cs` -> `helper/src/shared/AsusHid.cs`: `ASUS_ID`, `INPUT_ID` (0x5A), `MAIN_AURA_PIDS`, the controller collection choice of `FindDevices`/`FindHidStream` and `WriteInput`'s padded `SetFeature` write (the HID access itself is rewritten on the Win32 SetupAPI/hid.dll functions, as HidSharp is not used).
  - `app/Ally/AllyControl.cs` -> `helper/src/TractionCore.cs` (controller section): the controller mode report of `ApplyMode` (`[0x5A,0xD1,0x01,0x01,mode]`, Gamepad 1, Mouse 3) and the grip vibration reports of `SetDeadzones` (`[0x5A,0xD1,0x06,0x02,s,s]`, and `[0x5A,0xD1,0x1C,0x02,s,s]` on the Xbox Ally).
  - `app/Helpers/TouchscreenHelper.cs` -> `helper/src/shared/SystemControls.cs`: the PowerShell commands for the touchscreen (`Get-PnpDevice -FriendlyName '*touch*screen*'`, `Enable-PnpDevice` / `Disable-PnpDevice` on its InstanceId). The change is started without waiting and killed after 15 s; the status read is capped at 5 s.
- Values taken from G-Helper's source (not copied code): the stop-then-set-start-type order of `app/Helpers/ProcessHelper.cs` StopDisableService (`helper/src/shared/ServiceHardware.cs`, which uses the Windows service API instead of PowerShell).
