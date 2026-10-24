# Traction: design

Date: 2026-10-24. Status: design agreed in conversation; awaiting owner review of this written spec.

Traction is a new Decky Loader plugin for the ROG Ally family on Windows. It takes over the controller, screen and touch
controls from Overdrive and adds new ones. Overdrive keeps power, heat, fans and FPS.

## 1. Owner decisions (2026-10-24)

- Name: **Traction** (pairs with Overdrive). Folder and package name `traction`.
- Moved from Overdrive: Controller mode (Gamepad / Desktop / Auto), Grip vibration, Touch Screen.
- New in Traction: Button remap, Stick deadzones, Stick LED lights, Screen refresh rate, and Xbox controller off
  (recommended by Claude; the owner did not object; easy to move back to Overdrive if wanted).
- Separate helpers: Traction ships its own helper program. Overdrive and Traction share the helper's source code, not a
  running process. Neither plugin needs the other installed.
- The Armoury Crate toggle is in both plugins and both show the same real state (one shared record, one lock). Turning
  it off in one shows Off in the other the next time its panel opens.
- Built in two parts: Part 1 moves the three existing controls (Overdrive and Traction released together, so no control
  is ever missing); Part 2 adds the new controls one at a time, each probed on the Ally first.
- Publishing Traction (public repo, store list, dashboard) needs the owner's OK when it is ready. Public repo commits use
  `225994527+musika08@users.noreply.github.com`.

## 2. Architecture

```
Decky Loader (elevated)
 ├─ Overdrive  main.py ── asus-helper.exe      power, fans, FPS limit, Auto TDP, CPU Boost, Battery, Auto SR
 └─ Traction   main.py ── traction-helper.exe  controller HID, touchscreen, display refresh rate
        both ── C:\ProgramData\decky\armoury\state.json + mutex Global\DeckyArmourySwitch
```

