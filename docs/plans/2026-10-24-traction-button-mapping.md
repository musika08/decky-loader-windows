# Traction Button Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the owner turn each back button into a normal button or keep it as the Shift (combo) key, choose what each "Shift + button" combo does, and reset everything, from Traction's panel.

**Architecture:** A pure Python model (`bindings.py`) turns the owner's choices into the four 11-byte slots of each binding zone, using G-Helper's defaults for untouched slots. The helper gets one new command that writes a zone (codes validated against an allow-list) and checks that Windows still sees the gamepad, resetting to the factory set if not. The backend stores the choices, writes changed zones after the controller mode (desktop defaults in Desktop mode), and the reapplier puts them back after sleep.

**Tech Stack:** Python 3 (Decky backend, pytest), C# 5 / .NET Framework 4.8 (helper, in-box csc), TypeScript/React with `@decky/ui` (rollup).

**Spec:** `X:\Projects\Decky Loader\plugins\traction\docs\specs\2026-10-24-traction-button-mapping-design.md`. Probe evidence: `docs/notes/2026-10-24-remap-probe.md`.

## Global Constraints

- Repo `X:\Projects\Decky Loader\plugins\traction`, branch main, public (musika08/traction). Commit as `musika08 <225994527+musika08@users.noreply.github.com>`; check `git config user.email` before each commit. **No "Co-Authored-By" trailer or any Claude attribution in commits** (owner rule). Never push; the controller pushes.
- Binding report, proven on the device: `5A D1 02 <zone> 2C` + four 11-byte slots at offsets 5, 16, 27, 38 (button, button's combo, partner, partner's combo), zero padded to the 64-byte feature report. Slot encoding (G-Helper `DecodeBinding`): `01 xx` -> byte1 = xx; `02 xx` -> byte2 = xx; `03 xx` -> byte4 = xx; `04 n k1..kn` -> byte0 = 04 and bytes 5.. = n, k1..kn (G-Helper copies everything after the first byte to offset 5; e.g. `04-02-82-23` -> `04 00 00 00 00 02 82 23 00 00 00`); `05 xx` -> byte3 = xx; `00-00` = all zero.
- Zones: 1 D-pad up/down, 2 D-pad left/right, 3 L/R stick click, 4 LB/RB, 5 A/B, 6 X/Y, 7 View/Menu, 8 back buttons (left = M2 first, right = M1 second), 9 LT/RT.
- Back button as Shift = `02-8E` (left) / `02-8F` (right) in both its button slot and its combo slot.
- G-Helper gamepad defaults (button / combo): DU 01-09 / 05-19, DD 01-0A / 04-02-82-23, DL 01-0B / 04-04-8C-88-8A-05, DR 01-0C / 04-04-8C-88-8A-06, LS 01-07 / none, RS 01-08 / 04-04-8C-88-8A-0C, LB 01-05 / none, RB 01-06 / none, A 01-01 / none, B 01-02 / none, X 01-03 / 04-03-82-88-1B, Y 01-04 / 04-04-8C-88-8A-44, View 01-11 / none, Menu 01-12 / none, LT 01-0D / none, RT 01-0E / none, M2 02-8E / 02-8E, M1 02-8F / 02-8F. ("none" = all-zero slot.)
- G-Helper desktop (Mouse mode) defaults for the button slot (combo slots as gamepad): DU 02-98, DD 02-99, DL 02-9A, DR 02-9B, LS 02-88, RS 03-01, LB 02-0D, RB 03-01, A 02-5A, B 02-76, X 02-97, Y 02-96, LT 04-02-88-0D, RT 03-02; View, Menu, M1, M2 as gamepad.
- Copy fixed by the spec: "The controller stopped responding, so buttons were reset to the factory set."

## Action list (ids, labels, codes)

