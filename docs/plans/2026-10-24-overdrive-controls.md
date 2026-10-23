# Overdrive New Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add six controls to Overdrive:
- CPU Boost;
- Battery performance (Windows power mode);
- Controller mode (Gamepad / Desktop / Auto);
- Grip vibration;
- Touchscreen;
- Auto SR.

**Architecture:**
- **Helper:** new read/write commands behind small hardware interfaces (`IWinPower`, `IAsusHid`, `ISystemControls`) with fakes for tests, in the existing `HelperCore` dispatch.
- **Python:** a `controls.py` module for the setters and getters, settings and reapply additions, and a `ControllerAuto` loop for Auto mode.
- **Panel:** two rows in Performance and a new "Controller & screen" section.

**Tech Stack:**
- Helper: C# 5 on .NET Framework 4.8, built with the in-box csc via `helper/build.ps1`.
- Backend: Python 3.13 (asyncio).
- Panel: TypeScript/React with `@decky/ui`.

**Spec:** `docs/specs/2026-10-24-overdrive-controls-design.md`. Read it first; it is the authority.

## Global Constraints

- **CPU Boost:** boost-mode setting `be337238-0d82-4146-a960-4f3749d470c7` in subgroup `54533251-82be-4824-96c1-47b60b740d00` of the active scheme.
  - Values 0..6 only.
  - Off = 0. On = the saved `cpu_boost_on_value` (default 2).
  - AC and DC are written alike, then `PowerSetActiveScheme`.
- **Power mode overlays:**
  - Eco = `961cc777-2547-4f9d-8174-7d86181b8a7a`;
  - Balanced = `00000000-0000-0000-0000-000000000000`;
  - Best performance = `ded574b5-45a0-4f42-8737-46345c09c238`;
  - any other value = "other".
- **Controller reports:**
  - The controller is HID VID 0x0B05 with a PID in G-Helper's `MAIN_AURA_PIDS` (0x1B4C on the owner's Ally), on the collection that has feature report 0x5A.
  - Each report is padded to that collection's feature report length.
  - Mode: `[0x5A,0xD1,0x01,0x01,m]`, with m 1 = Gamepad, 3 = Desktop.
  - Vibration: `[0x5A,0xD1,0x06,0x02,s,s]`, plus `[0x5A,0xD1,0x1C,0x02,s,s]` when the product name contains "Xbox"; s = 100 on, 0 off.
- **Auto mode:**
  - Gamepad when the foreground exe is `steam.exe` or `steamwebhelper.exe` (case-insensitive), or when `frame_sample` fps > 0; otherwise Desktop.
  - Decided every 300 ms; switches after 3 equal decisions in a row; only a change is written.
- **Safety:** on unload, Gamepad is written before the helper stops. No controller write while an Armoury switch runs (`_busy()`).
- **Touchscreen:**
  - The PnP device has a FriendlyName like `*touch*screen*`.
  - `set_touchscreen` starts `Enable-PnpDevice`/`Disable-PnpDevice` and returns at once; a still-running process is killed after 15 s.
  - `get_touchscreen` waits at most 5 s.
- **Auto SR:**
  - `HKCU\Software\Microsoft\DirectX\UserGpuPreferences`, REG_SZ `DirectXUserGlobalSettings`. `DXGIEffects=1028` on, `1024` off (confirmed live 2026-10-24: Windows Settings shows On at 1028).
  - Only that pair is replaced (appended if missing); all other pairs are kept, in order.
  - The helper runs as the signed-in user (Musika, session 1, confirmed live), so it writes its own HKCU.
  - Installed = a folder `%ProgramFiles%\WindowsApps\Microsoft.AutoSuperResolution2_*` exists.
- **Saved settings:** `controller_mode` ("gamepad"|"desktop"|"auto", default "gamepad"), `grip_vibration` (bool, default true), `cpu_boost_on_value` (int 1..6, default 2). Windows keeps everything else.
- **Reapply:** controller mode and vibration are reapplied in the Reapplier's apply (start, resume, AC change, rerun) and after a helper restart.
- **Attribution:** new G-Helper code is marked in its file headers and in THIRD-PARTY-NOTICES:
  - `PowerNative.cs` (boost, overlay);
  - `AllyControl.cs` (mode, vibration bytes);
  - `AsusHid.cs` (`INPUT_ID`, PIDs);
  - `TouchscreenHelper.cs`.
