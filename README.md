# Traction

A Decky Loader plugin for Windows that puts controller and touch controls for the ROG Ally family in the Quick Access menu. Unofficial; based on [G-Helper](https://github.com/seerge/g-helper). Licensed GPL-3.0-only (see `LICENSE`; the G-Helper code it ports is listed in `THIRD-PARTY-NOTICES.md`).

## Features

- Controller mode: Gamepad, Desktop or Auto.
- Grip vibration: turn the grip motors on or off.
- Touch Screen: switch the touchscreen on or off.
- Armoury Crate toggle: stops or restores the Armoury Crate services. It is the same switch Overdrive has, shared between the two plugins so they never fight over it.

More controls are coming: button remap, stick deadzones, stick LEDs, refresh rate and turning the Xbox controller off.

## With or without Overdrive

Traction works on its own, and it works next to Overdrive. If Overdrive is installed it must be version 0.2.0 or newer (older versions still drive the controller themselves, so Traction leaves the controller alone and the panel tells you to update).

Settings carry over from Overdrive the first time Traction loads, so the controller mode, vibration and touchscreen choices you made there are kept.

## Build

```
npm install
npm run typecheck
npm run build
powershell -NoProfile -ExecutionPolicy Bypass -File helper\build.ps1
```

`npm run build` writes `dist/index.js`; `helper\build.ps1` compiles the hardware helper to `bin\traction-helper.exe`.

A plugin folder for Decky contains:

```
plugin.json
package.json
main.py
py_modules\
dist\
bin\traction-helper.exe
LICENSE
THIRD-PARTY-NOTICES.md
```

## Release zip

```
python tools/make-zip.py [output folder]
```

Writes `traction-<version>.zip` (top folder `traction/`) to `dist-release/` or the folder you give, and prints its sha256. Run the build first.

## Tests

None of the tests touch hardware, services or the registry:

```
python -m pytest -q tests
node --test tests/frontend/*.test.mjs
```
