# Overdrive: CPU Boost, power mode, controller mode, grip vibration, touchscreen, Auto SR

Date: 2026-10-24. Status: design approved in conversation; awaiting owner review of this written spec.
Builds on `2026-10-23-overdrive-stage1-design.md` (helper, settings, reapply) and the Auto TDP work
(`2026-10-24-overdrive-auto-tdp-design.md`, whose `frame_sample` is reused here).

## 1. Purpose

Six controls the owner asked for, all in the Overdrive plugin:
- **Performance section:**
  - CPU Boost on/off.
  - Battery performance (Eco / Balanced / Best performance).
- **New "Controller & screen" section:**
  - Controller mode (Gamepad / Desktop / Auto).
  - Grip vibration on/off.
  - Touchscreen on/off.
  - Auto SR on/off (added by the owner later the same day).

Owner decisions (2026-10-24):
- Touchscreen, controller mode and grip vibration live in Overdrive, not Control Center. Overdrive's helper already
  has admin rights and the ASUS controller access; Control Center stays a plain Windows-settings plugin without them.
- Battery performance sets Windows' power mode for the power source the Ally is on now (battery or plugged in), as
  Windows' quick setting and G-Helper do.
- Auto controller mode is changed from G-Helper's rule so that Steam Big Picture never loses gamepad control (section 4).

Source: G-Helper at the pinned commit `c09017453d9ed0c52261b67a48d02bd55232e6ac`:
- `app/Mode/PowerNative.cs`: `GetCPUBoost`, `SetCPUBoost`, `GetPowerMode`, `SetPowerMode`, the overlay GUIDs.
- `app/Ally/AllyControl.cs`: `ApplyMode`, `ControllerMode`, the vibration writes in `SetDeadzones`, `SetVibrationMode`.
- `app/USB/AsusHid.cs`: `INPUT_ID`, `WriteInput`, PIDs.
- `app/Helpers/TouchscreenHelper.cs`.

## 2. Facts read on the owner's Ally (2026-10-24, read only)

