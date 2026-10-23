# Overdrive Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Decky plugin "Overdrive" that sets the ROG Ally's performance mode, power limits, FPS limit and fan curve, shows temperature/fan readout, and can replace Armoury Crate, using G-Helper's hardware code.

**Architecture:** Decky frontend (TypeScript, rollup) -> Python backend (`main.py` + `py_modules/overdrive/`) -> one long-lived `bin\asus-helper.exe` (C# 5, .NET Framework 4.8, ported from G-Helper) speaking JSON lines over stdin/stdout. The helper validates every request itself and talks to `\\.\ATKACPI`, AMD ADL and the Windows service manager.

**Tech Stack:** C# 5 via `C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe`; Python 3.13 (Decky's runtime) with pytest; TypeScript + `@decky/ui` + `@decky/api` + rollup (same versions as `..\windows-quick-settings\package.json`).

**Spec:** `docs\specs\2026-10-23-overdrive-stage1-design.md` (read it first; it is the authority).

## Global Constraints

- License GPL-3.0-only. Every file copied or ported from G-Helper keeps its original header lines and gains `// Modified for Overdrive (ported to C# 5 / .NET Framework 4.8), 2026`. Record the G-Helper commit SHA used in `helper\GHELPER_COMMIT` (take it with `gh api repos/seerge/g-helper/commits/main --jq .sha` at the time of copying).
- Ported, not reinvented: hardware IDs, byte layouts, conversions and fan minimums come from G-Helper's source at that commit (`app/AsusACPI.cs`, `app/Fans.cs`, `app/Gpu/AMD/AmdAdl2.cs`, `app/Gpu/AMD/AmdGpuControl.cs`, `app/Helpers/AsusService.cs`, `app/AppConfig.cs`). Never from memory.
- Spec values (verbatim): PerformanceMode 0x00120075 (Balanced 0, Turbo 1, Silent 2); SPL 0x001200A3, sPPT 0x001200A0, fPPT 0x001200C1; Temp_CPU 0x00120094; CPU_Fan 0x00110013; power 5..50 W each, fPPT >= sPPT >= SPL; FPS limit 0 (off) or one of 30, 40, 45, 50, 60, 75, 90, 120; fan curve 8 points.
- Ally detection: BIOS SystemProductName contains "RC7" (`HKLM\HARDWARE\DESCRIPTION\System\BIOS`, value `SystemProductName`). Owner device: "ROG Xbox Ally X RC73XA_RC73XA".
- Not named G-Helper; no ASUS/ROG/Ally/Armoury trademark in the plugin name. Plugin name "Overdrive", author "Musika".
- Never touch the battery charge limit (device 0x00120057); Windows Quick Settings owns it.
- Implementers never write hardware on this PC: no helper `set_*`, no `armoury_stop/restore`, no service changes, no copying into `C:\ProgramData\decky`, no Decky restarts. Read-only `--probe` is run by the controller, not by subagents.
- Commits by explicit paths, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Local repo only; never push.

## Review Focus

1. Sleep/resume and AC plug/unplug while a value is being applied: reapply must not race a user change (Task 5 test `reapply_does_not_overwrite_newer_user_change`).
2. Helper killed mid-request (Defender scan, crash): pending calls resolve with "Overdrive helper restarted, try again", never hang (Task 5 test `pending_request_fails_on_helper_exit`).
3. Settings file missing, empty or hand-edited with out-of-range values: load defaults for bad fields, keep good ones, never send invalid values (Task 5 test `corrupt_settings_fall_back_per_field`).
4. Armoury switch turned off after a reboot: restore uses the recorded start types from settings, not current state (Task 4 test `restore_uses_recorded_state_after_restart`).
5. Fan curve edited with the controller to a non-monotonic shape: the panel blocks Apply and says why before calling the backend (Task 6 test `curve_editor_blocks_decreasing_points`).

---

### Task 1: Plugin scaffold and device check

**Files:**
- Create: `plugin.json`, `package.json`, `rollup.config.js`, `tsconfig.json`, `src\index.tsx`, `main.py`, `py_modules\overdrive\__init__.py`, `py_modules\overdrive\device.py`, `tests\conftest.py`, `tests\test_device.py`, `README.md`, `THIRD-PARTY-NOTICES.md`, `.gitignore`
- Copy structure from `..\windows-quick-settings` (plugin.json shape, package.json scripts/devDependencies/dependencies, rollup.config.js, tsconfig.json, conftest's `decky` stand-in).

**Interfaces:**
- Produces: `device.is_ally(read_product=None) -> bool`; `device.product_name(read_product=None) -> str`; `Plugin.get_support()` -> `{"supported": bool, "product": str}`.

- [ ] Step 1: tests `test_ally_x_detected` (read_product returns "ROG Xbox Ally X RC73XA_RC73XA" -> True), `test_rc71_detected` ("ROG Ally RC71L" -> True), `test_other_device` ("Legion Go 8APU1" -> False), `test_read_failure_is_unsupported` (reader raises -> False, product "").
- [ ] Step 2: run `python -m pytest tests -q` -> FAIL (module missing).
- [ ] Step 3: implement `device.py` (winreg read of the BIOS key; injectable reader) and `main.py` `Plugin` with `get_support`; `src\index.tsx` renders the title "Overdrive" and, when unsupported, "Overdrive supports the ROG Ally family only".
- [ ] Step 4: `python -m pytest tests -q` PASS; `npm install` then `npm run build` creates `dist\index.js`.
- [ ] Step 5: commit "Overdrive: plugin scaffold and Ally check".

### Task 2: Helper core (protocol, validation, fakes)

**Files:**
- Create: `helper\src\HelperMain.cs`, `helper\src\Protocol.cs`, `helper\src\Validation.cs`, `helper\src\Hardware.cs` (interfaces), `helper\tests\HelperTests.cs`, `helper\tests\FakeHardware.cs`, `helper\build.ps1`
- `build.ps1` compiles `bin\asus-helper.exe` (`/target:exe /platform:x64 /optimize`) and with `-Tests` compiles and runs `helper\out\HelperTests.exe` (exit code = failures). Use `System.Web.Extensions` JavaScriptSerializer for JSON like the dashboard does.

**Interfaces:**
- Produces protocol: request `{"id":n,"cmd":"...","args":{...}}`, reply `{"id":n,"ok":bool,"message":"","data":...}`. Commands: `hello` -> `{"version":"0.1.0","ally":bool}`; `status`; `sensors`; `set_mode {mode: "silent"|"balanced"|"turbo"|"manual", spl?, sppt?, fppt?}`; `set_power {spl,sppt,fppt}`; `get_fans`; `set_fans {temps:[8 ints], speeds:[8 ints]}`; `reset_fans`; `get_fps_limit`; `set_fps_limit {fps}`; `armoury_state`; `armoury_stop`; `armoury_restore {recorded:{service:startType,...}, running:[names]}`.
- Produces C# interfaces: `IAcpi { int DeviceGet(uint id); int DeviceSet(uint id, int value); byte[] GetFanCurve(int fan, int mode); int SetFanCurve(int fan, byte[] curve); }`, `IAdl { int GetFpsLimit(); int SetFpsLimit(int fps); }`, `IServices { Dictionary<string,string> StartTypes(IEnumerable<string>); List<string> Running(IEnumerable<string>); void Stop(string name, string startType); void Start(string name, string startType); }`.
- `Validation.ClampPower(int spl, int sppt, int fppt) -> int[3]` (each to 5..50, then fppt >= sppt >= spl by raising the later ones); `Validation.CheckCurve(int[] temps, int[] speeds, out string reason) -> bool`.

- [ ] Step 1: tests `UnknownCommand_Refused`, `UnknownArg_Refused`, `Power_ClampedAndOrdered` (in 3,60,10 -> 5,50,50 after clamp then ordering), `Power_OrderFixed` (30,20,25 -> 30,30,30), `Fps_OnlyAllowedSteps` (33 refused, 0 and 60 accepted), `Curve_RejectsDecreasingFan`, `Curve_RejectsNonRisingTemps`, `Curve_RejectsBelowGHelperMinimums` (values copied from G-Helper), `Mode_Manual_AppliesPowerAfterBalanced` (fake records DeviceSet 0x00120075=0 then the three PPT ids), `Malformed_Json_ReturnsError_NotCrash`.
- [ ] Step 2: `powershell -NoProfile -ExecutionPolicy Bypass -File helper\build.ps1 -Tests` -> FAIL.
- [ ] Step 3: implement; the main loop reads lines until EOF, one reply per line, never throws out of the loop.
- [ ] Step 4: same command -> all PASS.
- [ ] Step 5: commit "Overdrive helper: protocol and validation".

### Task 3: Helper hardware layer (ACPI) and read-only probe

**Files:**
- Create: `helper\src\ghelper\AsusACPI.cs` (ported subset: device open, DeviceGet/DeviceSet, Get/SetFanCurve, the IDs used here), `helper\src\AcpiHardware.cs` (IAcpi over it), `helper\GHELPER_COMMIT`
- Modify: `helper\src\HelperMain.cs` (`--probe`), `THIRD-PARTY-NOTICES.md` (G-Helper entry with URL, commit, GPL-3.0)

**Interfaces:**
- Consumes: Task 2 `IAcpi`.
- Produces: `asus-helper.exe --probe` prints one JSON object `{"product","mode","spl","sppt","fppt","cpuTempC","fanRpm","fanCurve":{"temps":[...],"speeds":[...]},"errors":[...]}` and exits 0; it only calls DeviceGet/GetFanCurve.

- [ ] Step 1: tests `Probe_UsesOnlyReads` (FakeHardware throws on any write; probe completes), `Sensors_ConvertLikeGHelper` (fake raw values -> °C and RPM using G-Helper's conversions, cite the source lines in the test comment).
- [ ] Step 2: build `-Tests` -> FAIL.
- [ ] Step 3: port the code (C# 5: no string interpolation `$""`, no `?.`, no expression-bodied members, no nullable annotations), keep headers.
- [ ] Step 4: build `-Tests` PASS; `bin\asus-helper.exe` builds with no warnings.
- [ ] Step 5: commit "Overdrive helper: ACPI layer and read-only probe". (The controller runs `--probe` on the Ally and records the output before Task 4 enables any write path in the plugin.)

### Task 4: FPS limit (ADL) and Armoury Crate services

**Files:**
- Create: `helper\src\ghelper\AmdAdl2.cs`, `helper\src\ghelper\AmdGpuControl.cs` (ported subset for FRTC get/set), `helper\src\AdlHardware.cs`, `helper\src\ServiceHardware.cs`, `helper\src\ArmouryServices.cs` (the two lists from G-Helper `AsusService.cs`: `services` -> Disabled, `servicesAC` -> Manual)
- Test: `helper\tests\HelperTests.cs`

**Interfaces:**
- Produces: `armoury_state` -> `{"installed":bool,"startTypes":{...},"running":[...]}` ("installed" = ArmouryCrateSEService or ArmouryCrateService exists); `armoury_stop` -> stops and sets start types per G-Helper, returns the recorded `startTypes` and `running` BEFORE changes; `armoury_restore` puts back exactly the given start types and starts the given running names.

- [ ] Step 1: tests `Armoury_Stop_RecordsBeforeChanging`, `Armoury_Stop_SetsTypesLikeGHelper`, `restore_uses_recorded_state_after_restart` (restore with a recorded map different from current state applies the recorded one), `Armoury_PartialFailure_ReportsServiceName_AndRollsBack`, `Fps_Set_Then_Get_RoundTrips` (fake), `Adl_Missing_ReturnsClearError`.
- [ ] Step 2: build `-Tests` FAIL. Step 3: implement. Step 4: PASS. Step 5: commit "Overdrive helper: FPS limit and Armoury Crate services".

### Task 5: Python client, settings and reapply

**Files:**
- Create: `py_modules\overdrive\helper_client.py`, `py_modules\overdrive\settings.py`, `py_modules\overdrive\reapply.py`, `tests\test_helper_client.py`, `tests\test_settings.py`, `tests\test_reapply.py`, `tests\fake_helper.py`
- Modify: `main.py`

**Interfaces:**
- Produces `HelperClient(start_process=None)` with `async call(cmd: str, args: dict | None = None, timeout: float = 10.0) -> dict` (reply dict); restarts with backoff 1, 2, 4 s, max 5 starts per 10 min, then `unavailable_reason: str`.
- `Settings.load(path) -> Settings`, `.save()`; fields: `mode` ("balanced"), `spl`/`sppt`/`fppt` (30/30/30 defaults clamp 5..50), `fps` (0), `fan_preset` ("default"), `fan_curve` (None or {temps,speeds}), `default_curve` (read from device on first run), `armoury_replaced` (False), `armoury_recorded` ({}), `armoury_running` ([]); file `os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "overdrive.json")`.
- `Reapplier(client, settings, clock=time, power_source=None)`: polls every 5 s; triggers `apply_all()` on start, when AC line status changes (Win32 `GetSystemPowerStatus`), and on resume (wall-clock vs monotonic gap > 30 s). A user change bumps a generation counter; a reapply started before it is dropped.
- Fan presets (ruling, the spec names them but gives no values): "default" = `default_curve` read from the device on first run; "quiet" = default speeds minus 10 points each; "cool" = default speeds plus 15 points each; both clamped to 0..100 and then raised to the G-Helper minimums so `Validation.CheckCurve` passes; temps unchanged. Test `presets_derive_from_default_and_pass_validation`.
- `main.py` callables: `get_support`, `get_state` -> `{support, mode, spl, sppt, fppt, fps, fan_preset, fan_curve, armoury:{installed, replaced}, unavailable}`, `set_mode(mode, spl=None, sppt=None, fppt=None)`, `set_fps(fps)`, `set_fan_preset(name)`, `set_fan_curve(temps, speeds)`, `reset_defaults()`, `set_armoury_replaced(on)`, `get_sensors()`; `_main` starts the helper and reapplier only when `is_ally()`; `_unload` stops both.

- [ ] Step 1: tests `pending_request_fails_on_helper_exit`, `restarts_with_backoff_then_unavailable`, `corrupt_settings_fall_back_per_field`, `settings_roundtrip`, `reapply_on_ac_change`, `reapply_on_resume_gap`, `reapply_does_not_overwrite_newer_user_change`, `armoury_on_saves_recorded_state_before_success_reply`, `not_ally_starts_nothing`, `presets_derive_from_default_and_pass_validation`.
- [ ] Step 2: pytest FAIL. Step 3: implement (asyncio subprocess with CREATE_NO_WINDOW, path `os.path.join(plugin_dir, "bin", "asus-helper.exe")`). Step 4: pytest PASS. Step 5: commit "Overdrive: helper client, settings and reapply".

### Task 6: Panel

**Files:**
- Modify: `src\index.tsx`; Create: `src\curve.ts` (pure helpers) and `tests\frontend\curve.test.mjs` (node --test on the built helpers, or tsc-compiled pure module)

**Interfaces:**
- Consumes Task 5 callables. Layout per spec section 4: mode buttons (Silent / Balanced / Turbo / Manual); Manual shows three sliders "Sustained", "Slow boost", "Fast boost" (5..50 W, step 1, label "N W"); FPS limit dropdown (Off, 30, 40, 45, 50, 60, 75, 90, 120); Fans: preset dropdown (Armoury default, Quiet, Cool, Custom) + "Edit curve" with 8 rows of steppers (temp, fan %); live line "CPU 62 °C · Fan 3100 RPM" polled every 2 s only while mounted; toggle "Use Overdrive instead of Armoury Crate" (hidden when not installed); button "Reset to Armoury Crate defaults"; inline status text per action; banner when `unavailable`.
- `curve.ts`: `validateCurve(temps:number[], speeds:number[]): string | null` mirroring `Validation.CheckCurve` messages.

- [ ] Step 1: tests `curve_editor_blocks_decreasing_points`, `curve_editor_blocks_non_rising_temps`, `valid_curve_passes`.
- [ ] Step 2: FAIL. Step 3: implement (controller-friendly: every control is a Decky Field/Button/Toggle/Slider/Dropdown, no drag-only UI). Step 4: tests PASS, `npm run build` OK. Step 5: commit "Overdrive: panel".

### Task 7: Dashboard Armoury tile awareness (steam-home-app repo)

**Files (repo `X:\Projects\Decky Loader\steam-home-app`):**
- Modify: `dashboard\src\Engine\Parts\ArmouryPart.cs` (or the file that owns the Armoury tile), its tests.

**Interfaces:**
- Reads `C:\ProgramData\decky\settings\overdrive\overdrive.json` (Decky's per-plugin settings dir; confirm the exact path from how Decky builds `DECKY_PLUGIN_SETTINGS_DIR` in `..\..\decky-loader\backend`) read-only, no link following; when `armoury_replaced` is true the tile state is "ok", summary "Overdrive is managing the device", and no on/off actions are offered.

- [ ] Step 1: tests `Armoury_OverdriveManaging_ShowsSummaryAndNoActions`, `Armoury_OverdriveFileMissingOrBad_NormalBehaviour`.
- [ ] Step 2-4: fail, implement, `build.ps1 -Tests` PASS. Step 5: commit "Dashboard: show when Overdrive manages the device".

### Task 8: Packaging, install script and live checks (controller-run)

**Files:**
- Create: `tools\install-dev.ps1` (copies plugin.json, main.py, package.json, README.md, LICENSE, THIRD-PARTY-NOTICES.md, dist\, py_modules\, bin\ into `C:\ProgramData\decky\plugins\overdrive` and restarts Decky; run elevated by the controller only)

- [ ] Step 1: controller runs `bin\asus-helper.exe --probe` and records values in the ledger.
- [ ] Step 2: controller installs with one UAC prompt; owner checks: each mode (watts in the readout), Manual limits under load, FPS limit in a game, a fan curve, sleep/wake and charger reapply, Armoury switch on then off (services back as before).
- [ ] Step 3: commit "Overdrive: dev install script".