- Traction layout mirrors Overdrive: `main.py`, `py_modules/traction/`, `src/index.tsx` (`@decky/ui`), `helper/src`
  (C# 5, .NET Framework 4.8, built with the in-box csc), `bin/traction-helper.exe`, plugin flags `["root"]`, GPL-3.0.
- The helper speaks the same JSON-lines protocol as asus-helper (one request, one reply, per line on stdin/stdout). It
  is a child of the plugin, so Decky's kill-on-close job ends it with the plugin.
- After Part 1, Overdrive never opens the controller HID device and Traction never touches power, fans or ADL power
  settings. The only thing both touch is the Armoury Crate switch (section 5).

### 2.1 Shared code

- The shared source lives in the Overdrive repo under `shared/` (C#: `AsusHid.cs`, `ArmouryServices.cs`, the
  Armoury-switch code, `Protocol.cs`, `Validation.cs`, the frame-rate sampler from `AdlHardware.cs`; Python: the
  helper client, the reapplier, settings helpers).
- Traction keeps a copy under its own `shared/` (so its public source is complete) plus `shared/SOURCE` naming the
  Overdrive commit it was copied from. `tools/sync-shared.py` copies the files from a given Overdrive commit;
  `tools/check-shared.py` (run by Traction's tests) fails when the copy differs from that commit.
- A fix to shared code lands in Overdrive first, then reaches Traction with a sync and a Traction release.

## 3. Part 1: the move

### 3.1 Traction gets

- Controller mode Gamepad / Desktop / Auto, with today's rules unchanged: Auto picks Gamepad for Steam, no window, a
  process drawing frames, or an unknown frame rate; Desktop only for another app with a valid 0 fps; 300 ms decisions,
  3 in a row to switch; Desktop resets to Gamepad whenever Traction loads.
- Grip vibration on/off.
- Touch Screen on/off (PnP "HID-compliant touch screen", read back after each change).
- The Armoury Crate toggle (section 5).
- Its own frame-rate reading for Auto (ADL FrameMetrics, as Overdrive does). Part 1's live test must show both plugins
  reading frame rate at the same time without either getting errors or wrong values. If they interfere, the ruling is:
  Traction asks Overdrive's backend for its last sample when Overdrive is installed and running, and samples itself
  otherwise.
- Reapply after sleep/resume and after an Armoury switch, through its own copy of the reapplier.
- Applies to the device only while Armoury Crate is off (the record says replaced), like Overdrive. While Armoury
  Crate runs, the panel shows: "Armoury Crate is running, so these settings may be overwritten."

### 3.2 Overdrive loses

- The Controller mode, Grip vibration and Touch Screen rows, `controller_auto.py`, the controller and touchscreen parts
  of `controls.py`, the HID and touchscreen commands in the helper.
- Panel order after the move: readout, Armoury toggle, CPU Boost, Auto SR, Performance, FPS limit, Auto TDP, Fan curve.
- Overdrive version 0.2.0.

### 3.3 Settings carry over

- On its first load (no `homebrew\settings\traction\settings.json`), Traction reads `controller_mode` and
  `grip_vibration` from `homebrew\settings\overdrive\settings.json` and saves them as its own. Touch Screen is not stored
  (it is read from Windows), so nothing to copy.
- A `desktop` value read this way still becomes Gamepad at load (the reset rule).
- Overdrive 0.2.0 ignores those keys and leaves them in its file (harmless; no rewrite needed).

### 3.4 Order of release

- Overdrive 0.2.0 and Traction 0.1.0 go to the store list in the same commit.
- If someone has Traction but an Overdrive older than 0.2.0, both would drive the controller. Traction detects an old
  Overdrive (its `package.json` version below 0.2.0 in the plugins folder) and then shows only a note: "Update Overdrive
  to 0.2.0 or newer to use Traction's controller settings." It writes nothing to the controller until then.

## 4. Part 2: new controls

Each one starts with a probe on the Ally (the device must answer as expected) before it is built. Values below come
from G-Helper `app/AllyControl.cs` at the pinned commit c09017453d9ed0c52261b67a48d02bd55232e6ac unless noted.

| Control | What the owner sees | Device side |
|---|---|---|
| Button remap | The two back buttons (M1, M2), each with a dropdown of targets (keyboard keys, gamepad buttons, mouse buttons, media keys) from G-Helper's `BindingGroups`; "Default" restores them | `BindZone(M1M2)` binding report, written with the whole controller state |
| Stick deadzones | Left stick, right stick: inner and outer sliders 0-100 %; triggers the same | `[5A D1 04 04 lsMin lsMax rsMin rsMax]`, `[5A D1 05 04 ltMin ltMax rtMin rtMax]` |
| Stick LED lights | Off / Low / Medium / High brightness and a colour choice | G-Helper's Aura code for the Ally (to be vendored from the pinned commit during planning) |
| Screen refresh rate | Dropdown of the rates Windows lists for the built-in screen (60 and 120 Hz on the Ally X) | Windows display API (`EnumDisplaySettings` / `ChangeDisplaySettingsEx`), no ASUS code; G-Helper `ScreenControl.cs` for reference |
| Xbox controller off | Toggle, for playing with an external controller only | `[5A D1 0B 01 02]` off, `[5A D1 0B 01 01]` on |

- Remap scope is the back buttons only, because those are the ones normally remapped. Other buttons can follow later if
  wanted.
- Controller writes follow G-Helper's order: mode, bindings, deadzones, vibration, all in one apply, so one control can
  never undo another.
- Xbox controller off is a safety risk: with no controller, Steam Big Picture can only be reached by touch. So it turns
  back on whenever Traction loads and after sleep. The toggle is single-press (owner preference: no two-press
  confirms); its description reads "The built-in controller stops working until you turn this off again, sleep the
  Ally or restart Decky."

## 5. Armoury Crate switch shared by both plugins

- One record: `C:\ProgramData\decky\armoury\state.json` with `replaced`, `recorded` (each service's start type before the
  switch) and `running` (which services were running). The same fields Overdrive keeps today in its own settings.
- One lock: a named mutex `Global\DeckyArmourySwitch`, held by the helper for a whole switch (on or off). A plugin that
  cannot take it within 2 seconds answers "Armoury Crate is being switched by another plugin, try again in a few
  minutes."
- The record is read on every panel open and every status tick, so the toggle in each plugin shows the real state.
- Migration: Overdrive 0.2.0 moves its `armoury_*` fields into the shared record on first load when the record does not
  exist yet, then stops writing them to its own file. Its existing recovery rules (a switch interrupted halfway is
  finished or undone at load) work on the shared record.
- When the record changes from replaced to not replaced (the other plugin turned Armoury Crate back on), each plugin
  stops applying its settings until it is replaced again.
- The record's folder is created by the elevated helper under `C:\ProgramData\decky`, which only admins can write. The
  record is validated on read (only known service names and start types), as Overdrive does today.

## 6. Errors and edge cases

- No controller found: the write is not retried for 5 seconds (today's rule) and the panel shows "Controller not found".
- Helper missing or crashed: the plugin restarts it once; if that fails, the panel shows the error and every control is
  disabled.
- Traction installed alone: everything in it works, including the Armoury toggle.
- Overdrive uninstalled while Traction stays: the shared record stays, so Traction's toggle still shows the right state
  and can restore Armoury Crate.
- Both uninstalled with Armoury Crate off: same as today with Overdrive (the services stay off). The dashboard's existing
  repair covers it.

## 7. Testing

- Unit tests for every Python module (pytest) and the helper's command handling (C# test runner), with fakes for HID,
  PnP, display and services; no test touches the real device.
- Shared-code check (`tools/check-shared.py`) in Traction's test run.
- Live tests on the Ally after each part: settings carried over, both toggles match after a switch from each side,
  Auto mode in and out of a game with both plugins running, sleep/resume reapply, and each Part 2 control read back
  from the device.

## 8. Out of scope

- Remapping buttons other than M1/M2, turbo buttons, the secondary binding layer.
- Per-game profiles.
- A shared running helper (rejected: one plugin's update or crash would cut off the other; small memory saving).