`nothing` Nothing 00-00 · `a` A 01-01 · `b` B 01-02 · `x` X 01-03 · `y` Y 01-04 · `lb` LB 01-05 · `rb` RB 01-06 · `ls` Left stick click 01-07 · `rs` Right stick click 01-08 · `du` D-pad up 01-09 · `dd` D-pad down 01-0A · `dl` D-pad left 01-0B · `dr` D-pad right 01-0C · `lt` LT 01-0D · `rt` RT 01-0E · `view` View 01-11 · `menu` Menu 01-12 · `mouse_left` Left click 03-01 · `mouse_right` Right click 03-02 · `mouse_middle` Middle click 03-03 · `scroll_up` Scroll up 03-04 · `scroll_down` Scroll down 03-05 · `esc` Esc 02-76 · `enter` Enter 02-5A · `tab` Tab 02-0D · `backspace` Backspace 02-66 · `space` Space 02-29 · `up` Up arrow 02-98 · `down` Down arrow 02-99 · `left` Left arrow 02-9A · `right` Right arrow 02-9B · `page_up` Page Up 02-96 · `page_down` Page Down 02-97 · `win` Windows key 02-82 · `show_desktop` Show desktop 04-02-82-23 · `screenshot` Screenshot 04-03-82-88-1B · `task_manager` Task Manager 04-03-8C-88-76 · `close_window` Close window 04-02-8A-0C · `alt_tab` Alt+Tab 04-02-8A-0D · `win_tab` Win+Tab 04-02-82-0D · `volume_up` Volume up 05-03 · `volume_down` Volume down 05-02 · `touch_keyboard` Show touch keyboard 05-19.

Buttons (ids, labels, zone, side 0 = first / 1 = second): `du` D-pad up 1/0, `dd` D-pad down 1/1, `dl` D-pad left 2/0, `dr` D-pad right 2/1, `ls` Left stick click 3/0, `rs` Right stick click 3/1, `lb` LB 4/0, `rb` RB 4/1, `a` A 5/0, `b` B 5/1, `x` X 6/0, `y` Y 6/1, `view` View 7/0, `menu` Menu 7/1, `back_left` Left back button 8/0, `back_right` Right back button 8/1, `lt` LT 9/0, `rt` RT 9/1.

## Rulings made while planning

