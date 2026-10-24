# Traction: button mapping design

Date: 2026-10-24. Status: draft for owner review. Extends `2026-10-24-traction-design.md` section 4 (Button remap),
which planned the back buttons only; the owner widened it after the live probe
(`docs/notes/2026-10-24-remap-probe.md`).

## 1. What the owner gets

- **Back buttons.** For each back button (left, right) one choice:
  - **Shift (combos)**: the factory behaviour. Holding it makes other buttons do their combo action.
  - **A button or key from the list** (section 3): it then acts as that, and stops being a Shift key.
  Example: right back button = B, left back button stays Shift.
- **Combos.** For each of the 17 other buttons (A, B, X, Y, LB, RB, LT, RT, left stick click, right stick click,
  D-pad up/down/left/right, View, Menu) one choice of what "Shift + that button" does: **Unchanged** (leave the
  controller as it is), **Nothing**, or an action from the list.
  Example: Shift + A = Screenshot.
- **Reset buttons**: puts every button and combo back to the factory set.
- Combos only fire while at least one back button is set to Shift; the panel says so when both are remapped.

## 2. Panel

A new section **Buttons** under the existing rows:
- "Left back button" and "Right back button": dropdowns (Shift (combos), then the list).
- "Combos": a row that opens a full-screen list (Decky modal) with one dropdown per button, so the main panel stays
  short. The row shows how many combos are changed, e.g. "3 changed".
- "Reset buttons".
- The same Armoury Crate note and old-Overdrive block as the other controller rows.

## 3. The action list

Only actions that work with nothing else running (no G-Helper or Armoury Crate hotkey listener):
- Controller: A, B, X, Y, LB, RB, LT, RT, L-stick click, R-stick click, D-pad up/down/left/right, View, Menu.
- Mouse: left click, right click, middle click, scroll up, scroll down.
- Keys: Esc, Enter, Tab, Backspace, Space, arrow keys, Page Up, Page Down, Windows key.
- Windows: Show desktop (Win+D), Screenshot (Win+Shift+S), Task Manager (Ctrl+Shift+Esc), Close window (Alt+F4),
  Alt+Tab, Win+Tab, Volume up, Volume down, Show touch keyboard.
- Nothing.
Codes come from G-Helper `AllyControl.cs` (constants and `BindingGroups`) at c09017453d9ed0c52261b67a48d02bd55232e6ac;
keys are PS/2 set-2 codes (`02-xx`), shortcuts `04-<n>-<keys>`, media `05-xx`, mouse `03-xx`, controller `01-xx`.
Left out on purpose: G-Helper's own hotkeys (brightness, controller mode, FPS limit, touchscreen toggle, overlay):
they are Ctrl+Shift+Alt+F-key shortcuts that need a program listening for them. They can come later if Traction
listens for them itself.

## 4. How it reaches the controller

- The binding report proved on the device: `5A D1 02 <zone> 2C` + four 11-byte slots (button, button's combo,
  partner button, partner's combo), one 64-byte feature report per zone; zone pairs as G-Helper (1 D-pad up/down,
  2 D-pad left/right, 3 stick clicks, 4 bumpers, 5 A/B, 6 X/Y, 7 View/Menu, 8 back buttons (left = M2, right = M1),
  9 triggers). No admin rights needed; a single zone write takes effect at once (probe, 2026-10-24).
- The controller cannot be read back, so Traction only writes a zone after the owner changes something in it, and
  then writes all four slots of that zone: changed slots from the owner's choices, untouched slots from G-Helper's
  defaults. "Unchanged" combos in a written zone therefore become G-Helper's default for that combo (stated in the
  panel's help text). Reset writes all 9 zones with G-Helper's defaults.
- Back button as Shift = the factory codes `02-8E` (left) / `02-8F` (right) in its slot and its combo slot.
- New helper command `set_bindings {zone, slots[4]}`; slot values validated against the action list (section 3)
  only, so the helper can never send an arbitrary report.
- Safety: after every write the helper checks that Windows still sees the gamepad (XInput pad present). If it is gone
  for 5 s, the helper writes G-Helper's full default set (start-up sequence, Gamepad mode, all 9 zones) and the
  panel shows "The controller stopped responding, so buttons were reset to the factory set."

## 5. When it is applied

- Stored in Traction's settings as `bindings: {slot: code}` (only changed slots).
- Reapplied with the controller mode after sleep, after Decky starts, and after an Armoury Crate switch, through the
  existing reapplier (the controller forgets its settings on sleep).
- Gamepad mode only. Switching to Desktop mode writes G-Helper's desktop defaults for the zones Traction changed
  (desktop uses keyboard and mouse codes there); switching back to Gamepad writes the owner's set again.
  Auto mode follows the same rule on each switch.
- Not written while an old Overdrive is installed (same block as the other controller rows).

## 6. Tests

- Unit: action list codes, zone/slot packing (bytes match the probe's proven reports), validation refusals, only
  changed zones written, reset writes 9 zones, desktop/gamepad switching, reapply after resume, the pad-gone reset.
- Live on the Ally, Steam Big Picture in front: right back = B; Shift + A = Screenshot; reset; sleep and wake keeps
  the mapping; Desktop and back keeps it.

## 7. Out of scope

Per-game profiles, turbo (auto-fire), G-Helper hotkey actions (section 3), remapping the Xbox, View-long-press, ROG
and Command Center buttons (not firmware-bindable).
