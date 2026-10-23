# Overdrive

A Decky Loader plugin for Windows that puts performance controls for the ROG Ally family in the Quick Access menu: performance mode, power limits, FPS limit and fan curves. Unofficial; based on [G-Helper](https://github.com/seerge/g-helper). Licensed GPL-3.0-only.

Status: scaffold. The panel checks that the PC is an ROG Ally (BIOS `SystemProductName` contains `RC7`, read from `HKLM\HARDWARE\DESCRIPTION\System\BIOS`) and otherwise says "Overdrive supports the ROG Ally family only".

## Build

```
npm install
npm run typecheck
npm run build
```

This writes `dist/index.js`. A plugin folder for Decky contains `plugin.json`, `package.json`, `main.py`, `py_modules` and `dist`.

## Tests

The device check takes an injected reader, so the tests never touch the registry:

```
python -m pytest -q tests
```