- Ruling: the combo list is a collapsible section inside the panel using the panel's existing inline picker (`src/picker.ts`), not a Decky modal: the panel already avoids Decky's DropdownItem because it opens a separate popup window (comment in `src/index.tsx`), and a modal would be the same kind of extra window. Cost if wrong: move the list into a modal later.
- Ruling: the action/defaults tables live in Python (`bindings.py`); the helper only holds the allow-list of codes and the gamepad defaults it needs for the emergency reset. A test checks the C# allow-list equals the Python action codes. Cost if wrong: one more table to keep in step.
- Ruling: settings key `bindings = {"back_left": <action id | "shift">, "back_right": <...>, "combos": {<button id>: <action id>}}`; missing keys mean factory. A back button set to an action also gets that action in its own combo slot (as G-Helper's defaults duplicate M1/M2).

## Review Focus

1. A stored action id that is no longer in the list (settings edited by hand or an older version): load drops it (factory for that slot), never fails. Test in Task 1.
2. Both back buttons remapped: combos can't be reached; the panel shows "Combos need a back button set to Shift." and the stored combos stay. Test in Task 4 (frontend helper).
3. Switching Gamepad -> Desktop -> Gamepad with a changed zone: Desktop gets G-Helper's desktop defaults for that zone, Gamepad gets the owner's set back. Test in Task 3.
4. The pad disappears after a write: helper resets, backend clears the stored bindings and shows the reset message; the next reapply writes nothing custom. Test in Tasks 2 and 3.
5. An old Overdrive installed: no set_bindings call at all (load, reapply, set, reset). Test in Task 3.

---

### Task 1: binding model (`bindings.py`)

**Files:** Create `py_modules/traction/bindings.py`, `tests/test_bindings.py`.

**Interfaces (produces):**
- `ACTIONS: list[tuple[str, str, str]]` (id, label, code) in the order above; `BUTTONS: list[tuple[str, str, int, int]]` (id, label, zone, side).
- `clean(raw) -> dict` -> `{"back_left": str, "back_right": str, "combos": {str: str}}` keeping only known ids (back values: "shift" or an action id; combo keys: button ids except back_left/back_right; combo values: action ids).
- `zone_slots(zone: int, bindings: dict, desktop: bool) -> list[str]` four codes (first, first combo, second, second combo).
- `changed_zones(bindings: dict) -> list[int]` sorted zones with any owner choice.
- `all_zones() -> list[int]` = [1..9].

- [ ] **Step 1: tests** (exact values from Global Constraints):
  - `test_factory_zone5 == ["01-01","00-00","01-02","00-00"]` with `{}` bindings, gamepad.
  - `test_back_right_b`: `{"back_right":"b"}` zone 8 -> `["02-8E","02-8E","01-02","01-02"]`; `changed_zones == [8]`.
  - `test_combo_a_screenshot`: `{"combos":{"a":"screenshot"}}` zone 5 -> `["01-01","04-03-82-88-1B","01-02","00-00"]`.
  - `test_desktop_defaults`: `{"combos":{"a":"screenshot"}}`, zone 5, desktop -> `["02-5A","00-00","02-76","00-00"]` (owner set not used in Desktop).
  - `test_clean_drops_unknown`: `{"back_left":"rocket","combos":{"a":"rocket","zz":"b"},"x":1}` -> `{"back_left":"shift","back_right":"shift","combos":{}}`. (Review Focus 1)
  - `test_actions_unique_and_codes_valid`: ids unique; every code matches `^(00-00|0[1235]-[0-9A-F]{2}|04-0[1-5](-[0-9A-F]{2})+)$`.
- [ ] **Step 2:** `X:\Projects\Decky Loader\decky-loader\backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=<NEW folder under %TEMP%> tests/test_bindings.py` -> FAIL.
- [ ] **Step 3:** implement (stdlib only).
- [ ] **Step 4:** run -> PASS; full suite passes.
- [ ] **Step 5:** commit `Button mapping: binding model`.

### Task 2: helper `set_bindings` with the gamepad check

**Files:** Modify `helper/src/TractionCore.cs`; create `helper/src/Bindings.cs`; modify `helper/src/HelperMain.cs` (real pad check); modify `helper/tests/HelperTests.cs`, `helper/tests/FakeHardware.cs`; create `tests/test_action_codes_match.py`.

**Interfaces (produces):**
- Command `set_bindings {"zone": int 1..9, "slots": [4 strings]}`: refuses unless every slot is in `Bindings.Allowed` (the action codes plus `02-8E`, `02-8F` and every default code from Global Constraints); writes the zone report; then polls `IPadCheck.Present()` every 500 ms up to 5 s; reply `{ok:true, data:{zone}}` when present; otherwise writes `Bindings.FactoryReset` (InitInput `5A`+"ASUS Tech.Inc.", `5A D1 0B 01 01`, `5A D1 01 01 01`, all 9 gamepad default zones, `5A D1 0F 20`, `5A D1 04 04 00 64 00 64`, `5A D1 05 04 00 64 00 64`, `5A D1 06 02 64 64`, plus `5A D1 1C 02 64 64` on Xbox) and replies `{ok:false, message:"The controller stopped responding, so buttons were reset to the factory set.", data:{reset:true}}`.
- `public interface IPadCheck { bool Present(); }` real one = `XInputGetState(0)` == 0 from `xinput1_4.dll`; on a non-Ally read list `set_bindings` is refused like other writes. Not in the read list.
- `Bindings.Encode(string code) -> byte[11]` per Global Constraints.

- [ ] **Step 1: tests** (C#, fakes only): `Encode` for `01-02`, `02-5A`, `03-01`, `04-03-82-88-1B`, `05-19`, `00-00` (byte arrays as in the probe dump); `set_bindings` zone 8 slots `["02-8E","02-8E","01-02","01-02"]` sends exactly `5A D1 02 08 2C 02 00 8E 00.. 02 00 8E 00.. 01 02 00.. 01 02 00..` (offsets 5/16/27/38) padded; unknown code `01-FF` refused with no write; zone 0 and 10 refused; pad absent -> factory sequence written in order and the reset reply (Review Focus 4); pad present after 1 poll -> ok.
- [ ] **Step 2:** `tests/test_action_codes_match.py`: parses `Bindings.cs` allow-list and asserts it equals the set of `bindings.ACTIONS` codes plus the default codes.
- [ ] **Step 3:** run `powershell -NoProfile -File helper\build.ps1 -Tests` and the pytest -> FAIL.
- [ ] **Step 4:** implement. **Step 5:** both pass. **Step 6:** commit `Helper: set_bindings with a gamepad check and factory reset`.

### Task 3: backend

**Files:** Modify `py_modules/traction/settings.py` (field `bindings`, cleaned with `bindings.clean`), `py_modules/traction/controls.py` (`apply_bindings`), `py_modules/traction/reapply.py` (no change if apply_controller covers it), `main.py`; tests `tests/test_settings.py`, `tests/test_controls.py`, `tests/test_main.py`, `tests/fake_helper.py`.

**Interfaces (produces):**
- `async apply_bindings(client, settings, mode: str) -> dict`: for each zone in `changed_zones` writes `set_bindings` with `zone_slots(zone, settings.bindings, desktop=(mode == "desktop"))`; on a reply with `data.reset` clears `settings.bindings`, returns `{ok:False, message, reset:True}`.
- `apply_controller` calls `apply_bindings` right after the mode write (same mode), so every reapply, Auto switch and mode change carries it.
- Plugin methods: `set_back_button(side: "left"|"right", value: str)`, `set_combo(button: str, action: str)` (`"unchanged"` removes the key), `reset_buttons()` (writes all 9 zones with factory slots for the current mode, then clears `bindings`). All refuse under an old Overdrive, validate against `bindings.py`, save, and write only the affected zone. `get_state()["buttons"] = {"back_left", "back_right", "combos": {...}, "changed": int}`.

- [ ] **Step 1: tests:** settings round-trip and junk dropped; `apply_bindings` writes only changed zones, desktop slots in Desktop; Gamepad -> Desktop -> Gamepad sequence of set_bindings payloads (Review Focus 3); reset reply clears settings and surfaces the message (Review Focus 4); old Overdrive: zero `set_bindings` calls across load, reapply tick, set_*, reset (Review Focus 5); `set_combo("a","unchanged")` removes it and rewrites zone 5 with factory slots.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** full suite PASS. **Step 5:** commit `Backend: button mapping settings, writes and reset`.

### Task 4: panel

**Files:** Modify `src/index.tsx`, `src/controls.ts`; tests `tests/frontend/controls.test.mjs`.

- [ ] **Step 1:** pure helpers in `controls.ts` with tests: `backButtonOptions()` (["Shift (combos)", ...action labels]); `combosNote(state)` returns "Combos need a back button set to Shift." when neither back button is "shift", else "" (Review Focus 2); `changedLabel(n)` -> "3 changed" / "None changed".
- [ ] **Step 2:** section "Buttons" after Touch Screen: "Left back button", "Right back button" (inline picker), "Combos" row showing `changedLabel` that expands an inline list of 17 pickers (options "Unchanged", then actions), "Reset buttons" button; disabled with the same `blocked` rule as controller rows; result messages like the other rows.
- [ ] **Step 3:** `npm run build`, `npm run typecheck`, `node --test tests/frontend/*.test.mjs` pass. **Step 4:** commit `Panel: Buttons section`.

### Task 5 (controller with the owner): live test on the Ally, Steam Big Picture in front

- [ ] Install with `tools\install-dev.ps1` (elevated). Right back = B: goes back from a game page; Shift + A = Screenshot opens the snip bar; Reset buttons; sleep and wake keeps the mapping; Desktop and back keeps it; XInput pad present after each write.
- [ ] Then version 0.2.0, release and store entry, with the owner's OK.
