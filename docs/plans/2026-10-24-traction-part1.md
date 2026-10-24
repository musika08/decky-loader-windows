# Traction Part 1 (the move) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the Traction Decky plugin with Controller mode, Grip vibration, Touch Screen and the Armoury Crate toggle, remove those controls from Overdrive (0.2.0), and make both plugins share one Armoury Crate record.

**Architecture:** Overdrive is refactored first so the code both plugins need (helper protocol kit, Armoury commands, HID, frame sampling, helper client, reapplier core, settings file handling, Armoury record and switch) sits in clearly listed shared files. Traction copies those files from a pinned Overdrive commit and adds its own small helper (`traction-helper.exe`) and backend. The Armoury record moves out of Overdrive's settings into `C:\ProgramData\decky\armoury\state.json`, guarded by a cross-process file lock.

**Tech Stack:** Python 3 (Decky plugin backend, pytest), TypeScript/React with `@decky/ui` (rollup), C# 5 on .NET Framework 4.8 built with the in-box `csc.exe`.

**Spec:** `X:\Projects\Decky Loader\plugins\traction\docs\specs\2026-10-24-traction-design.md` (sections 1, 2, 3, 5, 6, 7). Part 2 (section 4) gets its own plan after this ships.

## Global Constraints

- Repos: Overdrive `X:\Projects\Decky Loader\plugins\overdrive` (main), Traction `X:\Projects\Decky Loader\plugins\traction` (main). Each task names its repo.
- Commit author in both repos: `musika08 <225994527+musika08@users.noreply.github.com>`; every commit ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never push.
- C# 5 only (in-box csc), `/warnaserror+`. Python matches Overdrive's style (type hints, docstrings, no new dependencies).
- License GPL-3.0-only; G-Helper-derived files keep their header naming commit c09017453d9ed0c52261b67a48d02bd55232e6ac.
- Overdrive version `0.2.0`; Traction version `0.1.0`; Traction package/folder name `traction`; helper exe `bin\traction-helper.exe`; plugin flags `["root"]`.
- Panel switches are single-press (owner rule). No em-dashes in user-facing strings.
- Subagents never install, run elevated, or touch the live device; tests use fakes only.
- Behaviour that moves keeps today's rules exactly (Auto: 300 ms, 3 in a row, every doubt is Gamepad; Desktop resets to Gamepad at load; NOT_FOUND_RETRY 5 s).
- Copy strings fixed by the spec:
  - `Armoury Crate is running, so these settings may be overwritten.`
  - `Update Overdrive to 0.2.0 or newer to use Traction's controller settings.`
  - `Armoury Crate is being switched by another plugin, try again in a few minutes.`

## Rulings made while planning

- Ruling: the cross-plugin lock is an exclusive OS file lock (`msvcrt.locking`, LK_NBLCK) on `C:\ProgramData\decky\armoury\switch.lock`, retried for 2 s, instead of the spec's named mutex. Why: the switch is orchestrated by the Python backend across several helper calls, and a file lock is released by Windows if the process dies, same as a mutex. Cost if wrong: swap the lock implementation inside `armoury_record.py` only.
- Ruling: Traction applies its stored settings under the same rule as Overdrive (`user_set` true), not only while Armoury Crate is off; while the record says not replaced it shows the "Armoury Crate is running" note. Why: spec 3.1 says "like Overdrive", and Overdrive applies on `user_set`. Cost if wrong: one condition in Traction's reapplier.
- Ruling: shared files keep their paths in Overdrive and are listed in `overdrive/shared-files.txt`; shared C# moves to namespace `Ally.Shared` so Traction needs no rename. Python shared modules import each other only relatively. Cost if wrong: a namespace edit.
- Ruling: `controller_auto.py` is moved to Traction, not shared (Overdrive no longer needs it); its imports of `PERIOD`, `SAMPLE_TIMEOUT`, `ERROR_LOG_EVERY`, `_finite` come from a new Traction module `sampling.py` with the same values (0.3, 2.0, 60.0).

## Review Focus

