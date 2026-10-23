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
  - `app/AsusACPI.cs` -> `helper/src/ghelper/AsusACPI.cs`: opening `\\.\ATKACPI`, the DSTS/DEVS IOCTL call, DeviceGet/DeviceSet, CPU fan curve read/write, the device ids Overdrive uses, the fan reading conversion.
  - `app/HardwareControl.cs`, `app/Fan/FanSensorControl.cs` -> `helper/src/ghelper/Sensors.cs`: CPU temperature and fan RPM conversions.
- Values taken from G-Helper's source (not copied code): fan curve minimums from `app/Fans.cs` (`helper/src/Validation.cs`), power write order and fan reset from `app/Mode/ModeControl.cs` (`helper/src/Protocol.cs`).