- **Test commands:**
  - Python: `"X:/Projects/Decky Loader/decky-loader/backend/.venv/Scripts/python.exe" -m pytest -q -p no:cacheprovider --basetemp <scratch dir> tests`
  - Helper: `powershell -NoProfile -ExecutionPolicy Bypass -File helper/build.ps1 -Tests`
  - Panel: `node --test tests/frontend/*.test.mjs`, `npx tsc --noEmit`, `npm run build`
- **Subagents** never install, restart Decky, run the real helper or touch the device. Task 8 is done by the controller.
- **Commit trailer:** `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Auto never strands Big Picture in Desktop mode.** The Steam overlay and Steam's keyboard run as
   `steamwebhelper.exe`, so they count as Steam (Task 5 test `test_auto_steam_overlay_keeps_gamepad`).
2. **Unload, or a crash in the Auto loop, puts the controller back to Gamepad** (Task 6 test
   `test_unload_writes_gamepad_first`; Task 5 test `test_auto_loop_exception_writes_gamepad`).
3. **HID writes reach only the ASUS 0x5A collection.** No device found means no write and `found=false` (Task 2 test
   `ControllerHid_OnlyMatchingCollection`).
4. **The Auto SR write keeps every other DirectX pair.** An empty or missing value gets just
   `DXGIEffects=...;` (Task 3 test `AutoSr_KeepsOtherPairs`).
5. **CPU Boost Off then On returns the user's earlier non-zero value.** A value read as 0 at first On gives 2 (Task 4
   test `test_cpu_boost_round_trip`).

---

### Task 1: Helper, Windows power (CPU Boost, power mode)

**Files:**
- Create: `helper/src/WinPower.cs`
- Modify: `helper/src/Hardware.cs` (IWinPower), `helper/src/Protocol.cs`, `helper/src/HelperMain.cs`
- Modify: `helper/tests/FakeHardware.cs`, `helper/tests/HelperTests.cs`, `THIRD-PARTY-NOTICES.md`

**Interfaces:**
- Produces `IWinPower`, with:
  - `int[] GetCpuBoost()` (AC, DC; null on error);
  - `bool SetCpuBoost(int value, out string err)`;
  - `string GetOverlay()` (GUID lowercase D-format, null on error);
  - `bool SetOverlay(string guid, out string err)`.
- Produces commands:
  - `get_cpu_boost` -> `{ac, dc}`;
  - `set_cpu_boost {value}`, which refuses outside 0..6;
  - `get_power_mode` -> `{overlay, mode: "eco"|"balanced"|"best"|"other"}`;
  - `set_power_mode {mode: "eco"|"balanced"|"best"}`, which refuses anything else.
  - The read commands go in `ReadCommands`.
- `HelperCore` gains the extra dependencies through a new constructor overload. The existing constructors pass
  defaults, so current tests stay unchanged.

- [ ] **Step 1: Failing tests:**
  - `WinPower_GetSetBoost`;
  - `WinPower_BoostOutOfRangeRefused`;
  - `WinPower_ModeMapping` (the 3 GUIDs to names, and an unknown GUID to "other");
  - `WinPower_SetModeOnlyThree`;
  - `WinPower_ReadsWriteNothing`.
- [ ] **Step 2: Run `helper/build.ps1 -Tests`.** Expected: compile failure or FAIL.
- [ ] **Step 3: Implement.** Port G-Helper `PowerNative` P/Invokes (`PowerGetActiveScheme`, `PowerRead/WriteACValueIndex`, `PowerWriteDCValueIndex`, `PowerSetActiveScheme`, `PowerGetEffectiveOverlayScheme`, `PowerSetActiveOverlayScheme`) to C# 5. Free the scheme pointer with `LocalFree`.
- [ ] **Step 4: Run.** Expected: all pass, BUILD OK.
- [ ] **Step 5: Commit** "Helper: CPU Boost and Windows power mode".

### Task 2: Helper, ASUS controller (mode, vibration)

**Files:**
- Create: `helper/src/AsusHid.cs`
- Modify: `Hardware.cs` (IAsusHid), `Protocol.cs`, `HelperMain.cs`, the test files, `THIRD-PARTY-NOTICES.md`

**Interfaces:**
- Produces `IAsusHid`, with:
  - `HidInfo Find()`, where `HidInfo {bool Found; int Pid; int FeatureLength}`;
  - `bool SetFeature(byte[] report, out string err)`.
- Produces commands:
  - `controller_info` -> `{found, pid: "0x1B4C", xbox: bool}`, where xbox = product name contains "Xbox";
  - `set_controller_mode {mode: "gamepad"|"desktop"}`;
  - `set_vibration {strength: 0..100}`.
- Byte layouts are exactly as in Global Constraints. Not found: refused with "Controller not found".

- [ ] **Step 1: Failing tests:**
  - `Controller_ModeBytes` (gamepad gives the m=1 report, desktop the m=3 report, both padded to FeatureLength);
  - `Controller_VibrationBytes` (non-Xbox writes one report; Xbox writes the 0x06 then the 0x1C report);
  - `ControllerHid_OnlyMatchingCollection` (the fake lists several devices; only the VID/PID/0x5A one is used);
  - `Controller_NotFoundRefused`;
  - `Controller_InvalidArgsRefused`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
  - The real `AsusHid` uses `SetupDiGetClassDevs(HID guid)`, `SetupDiEnumDeviceInterfaces` and `SetupDiGetDeviceInterfaceDetail`.
  - For each path: `CreateFile` (read/write share), then `HidD_GetAttributes` (VID/PID).
  - It then reads the preparsed data (`HidD_GetPreparsedData`, `HidP_GetCaps`, `FeatureReportByteLength`) and the feature value caps, and keeps the collection whose feature report IDs include 0x5A.
  - The write is `HidD_SetFeature`.
  - The PID list is copied from G-Helper `AsusHid.MAIN_AURA_PIDS`.
- [ ] **Step 4: Run.** Expected: all pass.
- [ ] **Step 5: Commit** "Helper: ASUS controller mode and grip vibration".

### Task 3: Helper, touchscreen, Auto SR, foreground

**Files:**
- Create: `helper/src/SystemControls.cs`, `helper/src/AutoSr.cs` (the pure pair editor)
- Modify: `Hardware.cs` (ISystemControls), `Protocol.cs`, `HelperMain.cs`, the test files, `THIRD-PARTY-NOTICES.md`

**Interfaces:**
- Produces `AutoSr.Read(string settings) -> int?` and `AutoSr.Write(string settings, bool on) -> string`.
- Produces `ISystemControls`, with:
  - `TouchInfo GetTouchscreen()` (`{Found, Enabled, InstanceId}`, 5 s cap);
  - `bool StartSetTouchscreen(bool enabled, out string err)` (non-blocking; kills the process after 15 s);
  - `string ReadGpuSettings()`;
  - `bool WriteGpuSettings(string v, out string err)`;
  - `bool AutoSrInstalled()`;
  - `string ForegroundExe()` (lowercase file name, or "").
- Produces commands:
  - `get_touchscreen`, `set_touchscreen {enabled}`;
  - `get_auto_sr` -> `{installed, value}`, `set_auto_sr {on}`;
  - `foreground` -> `{exe}`.
  - The reads go in `ReadCommands`.

- [ ] **Step 1: Failing tests:**
  - `AutoSr_ReadValue` (1028, 1024, missing gives null);
  - `AutoSr_KeepsOtherPairs`, using the owner's exact string `VRROptimizeEnable=1;DXGIEffects=1028;SwapEffectUpgradeEnable=1;AutoHDREnable=0;`; off gives the same string with 1024;
  - `AutoSr_AppendsWhenMissing` (an empty string gives `DXGIEffects=1024;`);
  - `Touchscreen_SetReturnsAtOnce` (the fake records the start without blocking);
  - `Touchscreen_NotFound`;
  - `Foreground_Lowercase`;
  - `AutoSr_NotInstalledRefused`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
  - The real `SystemControls` runs PowerShell (`-NoProfile -NonInteractive -Command`) for PnP, as G-Helper `TouchscreenHelper` does.
  - It uses `Microsoft.Win32.Registry.CurrentUser` for the GPU settings.
  - It uses `GetForegroundWindow`, `GetWindowThreadProcessId`, `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` and `QueryFullProcessImageName` for the foreground app.
  - It uses `Directory.GetDirectories(%ProgramFiles%\WindowsApps, "Microsoft.AutoSuperResolution2_*")` for the installed check; an access error counts as not installed.
- [ ] **Step 4: Run.** Expected: all pass.
- [ ] **Step 5: Commit** "Helper: touchscreen, Auto SR and foreground app".

### Task 4: Python, settings and controls

**Files:**
- Create: `py_modules/overdrive/controls.py`
- Modify: `py_modules/overdrive/settings.py`, `py_modules/overdrive/reapply.py`, `tests/fake_helper.py`
- Test: `tests/test_controls.py`, `tests/test_settings.py`, `tests/test_reapply.py`

**Interfaces:**
- Produces settings: `controller_mode`, `grip_vibration` and `cpu_boost_on_value`, validated on load (bad values fall back to the defaults).
- Produces `controls.py`. Each function returns `{"ok", "message", ...}`:
  - `async read_controls(client) -> dict` with keys `cpu_boost {on, supported}`, `power_mode {value, supported}`, `controller {found}`, `touchscreen {found, enabled}`, `auto_sr {installed, on}`;
  - `async set_cpu_boost(client, settings, on: bool) -> dict`, which saves the prior non-zero value before Off;
  - `async set_power_mode(client, value: str) -> dict`;
  - `async set_touchscreen(client, enabled: bool) -> dict`;
  - `async set_auto_sr(client, on: bool) -> dict`;
  - `async apply_controller(client, settings, mode: Optional[str] = None) -> dict`, which writes the mode ("auto" writes Gamepad as its starting state) and the vibration.
- The Reapplier's `_apply_all` calls `apply_controller` after the FPS step. It skips when no controller is found. A failure joins the reapply's warnings.

- [ ] **Step 1: Failing tests:**
  - `test_cpu_boost_round_trip` (boost read 3, Off saves 3 and writes 0, On writes 3; first On from a 0 read writes 2);
  - `test_power_mode_values`;
  - `test_auto_sr_on_off`;
  - `test_touchscreen_toggle`;
  - `test_apply_controller_writes_mode_and_vibration`;
  - `test_reapply_includes_controller`;
  - `test_settings_new_fields_defaults_and_bad_values`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the whole suite. Expected: PASS.
- [ ] **Step 5: Commit** "Controls: CPU Boost, power mode, touchscreen, Auto SR, controller; saved and reapplied".

### Task 5: Python, `ControllerAuto` loop

**Files:**
- Create: `py_modules/overdrive/controller_auto.py`
- Modify: `py_modules/overdrive/auto_tdp_loop.py` (expose the last sample)
- Test: `tests/test_controller_auto.py`

**Interfaces:**
- Consumes `AutoTdpLoop`. Add the attribute `last_sample: Optional[tuple]` = `(fps, monotonic_time)`, set on every good `frame_sample`.
- Produces `class ControllerAuto`, with:
  - `__init__(client, reapplier, autotdp, clock=time, sleep=asyncio.sleep, logger=None)`;
  - `start()`;
  - `async stop()`, which writes Gamepad;
  - `async run_once()`;
  - `decide(exe: str, fps: Optional[float]) -> str`.
- Each tick it calls `foreground`. It uses `autotdp.last_sample` when it is younger than 0.6 s; otherwise it calls `frame_sample` itself (one sampler shared, per spec 5.2).
- Writes take `reapplier.lock` and are skipped while busy.

- [ ] **Step 1: Failing tests:**
  - `test_decide_rules` (steam.exe gives gamepad, steamwebhelper.exe gives gamepad, game.exe with fps 60 gives gamepad, msedge.exe with fps 0 gives desktop);
  - `test_switch_after_three` (two desktop decisions then gamepad: no write; three: one write);
  - `test_auto_steam_overlay_keeps_gamepad`;
  - `test_uses_autotdp_sample_when_fresh` (no extra `frame_sample` call);
  - `test_auto_loop_exception_writes_gamepad`;
  - `test_skips_while_busy`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the whole suite. Expected: PASS.
- [ ] **Step 5: Commit** "Controller Auto mode (Steam and games keep Gamepad)".

### Task 6: Plugin wiring (`main.py`)

**Files:**
- Modify: `main.py`, `tests/test_main.py`

**Interfaces:**
- Produces callables, each refused like other writes:
  - `set_cpu_boost(on)`;
  - `set_power_mode(value)`;
  - `set_controller_mode(mode)`, which saves, applies, and starts or stops `ControllerAuto`;
  - `set_grip_vibration(on)`, which saves and applies;
  - `set_touchscreen(on)`;
  - `set_auto_sr(on)`.
- `get_state` merges `read_controls(...)` plus `controller {mode, found}` and `grip_vibration`.
- `_startup_sequence` starts `ControllerAuto` when the mode is auto.
- `_unload`: `await controller_auto.stop()` (Gamepad), then the Auto TDP stop, then `reapplier.stop()` and `client.stop()`.
- R10 holds: no lock-taking stop inside `user_change()`.

- [ ] **Step 1: Failing tests:**
  - `test_unload_writes_gamepad_first`;
  - `test_set_controller_mode_auto_starts_loop`;
  - `test_new_setters_refused_while_busy`;
  - `test_state_reports_new_controls`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the whole suite. Expected: PASS.
- [ ] **Step 5: Commit** "Plugin: new controls wired; Gamepad on unload".

### Task 7: Panel

**Files:**
- Modify: `src/curve.ts`, `src/index.tsx`, `tests/frontend/curve.test.mjs`

**Interfaces:**
- Consumes the `get_state` keys and callables from Task 6.
- Produces in `curve.ts`:
  - `powerModeLabel(value)` ("Eco" / "Balanced" / "Best performance" / "Set in Windows");
  - `controllerModeDescription(mode)` with the spec 3.3 texts.

**Layout:**
- **Performance section:** the "CPU Boost" toggle under Auto TDP, then the "Battery performance" InlinePicker (Eco, Balanced, Best performance).
- **New section "Controller & screen":**
  - the Controller mode InlinePicker;
  - the "Grip vibration" toggle;
  - the "Touchscreen" toggle;
  - the "Auto SR" toggle.
- Descriptions are verbatim from spec 3.1 to 3.6.
- Hidden rows: grip vibration when no controller, touchscreen when none, Auto SR when not installed.
- Toggles follow the Auto TDP pattern (single press, fed the stored or live value).

- [ ] **Step 1: Failing node tests** for the two functions, with the exact strings.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** node, tsc and build. Expected: clean.
- [ ] **Step 5: Commit** "Panel: CPU Boost, Battery performance, Controller & screen".

### Task 8: Install and live test (controller only)

- [ ] Install with `tools/install-dev.ps1` elevated, check the hashes, and check that Decky answers.
- [ ] **CPU Boost:** Off makes `powercfg /qh` show 0 for AC/DC; On shows the previous value (2 the first time).
- [ ] **Battery performance:** each choice changes `ActiveOverlay*PowerScheme` for the current source. Put the owner's Best performance back.
- [ ] **Auto SR:** off makes Windows Settings show Off and the other pairs unchanged. Put it back On.
- [ ] **Touchscreen:** off and on, read via `Get-PnpDevice`. The owner confirms touch by hand.
- [ ] **Controller:** Desktop, then the owner confirms the right stick moves the mouse; back to Gamepad and Big Picture steers. Auto: a browser in front gives Desktop, and Steam in front gives Gamepad within about 1 s. A Decky restart while in Desktop gives Gamepad.
- [ ] **Grip vibration:** the owner checks rumble off/on in a game.
- [ ] Report the measured results to the owner and restore their settings.