1. A switch interrupted halfway (plugin killed during `armoury_stop`) with the record now in the shared file: the next load of EITHER plugin finishes or undoes it. Test in Task 3.
2. Both plugins loading at once at boot, both wanting to re-stop or recover: exactly one does it, the other skips with a log line and still shows the right state. Test in Task 3.
3. Overdrive 0.2.0 started on a box where its settings still hold `armoury_*` fields and no shared record exists: fields move to the record once, and a later load does not move them again or overwrite a record Traction wrote. Test in Task 3.
4. Traction's first load when Overdrive's settings file is missing, unreadable, or holds bad values: Traction uses defaults (gamepad, vibration on) and never fails to load. Test in Task 6.
5. An Overdrive older than 0.2.0 installed next to Traction: Traction writes nothing to the controller (not even at unload) and shows the update note. Test in Task 6.

---

### Task 1 (Overdrive): shared C# split

**Files:**
- Create: `helper/src/ProtocolKit.cs` (reply building, `GetBool`, `GetInt`, `GetIntArray`, `TryInt`, allowed-args check), `helper/src/ArmouryCommands.cs` (`ArmouryState`, `ArmouryStop`, `ArmouryRestore`, `ServiceStep`, `Ordered` moved out of `Protocol.cs`)
- Modify: `helper/src/Protocol.cs` (uses the two new files), `helper/src/AsusHid.cs`, `ArmouryServices.cs`, `ServiceHardware.cs`, `Validation.cs`, `AdlHardware.cs`, `ghelper/AmdAdl2.cs` (namespace `Ally.Shared`; `ghelper` keeps `Ally.Shared.GHelper`)
- Test: `helper/tests/HelperTests.cs`

**Interfaces:**
- Produces: `namespace Ally.Shared`: `static class ProtocolKit { string Reply(object id, bool ok, string message, object data); bool GetBool(...); bool GetInt(...); bool GetIntArray(...); string CheckArgs(Dictionary<string,string[]> commands, string cmd, Dictionary<string,object> args) }`; `class ArmouryCommands { ArmouryCommands(IServices services, Action<int> sleep); string State(object id); string Stop(object id); string Restore(object id, Dictionary<string,object> args) }`.

- [ ] **Step 1:** Move the code; no behaviour change. Every existing helper test must pass unchanged except `using` lines.
- [ ] **Step 2:** Run `powershell -File helper\build.ps1 -Tests`. Expected: build OK, 0 failed.
- [ ] **Step 3:** Commit `Helper: split protocol kit and Armoury commands into shared files`.

### Task 2 (Overdrive): shared Python split

**Files:**
- Create: `py_modules/overdrive/reapply_core.py` (`Reapplier` base: lock, `generation`, `applies`, `applying`, `rerun`, `user_change()`, `_step`, triggers, `tick`, `start`, `stop`, `Dropped`, `_StepClient`), `py_modules/overdrive/settings_file.py` (atomic save with `.bak`, unreadable-file copy, `_read_json`, `_is_link`)
- Modify: `py_modules/overdrive/reapply.py` (`class Reapplier(ReapplierCore)` keeps mode, fans, FPS, controller steps in `_apply_steps`), `py_modules/overdrive/settings.py` (uses `settings_file`)
- Test: existing `tests/test_reapply.py`, `tests/test_settings.py`; new `tests/test_reapply_core.py`

**Interfaces:**
- Produces: `class ReapplierCore: __init__(client, clock=time, power_source=None, logger=None, poll=5.0)`; abstract `async def _apply_steps(self, gen: int) -> "tuple[list[str], bool, bool]"` returning (problems, transport, mode_ok); `user_set` read through `self._should_apply() -> bool` (subclass overrides); `on_applied` called when the third value is true. `settings_file.save_json(path, data: dict) -> None`, `settings_file.load_json(path, log) -> "tuple[Optional[dict], str]"` (data, note).

- [ ] **Step 1: Write the failing test** `tests/test_reapply_core.py::test_subclass_steps_run_and_dropped_on_user_change`: a subclass whose `_apply_steps` awaits `self._step(gen, "x", {})` twice; a `user_change()` entered between the steps makes `apply_all()` return False and sets `rerun`.
- [ ] **Step 2:** Run `pytest tests/test_reapply_core.py -v`. Expected: FAIL (module missing).
- [ ] **Step 3:** Extract; Overdrive's `Reapplier` behaviour unchanged.
- [ ] **Step 4:** Run `pytest -q`. Expected: all pass (same count as before plus the new test).
- [ ] **Step 5:** Commit `Split reapplier core and settings file handling into shared modules`.

