# Third-party notices

Overdrive is licensed GPL-3.0-only (see `LICENSE`). It is an unofficial plugin and is not affiliated with ASUS.


## Bundled in `dist/index.js`

- `@decky/api` (LGPL-2.1): Decky Loader frontend API.
- `react-icons` (MIT): the plugin icon (Font Awesome icons by Fonticons, Inc., CC BY 4.0, via react-icons).

`react`, `react-dom` and `@decky/ui` are provided by Decky Loader at run time and are not bundled.

## G-Helper (ported into `bin/asus-helper.exe`)

- Project: G-Helper, https://github.com/seerge/g-helper
- Authors: seerge and contributors
- License: GPL-3.0
- Commit used: c09017453d9ed0c52261b67a48d02bd55232e6ac (also recorded in `helper/GHELPER_COMMIT`)
- Ported (subset, rewritten for C# 5 / .NET Framework 4.8; each file is marked "Modified for Overdrive (ported to C# 5 / .NET Framework 4.8), 2026"):
  - `app/AsusACPI.cs` -> `helper/src/ghelper/AsusACPI.cs`: opening `\\.\ATKACPI`, the DSTS/DEVS IOCTL call, DeviceGet/DeviceSet, CPU fan curve read/write, the device ids Overdrive uses (including the read-only GPU temperature id `Temp_GPU`), the fan reading conversion.
  - `app/HardwareControl.cs`, `app/Fan/FanSensorControl.cs` -> `helper/src/ghelper/Sensors.cs`: CPU temperature and fan RPM conversions.
  - `app/Gpu/AMD/AmdAdl2.cs` -> `helper/src/ghelper/AmdAdl2.cs`: the AMD ADL declarations the FPS limit needs (library load, context create/destroy, adapter info and ASIC family lookup, `ADL2_FPS_Settings_Get/Set` and their structs; the PMLog sensor structs, the sensor types the integrated-GPU readings use and `ADL2_New_QueryPMLogData_Get`; `ADL2_Adapter_FrameMetrics_Start/Get/Stop`).
  - `app/Gpu/AMD/AmdGpuControl.cs` -> `helper/src/ghelper/AmdGpuControl.cs`: ADL set-up and teardown, integrated-GPU lookup, the FPS limit get/set (frame rate target control), and the read-only integrated-GPU sensors (`GetiGpuSensors`: temperature, usage, graphics and CPU power), and the frame rate (`StartFPS`/`GetFPS`/`StopFPS` as `FrameMetricsStart/Get/Stop`).
  - `app/Helpers/AsusService.cs` -> `helper/src/ArmouryServices.cs`: the `services` and `servicesAC` service lists and the start type each gets when stopped (servicesAC Manual, the rest Disabled).
  - `app/Mode/PowerNative.cs` -> `helper/src/WinPower.cs`: the PowrProf declarations and the CPU Boost get/set (boost mode setting of the active scheme, AC and DC) and the power mode overlay get/set (`PowerGetEffectiveOverlayScheme`, `PowerSetActiveOverlayScheme`).
  - `app/USB/AsusHid.cs` -> `helper/src/AsusHid.cs`: `ASUS_ID`, `INPUT_ID` (0x5A), `MAIN_AURA_PIDS`, the controller collection choice of `FindDevices`/`FindHidStream` and `WriteInput`'s padded `SetFeature` write (the HID access itself is rewritten on the Win32 SetupAPI/hid.dll functions, as HidSharp is not used).
  - `app/Ally/AllyControl.cs` -> `helper/src/Protocol.cs` (controller section): the controller mode report of `ApplyMode` (`[0x5A,0xD1,0x01,0x01,mode]`, Gamepad 1, Mouse 3) and the grip vibration reports of `SetDeadzones` (`[0x5A,0xD1,0x06,0x02,s,s]`, and `[0x5A,0xD1,0x1C,0x02,s,s]` on the Xbox Ally).
- Values taken from G-Helper's source (not copied code): fan curve minimums from `app/Fans.cs` (`helper/src/Validation.cs`), power write order and fan reset from `app/Mode/ModeControl.cs` (`helper/src/Protocol.cs`), the GPU temperature source order and valid range from `app/HardwareControl.cs` GetGPUTemp (`helper/src/Protocol.cs` ReadGpuSensors), the stop-then-set-start-type order of `app/Helpers/ProcessHelper.cs` StopDisableService (`helper/src/ServiceHardware.cs`, which uses the Windows service API instead of PowerShell).
