# Button remap probe on the ROG Xbox Ally X (2026-10-24)

First live probe of G-Helper's binding report (AllyControl.BindZone at c09017453d9ed0c52261b67a48d02bd55232e6ac):
`[5A D1 02 <zone> 2C]` + four 10-byte bindings (left, left layer 2, right, right layer 2), sent as one 64-byte
feature report to the controller collection (PID 0x1B4C). No admin rights are needed for the write.

What happened, in order:
1. Zone 8 (M1/M2), M1 -> gamepad B (`01-02`), layer 2 kept at the defaults. Write ok. Owner: pressing the right back
   button did nothing in Steam.
2. Mode write `[5A D1 01 01 01]`, then zone 8 with both back buttons -> B. Write ok. Nothing happened.
3. Mode write, then zone 5 (A/B) with A -> B. Write ok. Owner: A on a game tile did nothing (read at the time as
   "A now acts as B"; see 5).
4. Zone 5 restored, zone 8 -> Enter (`02-5A`). Owner: no button, stick or D-pad worked any more.
5. XInput showed no controller at all (XInputGetState 1167 on all four pads), although the PnP devices
   "ROG Xbox Ally Device Gamepad" and "HID-compliant game controller" were present and OK. Rewriting every zone
   with G-Helper's defaults, `[5A D1 0B 01 01]` (controller on) and G-Helper's InitInput
   (`5A` + "ASUS Tech.Inc.") did not bring it back. The power button did not sleep the device either.
6. A Windows restart brought the gamepad back (XInput pad 0 sending packets).

Open questions before any further write:
- Which step dropped the gamepad: possibly already step 1, so "A did nothing" in step 3 may mean the controller was
  already gone, not that the remap worked.
- Whether the Xbox Ally X accepts BindZone at all, and whether its back buttons go through zone 8.

Rules for the next probe:
- Run the XInput watcher between every single write; if the gamepad disappears, stop and restore.
- Keep the touchscreen on as a fallback input.
- Read G-Helper's issues/changelog for Xbox Ally X remapping first.