### Task 3 (Overdrive): shared Armoury record and switch

**Files:**
- Create: `py_modules/overdrive/armoury_record.py`, `py_modules/overdrive/armoury_switch.py`
- Modify: `main.py` (Armoury section 565-740 replaced by an `ArmourySwitch`), `py_modules/overdrive/settings.py` (drops `armoury_*` from what it writes; still reads them for migration), `py_modules/overdrive/reapply_core.py` (record trigger)
- Test: `tests/test_armoury_record.py`, `tests/test_armoury_switch.py`, `tests/test_main.py`

**Interfaces:**
- Produces:
  - `RECORD_DIR = r"C:\ProgramData\decky\armoury"`; `class ArmouryRecord: __init__(folder: str = RECORD_DIR, logger=None)`; fields `replaced: bool`, `recorded: dict`, `running: list`; `load() -> str` (note; validates names against `ArmouryServices` list and start types, recovers from `state.json.bak` like today's `_recover_armoury`); `save() -> None` (atomic via `settings_file.save_json`, raises OSError); `exists() -> bool`; `@contextlib.contextmanager lock(timeout: float = 2.0)` raising `ArmouryLocked` when not taken.
  - `class ArmourySwitch: __init__(client, record: ArmouryRecord, owner: str, logger=None, on_replaced: Callable[[], None] = None)`; `async set(on: bool) -> dict`; `async on_load() -> None` (P8 re-stop or recovery); `busy -> bool`; `until -> float`; `warning -> str`; `installed -> bool`. `owner` ("Overdrive" / "Traction") only fills messages ("Could not save {owner}'s settings").
  - `migrate_from_settings(record: ArmouryRecord, s) -> bool`: copies `armoury_replaced/recorded/running` into the record when `not record.exists()` and the settings hold any of them; True when it moved them.
  - `ReapplierCore` gains `record: Optional[ArmouryRecord]`; each tick reads `record.replaced` (cheap reload) and appends reason `"Armoury switched"` and sets `rerun` when it flips from False to True.
- Consumes: Task 2 `settings_file`, `ReapplierCore`.

- [ ] **Step 1: Write failing tests** (all with a temp folder, fake client):
  - `test_record_roundtrip_and_validation`: saves `{"replaced": True, "recorded": {"ArmouryCrateService": "Automatic", "Bogus": "Automatic"}, "running": ["ArmouryCrateService"]}`; reload keeps only the listed service.
  - `test_record_recovers_from_bak` when `state.json` is not valid JSON.
  - `test_lock_second_holder_gets_ArmouryLocked` (two `ArmouryRecord` objects on one folder; second `lock(timeout=0.1)` raises).
  - `test_switch_locked_returns_message`: `set(True)` while another holder has the lock returns `{"ok": False, "message": "Armoury Crate is being switched by another plugin, try again in a few minutes."}` and calls no `armoury_*` command.
  - `test_switch_on_writes_record_before_stop` (record on disk with `recorded` before `armoury_stop` is called) and `test_switch_off_restores_and_clears`.
  - `test_on_load_recovers_cut_switch` (record: replaced False, recorded non-empty) calls `armoury_restore` with the record and clears it. (Review Focus 1)
  - `test_on_load_skips_when_locked`: lock held elsewhere, `on_load()` calls nothing, logs, sets no warning. (Review Focus 2)
  - `test_migrate_once`: settings with `armoury_replaced=True` and no record: moved, returns True; second call returns False and does not overwrite a record whose `replaced` was changed to False. (Review Focus 3)
  - `tests/test_main.py`: `test_state_reads_shared_record` (`get_state()["armoury"]["replaced"]` follows the record file) and `test_reapply_on_record_flip` (record changes to replaced: next tick reapplies with reason "Armoury switched").
- [ ] **Step 2:** Run `pytest tests/test_armoury_record.py tests/test_armoury_switch.py -v`. Expected: FAIL (modules missing).
- [ ] **Step 3:** Implement by moving today's logic from `main.py` (`_armoury_timeout`, `_armoury_call`, `_merge_new_services`, `_restop_on_load`, `_recover_on_load`, `_armoury_on`, `_armoury_off`, `_clean_record`) into `ArmourySwitch`, reading and writing `ArmouryRecord` instead of `Settings`. Each public operation takes the asyncio lock, then `record.lock()`, then reloads the record before acting. `main.py` calls `migrate_from_settings` once in `_startup_sequence` before `on_load()`, then saves Overdrive's settings without the fields.
- [ ] **Step 4:** Run `pytest -q`. Expected: all pass. Existing Armoury tests in `test_main.py` are ported to `test_armoury_switch.py`, not deleted.
- [ ] **Step 5:** Commit `Armoury Crate record shared between plugins, with a cross-process lock`.

### Task 4 (Overdrive): remove the moved controls, 0.2.0

**Files:**
- Modify: `main.py` (drop `set_controller_mode`, `set_grip_vibration`, `set_touchscreen`, `controller_auto`, `_desktop_not_kept_on_load`, controller steps in unload), `py_modules/overdrive/controls.py` (drop controller and touchscreen parts), `py_modules/overdrive/reapply.py` (drop controller steps), `py_modules/overdrive/settings.py` (drop `controller_mode`, `grip_vibration` from defaults and save; reading old files still works), `helper/src/Protocol.cs` (drop `controller_info`, `set_controller_mode`, `set_vibration`, `get_touchscreen`, `set_touchscreen` and their read-list entries), `helper/src/SystemControls.cs` (touchscreen code stays in the file for sharing, unused by Overdrive), `src/index.tsx` (drop the three rows), `package.json` version `0.2.0`, `README.md` (points to Traction for controller, screen and touch)
- Delete: `py_modules/overdrive/controller_auto.py`, `tests/test_controller_auto.py` (Traction gets them in Task 6)
- Create: `shared-files.txt` (one repo-relative path per line: `helper/src/ProtocolKit.cs`, `helper/src/ArmouryCommands.cs`, `helper/src/AsusHid.cs`, `helper/src/ArmouryServices.cs`, `helper/src/ServiceHardware.cs`, `helper/src/SystemControls.cs`, `helper/src/Validation.cs`, `helper/src/AdlHardware.cs`, `helper/src/ghelper/AmdAdl2.cs`, `py_modules/overdrive/helper_client.py`, `py_modules/overdrive/device.py`, `py_modules/overdrive/reapply_core.py`, `py_modules/overdrive/settings_file.py`, `py_modules/overdrive/armoury_record.py`, `py_modules/overdrive/armoury_switch.py`)
- Test: `tests/test_main.py`, `helper/tests/HelperTests.cs`, `tests/test_shared_files.py`

- [ ] **Step 1: Write failing tests:** `test_moved_commands_refused` (helper: `set_controller_mode` returns ok=false "unknown command"); `test_state_has_no_controller_keys` (`get_state()` has no `controller`, `touchscreen`); `test_shared_files_exist_and_import_relatively` (every listed file exists; listed Python files contain no `from overdrive` / `import overdrive`; listed C# files declare `namespace Ally.Shared`).
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Remove the code. Panel order after: readout, Armoury toggle, CPU Boost, Auto SR, Performance, FPS limit, Auto TDP, Fan curve.
- [ ] **Step 4:** Run `pytest -q`, `powershell -File helper\build.ps1 -Tests`, `npm run build`, `npm run typecheck`, `node --test tests/frontend`. Expected: all pass.
- [ ] **Step 5:** Commit `Overdrive 0.2.0: controller, vibration and touchscreen move to Traction`.

### Task 5 (Traction): scaffold, shared copy, helper

**Files:**
- Create: `package.json` (name `traction`, version `0.1.0`, same scripts and devDependencies as Overdrive), `plugin.json` (`"name": "Traction"`, flags `["root"]`, publisher `musika08`), `rollup.config.js`, `tsconfig.json`, `pyrightconfig.json`, `LICENSE` (GPL-3.0), `THIRD-PARTY-NOTICES.md` (G-Helper), `.gitignore` (`node_modules`, `helper/out`, `__pycache__`, `dist`), `tools/sync-shared.py`, `tools/check-shared.py`, `shared/SOURCE`, `helper/build.ps1` (output `bin\traction-helper.exe`), `helper/src/TractionCore.cs`, `helper/src/HelperMain.cs`, `helper/tests/HelperTests.cs`, `helper/tests/FakeHardware.cs`, `tests/conftest.py`, `tests/fake_helper.py`, `tests/test_shared.py`
- Copied by sync: the files in Overdrive's `shared-files.txt`, Python ones to `py_modules/traction/<name>.py`, C# ones to `helper/src/shared/<path under helper/src>`

**Interfaces:**
- Produces: `tools/sync-shared.py --overdrive <repo> --commit <sha>` (reads `shared-files.txt` at that commit via `git show`, writes files, writes `shared/SOURCE` as JSON `{"overdrive_commit": sha, "files": {dest_path: sha256}}`); `tools/check-shared.py` (exit 1 when any listed file's sha256 differs from SOURCE; `--overdrive <repo>` also compares with `git show <commit>:<path>`).
- Helper commands (`TractionCore`, same JSON-lines protocol, version `0.1.0`): `hello`, `frame_sample`, `foreground`, `armoury_state`, `armoury_stop`, `armoury_restore` (via `ArmouryCommands`), `controller_info`, `set_controller_mode {mode}`, `set_vibration {strength}`, `get_touchscreen`, `set_touchscreen {enabled}`. Read list for non-Ally: `hello`, `frame_sample`, `foreground`, `armoury_state`, `controller_info`, `get_touchscreen`. Replies and refusals byte-identical to Overdrive 0.1.0's for the same command (port its tests).
- Consumes: Overdrive commit from Task 4.

- [ ] **Step 1:** Write `tests/test_shared.py::test_shared_copy_matches_source` (runs `check-shared.py`, expects exit 0) and `test_tampered_copy_fails` (temp copy with one byte changed, expects exit 1). Port Overdrive 0.1.0's helper tests for the moved commands into `helper/tests/HelperTests.cs`.
- [ ] **Step 2:** Run. Expected: FAIL.
- [ ] **Step 3:** Implement; run `python tools/sync-shared.py --overdrive ..\overdrive --commit <Task 4 sha>`.
- [ ] **Step 4:** Run `pytest -q` and `powershell -File helper\build.ps1 -Tests`. Expected: pass.
- [ ] **Step 5:** Commit `Traction scaffold: shared code from Overdrive <sha>, traction-helper`.

### Task 6 (Traction): backend

**Files:**
- Create: `main.py`, `py_modules/traction/settings.py`, `py_modules/traction/controls.py`, `py_modules/traction/reapply.py`, `py_modules/traction/sampling.py`, `py_modules/traction/controller_auto.py` (moved from Overdrive 0.1.0, imports from `.sampling`), `py_modules/traction/overdrive_compat.py`
- Test: `tests/test_settings.py`, `tests/test_controls.py`, `tests/test_controller_auto.py` (moved), `tests/test_main.py`, `tests/test_overdrive_compat.py`

**Interfaces:**
- `Settings` fields: `controller_mode` ("gamepad" | "desktop" | "auto", default "gamepad"), `grip_vibration` (default True), `user_set` (default False). File `homebrew\settings\traction\settings.json` via `settings_file`.
- `migrate_from_overdrive(overdrive_settings_path: str, s: Settings) -> bool`: only when Traction's file does not exist; takes valid `controller_mode` / `grip_vibration`, sets `user_set` True when either was taken; any read error or bad value leaves defaults and returns False.
- `overdrive_compat.old_overdrive(plugins_dir: str) -> bool`: True when `plugins_dir\overdrive\package.json` exists and its `version` is below `0.2.0` (unparsable version counts as old).
- Plugin methods (`callable` from the panel): `get_state() -> dict` with `support`, `armoury {installed, replaced, warning}`, `controller {mode, found}`, `vibration`, `touchscreen {state}`, `blocked` (the update-Overdrive note or ""), `note` (the Armoury-running note when not replaced, else ""), `busy`; `set_armoury_replaced(on)`, `set_controller_mode(mode)`, `set_grip_vibration(on)`, `set_touchscreen(enabled)`.
- Startup: load settings, migrate, Desktop to Gamepad (`_desktop_not_kept_on_load`, same log line wording with "Traction"), `ArmourySwitch(..., owner="Traction").on_load()`, reapplier start (controller steps only, from Overdrive 0.1.0's reapply), Auto loop when stored "auto" with `ControllerAuto(client, reapplier, autotdp=None)`.
- When `old_overdrive()` is true: no controller writes at all (reapplier not started, Auto not started, set_* return `{"ok": False, "message": "Update Overdrive to 0.2.0 or newer to use Traction's controller settings."}`, unload writes nothing); Armoury toggle and Touch Screen still work.

- [ ] **Step 1: Write failing tests:**
  - `test_migrate_takes_overdrive_values` (`{"controller_mode": "auto", "grip_vibration": False}` taken), `test_migrate_desktop_becomes_gamepad_at_load`, `test_migrate_missing_unreadable_or_bad_values_keep_defaults` (missing file, `"{"` file, `{"controller_mode": 5}`) (Review Focus 4), `test_migrate_not_run_when_traction_file_exists`.
  - `test_old_overdrive_versions` (`0.1.0` True, `0.2.0` False, `0.10.0` False, `"x"` True, no file False).
  - `test_old_overdrive_blocks_controller_writes` (fake helper sees no `set_controller_mode` / `set_vibration` across load, set calls and unload; `get_state()["blocked"]` is the note) (Review Focus 5).
  - `test_note_while_armoury_runs` (record not replaced: `note == "Armoury Crate is running, so these settings may be overwritten."`; replaced: "").
  - Ported from Overdrive 0.1.0: controller mode, vibration, touchscreen, Auto and unload-to-Gamepad tests.
- [ ] **Step 2:** Run `pytest -q`. Expected: the new ones FAIL.
- [ ] **Step 3:** Implement, porting the Overdrive 0.1.0 code paths unchanged in behaviour.
- [ ] **Step 4:** Run `pytest -q`. Expected: pass.
- [ ] **Step 5:** Commit `Traction backend: controller mode, vibration, touchscreen, shared Armoury switch`.

### Task 7 (Traction): panel

**Files:**
- Create: `src/index.tsx`, `tests/frontend/*.test.mjs` as needed
- Test: `npm run build`, `npm run typecheck`, `node --test tests/frontend`

- [ ] **Step 1:** Rows in order: Armoury toggle (label `Use Traction instead of Armoury Crate`, same busy/result handling as Overdrive's, which survives the panel closing), Controller mode (Gamepad / Desktop / Auto), Grip vibration, Touch Screen. Port the row components from Overdrive 0.1.0 `src/index.tsx`. Above the controller rows show `blocked` when set (rows disabled), else `note` when set. Plugin title `Traction`, icon a gamepad from `react-icons`.
- [ ] **Step 2:** Run the three commands. Expected: pass.
- [ ] **Step 3:** Commit `Traction panel`.

### Task 8 (Traction): packaging and docs

**Files:**
- Create: `tools/install-dev.ps1` (copy of Overdrive's, target `C:\ProgramData\decky\plugins\traction`, stops only `traction-helper` processes inside that folder), `tools/make-zip.py` (zip with top folder `traction/`: `plugin.json`, `main.py`, `package.json`, `README.md`, `LICENSE`, `THIRD-PARTY-NOTICES.md`, `dist/`, `py_modules/`, `bin/`; skips `__pycache__`; prints sha256), `README.md` (what Traction does, works with or without Overdrive, needs Overdrive 0.2.0+ if Overdrive is installed)
- Test: `tests/test_make_zip.py` (zip has exactly those top entries under `traction/`, no `__pycache__`)

- [ ] **Step 1:** Write the zip test; run, expect FAIL; implement; run, expect PASS.
- [ ] **Step 2:** Commit `Traction packaging and README`.

### Task 9 (controller, not a subagent): live test on the Ally

- [ ] Install Overdrive 0.2.0 then Traction 0.1.0 with their `install-dev.ps1` (elevated).
- [ ] Check: Traction shows the controller mode and vibration Overdrive had; Overdrive's panel no longer has the three rows; `C:\ProgramData\decky\armoury\state.json` holds the record and Overdrive's settings no longer do.
- [ ] Switch Armoury from Overdrive, open Traction: same state; switch back from Traction, open Overdrive: same state.
- [ ] With a game running: Auto goes Gamepad in game, Desktop on the desktop app, with Overdrive's Auto TDP on at the same time; both logs free of frame-sample errors (spec 3.1 interference check).
- [ ] Sleep and wake: controller mode and vibration reapplied by Traction; power mode by Overdrive.
- [ ] Decky restart with Desktop chosen: comes back Gamepad.
- [ ] Then: store list commit (local) adding Traction 0.1.0 and Overdrive 0.2.0; publishing waits for the owner's OK.
