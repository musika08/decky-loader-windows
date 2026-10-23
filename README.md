# Overdrive

A Decky Loader plugin for Windows that puts performance controls for the ROG Ally family in the Quick Access menu. Unofficial; based on [G-Helper](https://github.com/seerge/g-helper). Licensed GPL-3.0-only (see `LICENSE`; the G-Helper code it ports is listed in `THIRD-PARTY-NOTICES.md`).

The panel checks that the PC is an ROG Ally (BIOS `SystemProductName` contains `RC7`, read from `HKLM\HARDWARE\DESCRIPTION\System\BIOS`) and otherwise says "Overdrive supports the ROG Ally family only".

## Features

- Performance mode: Silent, Balanced, Turbo, or Manual with your own power limits (sustained, slow boost, fast boost; 5 to 50 W, fast boost kept at or above slow boost, slow boost at or above sustained). A mode Overdrive does not set (one chosen in Armoury Crate) shows as "Set by Armoury Crate"; outside Manual the power limits show as "Firmware default".
- FPS limit: Off or 30, 40, 45, 50, 60, 75, 90, 120 FPS (through the AMD driver, as G-Helper does).
- Fans: the device's own curve ("Armoury default"), Quiet, Cool, or a custom 8-point curve. Every curve stays at or above G-Helper's safe minimum.
- Live CPU temperature and fan speed.
- Settings are put back on start, after sleep and when the charger is plugged in or out.
- "Use Overdrive instead of Armoury Crate": stops the Armoury Crate services (as G-Helper does) so they do not undo these settings, and puts them back exactly as they were when turned off. A switch cut short (for example by a Decky restart) is put right at the next start.
- Reset to Armoury Crate defaults.

The battery charge limit is not part of Overdrive: it lives in the "Control Center" plugin.

## Build

```
npm install
npm run typecheck
npm run build
powershell -NoProfile -ExecutionPolicy Bypass -File helper\build.ps1
```

`npm run build` writes `dist/index.js`; `helper\build.ps1` compiles the hardware helper to `bin\asus-helper.exe` (in-box .NET Framework compiler, no download).

A plugin folder for Decky contains:

```
plugin.json
package.json
main.py
py_modules\
dist\
bin\asus-helper.exe
LICENSE
THIRD-PARTY-NOTICES.md
```

## Tests

None of the tests touch hardware, services or the registry:

```
powershell -NoProfile -ExecutionPolicy Bypass -File helper\build.ps1 -Tests
python -m pytest -q tests
node --test tests/frontend/curve.test.mjs
```