- **Controller:** USB VID 0x0B05, PID 0x1B4C (in G-Helper's `MAIN_AURA_PIDS`). It exposes several HID collections
  (vendor-defined MI_02 collections, the gamepad on MI_05).
- **Touchscreen:** PnP device "HID-compliant touch screen", `HID\NVTK0603&COL01\...`, status OK.
- **Active power plan:** "PD Turbo" (62346f4c-22a8-45c0-adf0-24a1cb9f2739, created by Armoury Crate).
  - Processor performance boost mode (be337238-0d82-4146-a960-4f3749d470c7, subgroup
    54533251-82be-4824-96c1-47b60b740d00) is 0 (Disabled) on AC and DC.
  - Values available: 0 Disabled, 1 Enabled, 2 Aggressive, 3 Efficient Enabled, 4 Efficient Aggressive,
    5 Aggressive At Guaranteed, 6 Efficient Aggressive At Guaranteed.
- **Power mode overlay:** ActiveOverlayAc and ActiveOverlayDc are both ded574b5-45a0-4f42-8737-46345c09c238
  (Best performance).

## 3. Controls

### 3.1 CPU Boost (Performance section)

- Toggle "CPU Boost", single press, showing the live value read from Windows: on when the boost mode of the active
  plan is not 0.
- **Off:** writes 0 to AC and DC, then re-activates the active scheme (G-Helper `SetCPUBoost`).
- **On:** writes the value saved when it was last turned off. If no value was ever saved (as on the owner's Ally,
  where it is already 0), it writes 2 (Aggressive). AC and DC are written alike.
- The value written for On is saved as `cpu_boost_on_value` (int, default 2). Nothing else is saved. Windows keeps
  the setting.
- Description: "Lets the CPU run above its base clock. Off: cooler and longer battery, a little slower."

### 3.2 Battery performance (Performance section)

- An inline full-width picker, like FPS limit, labelled "Battery" (owner rename 2026-10-24; label, value and arrow on one line).
- Choices map to Windows' power-mode overlays (G-Helper `PowerNative` GUIDs):
  - Eco = 961cc777-2547-4f9d-8174-7d86181b8a7a (Windows: Best power efficiency);
  - Balanced = 00000000-0000-0000-0000-000000000000;
  - Best performance = ded574b5-45a0-4f42-8737-46345c09c238.
- **Read:** `PowerGetEffectiveOverlayScheme`, shown live. Any other value shows as "Set in Windows" with no choice
  checked.
- **Write:** `PowerSetActiveOverlayScheme(guid)`. It applies to the source the Ally is on now; Windows keeps the
  other source's own choice.
- Description: "Windows power mode for the power source in use now (battery or plugged in)."
- Not saved by Overdrive (Windows keeps it).
- Not linked to Overdrive's performance modes. G-Helper links them; the owner's control stays independent.

### 3.3 Controller mode (Controller & screen section)

- An inline picker: Gamepad, Desktop, Auto.
- **Writes** (G-Helper `ApplyMode`): HID feature report `[0x5A, 0xD1, 0x01, 0x01, mode]` with mode 1 = Gamepad,
  3 = Desktop (G-Helper "Mouse"). It is sent to the ASUS controller's HID collection that supports report id 0x5A
  (VID 0x0B05, PID in G-Helper's list; 0x1B4C here).
- **Auto (Overdrive's own rule):** Gamepad when the foreground window belongs to Steam (steam.exe or
  steamwebhelper.exe) or to a process drawing frames (`frame_sample` fps > 0); Desktop when any other app is in front
  (e.g. a browser or File Explorer) and the frame reading is valid and exactly 0. An unknown frame rate (a failed
  reply, no AMD reading, a non-finite value) decides Gamepad.
  - The decision is made every 300 ms and switches after 3 equal decisions in a row (about 1 s), as G-Helper's
    `_autoCount == 3`.
  - Only a change is written.
- **Saved:** `controller_mode` ("gamepad" | "desktop" | "auto", default "gamepad"). Reapplied at start and after
  resume, AC change or a helper restart (the controller forgets after sleep).
  - Desktop does not survive a plugin load (ruling C7): at load, before the first reapply, a stored "desktop" is
    changed to "gamepad" and saved, so after a Decky restart or a boot the panel and the controller both show
    Gamepad. Within a running session, reapplies (resume, AC change, helper restart) keep writing Desktop. "auto" is
    kept (it starts as Gamepad anyway).
- **Safety:** when Overdrive stops (unload, Decky restart) the controller is set to Gamepad before the helper stops,
  so Big Picture is never left with a mouse-only controller.
  - The mode is not changed while an Armoury Crate switch is in progress.
- Description per choice:
  - Gamepad: "The controller works as a gamepad (Steam and games)."
  - Desktop: "Sticks and triggers work as a mouse; Steam menus cannot be steered."
  - Auto: "Gamepad in Steam and games, mouse on the desktop."

### 3.4 Grip vibration (Controller & screen section)

- Toggle "Grip vibration", single press.
- **Write** (G-Helper `SetDeadzones` vibration lines): `[0x5A, 0xD1, 0x06, 0x02, s, s]`, and on the Xbox Ally family
  also `[0x5A, 0xD1, 0x1C, 0x02, s, s]`. s = 100 for on, 0 for off.
  - Xbox Ally = product name contains "Xbox" (the owner's reports "ROG Xbox Ally X RC73XA").
- **Saved:** `grip_vibration` (bool, default true). Reapplied with the controller mode.
- Description: "Vibration motors in the grips."

### 3.5 Touchscreen (Controller & screen section)

- Toggle "Touchscreen", single press, showing the live device status. On = the device "HID-compliant touch screen"
  (FriendlyName like `*touch*screen*`) is enabled.
- **Write:** enable or disable that PnP device (G-Helper uses `Enable-PnpDevice` / `Disable-PnpDevice` through
  PowerShell). The helper starts the same command and replies at once without waiting, so the single-threaded
  helper (and Auto TDP's 300 ms samples) are never blocked. The panel reads the status back on its next polls
  (up to 15 s), then reports the result.
- Not saved (Windows keeps the device state across restarts).
- No device found: the toggle is hidden.
- Description: "Turns the touch screen off, e.g. to avoid stray touches."

### 3.6 Auto SR (Controller & screen section)

- Toggle "Auto SR", single press, showing the live value. It is Windows' global "Automatic super resolution" switch
  (Settings > System > Display > Graphics); the per-game "Always use with this game" stays in Game Bar.
- **Storage (third-party guides, not Microsoft docs):** `HKCU\Software\Microsoft\DirectX\UserGpuPreferences`, REG_SZ
  `DirectXUserGlobalSettings`, a `;`-separated list of `Name=Value;` pairs. `DXGIEffects=1028` = on,
  `DXGIEffects=1024` = off.
  - Owner's Ally on 2026-10-24: `VRROptimizeEnable=1;DXGIEffects=1028;SwapEffectUpgradeEnable=1;AutoHDREnable=0;`.
  - Before building, the mapping is confirmed live: flip the switch in Windows Settings and read the value back.
- **Write:** only the `DXGIEffects` pair is replaced (or appended when missing). Every other pair is kept as is, in
  order. Any value other than 1024/1028 shows as "Set in Windows" and is only replaced when the owner presses the
  toggle.
- **User hive:** the value is written under the signed-in user's HKCU, not the helper's own account. If the helper
  runs as another account (e.g. SYSTEM), it writes `HKEY_USERS\<signed-in user SID>`; the plan confirms which
  account the helper runs as.
- **Takes effect:** for games started after the change (a game already running keeps its state).
- Not saved by Overdrive (Windows keeps it). Hidden when the Auto SR package `Microsoft.AutoSuperResolution2` is not
  installed.
- Description: "Windows Automatic Super Resolution for games started from now on (per-game choice stays in Game Bar)."

## 4. Why Auto differs from G-Helper

G-Helper's Auto is Gamepad when a game draws frames with GPU use over 15 %, else Mouse. In Desktop (Mouse) mode the
controller sends mouse and keyboard input, and Steam Big Picture, the owner's home screen, can only be steered with a
gamepad. G-Helper's rule would switch to Mouse whenever no game is running, leaving Big Picture unusable with the
controller. The owner-approved rule keeps Gamepad whenever Steam is in front.

## 5. Architecture

### 5.1 Helper commands (C# 5, ported from G-Helper, marked in file headers and THIRD-PARTY-NOTICES)

- `get_cpu_boost` -> `{ac: int, dc: int}`; `set_cpu_boost {value: 0..6}`.
  - `PowerReadACValueIndex` / `PowerWriteACValueIndex` / `PowerWriteDCValueIndex` on the active scheme, then
    `PowerSetActiveScheme`.
- `get_power_mode` -> `{overlay: guid}`; `set_power_mode {overlay: one of the three GUIDs}`.
  - `PowerGetEffectiveOverlayScheme` / `PowerSetActiveOverlayScheme`.
  - Any other GUID is refused.
- `controller_info` -> `{found: bool, pid: hex, xbox: bool}`.
- `set_controller_mode {mode: "gamepad"|"desktop"}` (sent as report mode byte 1 or 3).
- `set_vibration {strength: 0..100}`.
- Raw Win32 HID: `SetupDiGetClassDevs` (HID class), `HidD_GetAttributes`, `HidP_GetCaps` / feature report length,
  `HidD_SetFeature`.
  - G-Helper uses HidSharp; it is not in the in-box .NET Framework, so the helper calls the Win32 HID API directly.
  - Only VID 0x0B05 with a PID in G-Helper's list and a collection supporting feature report 0x5A is written.
  - The report is padded to that collection's feature report length.
- `get_touchscreen` -> `{found: bool, enabled: bool, instanceId}`; `set_touchscreen {enabled: bool}`.
  - PowerShell `Get-PnpDevice` / `Enable-PnpDevice` / `Disable-PnpDevice`, as G-Helper. `set_touchscreen` starts
    the command without waiting; the process is killed after 15 s if still running; `get_touchscreen` waits at most
    5 s.
- `get_auto_sr` -> `{installed: bool, value: int|null}`; `set_auto_sr {on: bool}`: reads/writes the
  `DXGIEffects` pair as in 3.6, keeping all other pairs.
- `foreground` -> `{exe: string}`: the process name of the foreground window (`GetForegroundWindow`,
  `GetWindowThreadProcessId`, `QueryFullProcessImageName`). Read only. Used by Auto controller mode.

### 5.2 Python

- **New module `py_modules/overdrive/controls.py`** for CPU Boost, power mode, touchscreen and the controller
  setters. Each returns the usual `{ok, message}` replies and goes through `_refuse_write` / `user_change` like the
  other setters.
- **New `ControllerAuto` loop** (300 ms, only while the mode is auto).
  - It shares the 300 ms frame sample with Auto TDP when both run: one sampler, two consumers, so the helper is
    asked once per tick.
  - It calls `foreground` and `frame_sample`, applies the 3-in-a-row rule, and writes only changes.
  - It stops with Gamepad written on unload.
- **Settings:** `controller_mode`, `grip_vibration`, `cpu_boost_on_value`.
  - Controller mode and vibration are added to the reapply.
  - The Reapplier also reapplies them after a helper restart, because the controller forgets on sleep.
- **get_state adds:**
  - `cpu_boost {on, supported}`;
  - `power_mode {value: "eco"|"balanced"|"best"|"other", supported}`;
  - `controller {mode, found}`;
  - `grip_vibration`;
  - `touchscreen {found, enabled}`;
  - `auto_sr {installed, on}` (`on` null when the value is neither 1024 nor 1028).

### 5.3 Panel

- **Performance section:** the CPU Boost toggle under Auto TDP, and the Battery performance picker under it.
- **New section "Controller & screen":**
  - the Controller mode picker, with its per-choice description;
  - the Grip vibration toggle, hidden when no controller is found;
  - the Touchscreen toggle, hidden when no touchscreen is found;
  - the Auto SR toggle, hidden when the Auto SR package is not installed.
- Toggles follow the Armoury/Auto TDP pattern (fed the stored or live state, single press, fall back on failure).

## 6. Errors

- **No ASUS controller found:** the controller rows show "Controller not found"; nothing is written.
- **A refused or failed write:** the panel shows the message in the row's status line. Nothing is saved for a
  refused write.
- **Touchscreen command timeout:** "Touchscreen did not respond", and the live status is read back.
- **Power API errors:** the Windows error code is shown in the message.

## 7. Testing

- **Helper tests** with fakes for the power API, HID writer, PnP runner and foreground reader:
  - exact report bytes for each mode and vibration strength (including the Xbox 0x1C line);
  - only matching devices are written;
  - unknown overlay GUIDs and out-of-range values are refused;
  - read-only commands write nothing.
- **Python tests:**
  - the setters, saving and reapply;
  - Auto rule sequences (Steam in front, game drawing frames, a browser in front, flicker under 3 samples);
  - the shared sampler with Auto TDP;
  - Gamepad written on unload;
  - CPU Boost on-value memory (default 2, a saved value reused).
- **Panel node tests** for the description texts and the "other" power mode label.
- **Live on the Ally:**
  - CPU Boost off: powercfg shows 0, and a full-load run is slower. On: powercfg shows 2.
  - Each Battery performance choice: the registry overlay value changes for the current source.
  - Controller Desktop: the right stick moves the mouse on the desktop. Gamepad: Big Picture steers again.
  - Auto: switching to a browser gives Desktop; back to Steam gives Gamepad within about 1 s.
  - Grip vibration off: a game's rumble stops (owner check by hand). On: it returns.
  - Touchscreen off: touches do nothing. On: they work again.
  - Decky restart while in Desktop leaves the controller in Gamepad, and the panel shows Gamepad after the restart.
  - Auto SR off/on: Windows Settings > Graphics shows the same state, other DirectXUserGlobalSettings pairs are
    unchanged, and a DirectX 11/12 game started afterwards follows it.

## 8. Review focus

1. Auto never leaves Big Picture in Desktop mode: Steam in front, Steam's overlay and Steam's keyboard all count as
   Steam.
2. Unload, Decky restart and plugin errors put the controller back to Gamepad.
3. HID writes go only to the ASUS controller collection with report 0x5A, never to other HID devices.
4. CPU Boost "On" never writes a value outside 0..6, and Off/On round-trips the user's earlier value.
5. A touchscreen command never blocks the helper: `set_touchscreen` returns at once, and a hung PowerShell is killed
   after 15 s.

## 9. Out of scope

Vibration strength slider, enhanced vibration (G-Helper `vibra_enhanced`), button remapping, deadzones, disabling
the Xbox controller, linking Windows power mode to Overdrive's performance modes, Control Center changes.
