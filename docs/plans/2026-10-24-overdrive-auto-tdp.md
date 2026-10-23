# Overdrive Auto TDP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto TDP changes the power limit by itself while a game runs: it holds the FPS limit at the lowest watts, or,
with the limit Off, gives the most frames while trimming watts that add none.

**Architecture:**
- The helper gets one read-only command, `frame_sample`: the frame rate from the AMD driver plus graphics power.
- A pure Python controller (`AutoTdp`) turns samples into watt decisions.
- A Python loop (`AutoTdpLoop`, 300 ms) feeds it and writes all three limits through the existing `set_power`, under
  the Reapplier's lock.
- The panel gets a toggle and a live line.

**Tech Stack:**
- Helper: C# 5 on .NET Framework 4.8, built with the in-box csc via `helper\build.ps1`.
- Backend: Python 3.13 (asyncio).
- Panel: TypeScript/React with `@decky/ui`, built with rollup.

**Spec:** `docs/specs/2026-10-24-overdrive-auto-tdp-design.md`. Read it first; it is the authority.

## Global Constraints

- Floor 6 W. Ceiling = `MODE_POWER[mode]` (silent 13, balanced 17, turbo 35), or `settings.spl` in Manual.
- Climb +2 W per step, step down 1 W. 8 on-target samples before the first step down. Period 300 ms.
- Hold thresholds:
  - up when `fps <= min(L*0.9, L-4)`;
  - on target when `fps >= min(L*0.95, L-2)`;
  - step down only when `gpu_power < watts` (null power never steps down).
- Most frames:
  - `R` = median of 10 samples at the ceiling;
  - recheck every 60 s and whenever trimming climbs back to the ceiling;
  - `R < 10` FPS counts as no game: stay at the ceiling.
- All three limits are written equal: `set_power {"spl": w, "sppt": w, "fppt": w}`.
- Pause after 3 consecutive failed samples: write the mode again with `mode_write`, then resume after the next
  successful reapply.
- Writes by the loop are not user changes: no generation bump, nothing saved.
- The helper is read only for this feature. There are no new device write ids.
- New G-Helper code (FrameMetrics imports, commit `c09017453d9ed0c52261b67a48d02bd55232e6ac`, `app/Gpu/AMD/AmdGpuControl.cs`
  `StartFPS/GetFPS/StopFPS`) is marked in its file header and listed in `THIRD-PARTY-NOTICES.md`.
- Test commands:
  - Python: `"X:/Projects/Decky Loader/decky-loader/backend/.venv/Scripts/python.exe" -m pytest -q -p no:cacheprovider --basetemp <scratch dir>`
  - Helper: `powershell -File helper/build.ps1 -Tests`
  - Panel: `node --test tests/frontend/*.test.mjs`, `npx tsc --noEmit`, `npm run build`
- Subagents never install, restart Decky or touch the live device. Task 7 is done by the controller.
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Plugin unload or Decky restart while Auto TDP sits at low watts.** The mode's watts must be written back before
   the helper stops, so the Ally is not left at, say, 7 W (Task 5 test `test_unload_restores_mode_watts`).
2. **A user mode change racing a loop write.** The user's mode and its watts land last; a loop write sees the newer
   generation and drops (Task 4 test `test_loop_write_dropped_after_user_change`).
3. **Alt-tab or the Steam overlay mid-game (fps 0).** Watts climb to the ceiling by 2 W per sample, never stalling
   low (Task 2 test `test_hold_zero_fps_climbs_to_ceiling`).
4. **Manual Sustained lowered below the current Auto TDP watts.** The next write is at most the new ceiling (Task 4
   test `test_reset_lowers_to_new_ceiling`).
5. **Helper restarted mid-game.** Frame metrics start again on the next `frame_sample`, with no stale "started" flag
   (Task 1 test `FrameSample_RestartsMetricsAfterReset`).

---

### Task 1: Helper `frame_sample` command

**Files:**
- Modify: `helper/src/Hardware.cs` (IAdl)
- Modify: `helper/src/AdlHardware.cs`
- Modify: `helper/src/ghelper/AmdGpuControl.cs` (or a new `helper/src/ghelper/AmdFrameMetrics.cs`)
- Modify: `helper/src/Protocol.cs` (dispatch + handler)
- Modify: `helper/tests/FakeHardware.cs`, `helper/tests/HelperTests.cs`
- Modify: `THIRD-PARTY-NOTICES.md`

**Interfaces:**
- Produces: `IAdl.float? GetFrameRate()`:
  - null when ADL or its frame metrics are unavailable (reason in `Unavailable`);
  - 0 when no game is in front.
- Produces: `IAdl.bool FrameRateSupported { get; }`, false when the FrameMetrics entry points are missing.
- Produces: the helper command `frame_sample` (no args), replying
  `{"supported": bool, "fps": float|null, "gpuPowerW": int|null, "errors": [string]}`.
  `gpuPowerW` is `GetGpuSensors().GfxPowerW` in 1..1000, else null.

- [ ] **Step 1: Write the failing tests** in `HelperTests.cs` (FakeAdl gains `public float? Fps = 60f; public bool FrameSupported = true; public int FrameStarts;`):
  - `FrameSample_ReturnsFpsAndGfxPower`: Fps 58.5, Gpu.GfxPowerW 7. Expect `supported=true, fps=58.5, gpuPowerW=7, errors=[]`, and no ACPI or ADL writes.
  - `FrameSample_Unsupported`: FrameSupported=false. Expect `supported=false, fps=null`, and one error containing "frame rate".
  - `FrameSample_ZeroPowerIsNull`: GfxPowerW 0. Expect `gpuPowerW=null`.
  - `FrameSample_RestartsMetricsAfterReset`: two samples give FrameStarts 1. After a helper restart, with a new core on the same FakeAdl and the fake's started flag cleared, the next sample makes FrameStarts 2.
- [ ] **Step 2: Run** `powershell -File helper/build.ps1 -Tests`. Expected: the new tests fail to compile or fail.
- [ ] **Step 3: Implement.**
  - The FrameMetrics P/Invokes (`ADL2_Adapter_FrameMetrics_Start/Get/Stop(IntPtr ctx, int adapter, int display[, out float fps])`) go in their own class that `Load` does not prelink, as `PMLogMethods` does. Display is 0.
  - `AdlHardware` starts the metrics on the first `GetFrameRate` and stops them in `Dispose`. The result of Stop is ignored (it returned -1 live).
  - `EntryPointNotFoundException` sets `FrameRateSupported=false`.
  - Add the file-header G-Helper attribution and a THIRD-PARTY-NOTICES line.
- [ ] **Step 4: Run** `helper/build.ps1 -Tests`. Expected: all pass, BUILD OK.
- [ ] **Step 5: Commit** "Helper: frame_sample (AMD frame metrics + graphics power), read only".

### Task 2: `AutoTdp` controller (pure)

**Files:**
- Create: `py_modules/overdrive/auto_tdp.py`
- Test: `tests/test_auto_tdp.py`

**Interfaces:**
- Produces, as constants: `FLOOR=6, STEP_UP=2, STEP_DOWN=1, DOWN_SAMPLES=8, MEASURE_SAMPLES=10, RECHECK_S=60.0,
  MIN_GAME_FPS=10.0, PERIOD=0.3`.
- Produces: `class AutoTdp`, with:
  - `__init__(self, ceiling: int, fps_limit: int, now: float)`;
  - `reset(self, ceiling: int, fps_limit: int, now: float) -> None`;
  - `step(self, fps: float, gpu_power: Optional[int], now: float) -> Optional[int]`: the new watts to write, or None
    when unchanged;
  - attributes `watts: int`, `target: Optional[float]` (L in hold, R in most frames, None while measuring) and
    `phase: str`, one of `"hold" | "measure" | "trim"`.
- Rules: Global Constraints and spec section 4, verbatim.
  - `fps_limit` 0 means most frames. Start at `ceiling`.
  - In most frames, `step` returns the ceiling when a measure begins below it.

- [ ] **Step 1: Write the failing tests** (`now` advances 0.3 per sample):
  - `test_hold_steps_up_two_at_once`: C=17, L=60, watts 11 (reached by trimming). A sample at 53 fps (≤ min(54, 56)) returns 13.
  - `test_hold_steps_down_after_eight_then_each`: C=17, L=60, gpu 5. 7 samples at 59 return None; the 8th returns 16; the 9th returns 15.
  - `test_hold_no_step_down_without_headroom`: gpu_power == watts or None → never steps down.
  - `test_hold_clamps`: never below 6 or above C.
  - `test_hold_zero_fps_climbs_to_ceiling`: from 6 W, fps 0 samples give 8, 10, 12, ... then cap at 17.
  - `test_most_frames_measures_median_then_trims`: L=0, C=17. 10 samples [70,72,74,71,73,75,72,74,73,72] give target 72.5 and phase "trim". Then on-target samples trim 1 W at a time.
  - `test_most_frames_rechecks_after_60s`: at now ≥ 60 s after the measure, the step returns 17 and phase becomes "measure".
  - `test_most_frames_rechecks_when_back_at_ceiling`: trimming climbs back to C, so phase becomes "measure".
  - `test_most_frames_no_game_stays_at_ceiling`: median < 10 keeps watts at C and measures again after 60 s.
  - `test_reset_starts_at_new_ceiling`: reset(13, 60, t) sets watts 13 and zeroes the counts.
- [ ] **Step 2: Run** `pytest tests/test_auto_tdp.py -q`. Expected: ImportError.
- [ ] **Step 3: Implement** `AutoTdp` in `py_modules/overdrive/auto_tdp.py`. Stdlib only (`statistics.median`).
- [ ] **Step 4: Run.** Expected: PASS.
- [ ] **Step 5: Commit** "Auto TDP controller (hold the FPS limit / most frames)".

### Task 3: Setting `auto_tdp`

**Files:**
- Modify: `py_modules/overdrive/settings.py` (`Settings.__init__`, `FIELDS`, load validation)
- Test: `tests/test_settings.py`

**Interfaces:**
- Produces: `Settings.auto_tdp: bool`, default False, saved and loaded like the other fields. A non-bool value loads
  as False.

- [ ] **Step 1: Failing tests:**
  - `test_auto_tdp_round_trip`: True survives save/load.
  - `test_auto_tdp_bad_value_is_false`: "yes" loads as False.
  - `test_auto_tdp_missing_is_false`: an old file without the key loads as False.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement**, following how the existing bool `armoury_replaced` is loaded.
- [ ] **Step 4: Run.** Expected: PASS (the whole suite).
- [ ] **Step 5: Commit** "Settings: auto_tdp".

### Task 4: `AutoTdpLoop`

**Files:**
- Create: `py_modules/overdrive/auto_tdp_loop.py`
- Modify: `py_modules/overdrive/reapply.py` (add `on_applied` hook)
- Modify: `tests/fake_helper.py` (a `frame_sample` reply)
- Test: `tests/test_auto_tdp_loop.py`

**Interfaces:**
- Consumes: `AutoTdp` (Task 2), `Settings.auto_tdp` (Task 3), `mode_write(mode, manual_power=None)` and
  `MODE_POWER` (existing), `Reapplier.lock` and `Reapplier.generation`.
- Produces: `ceiling(settings: Settings) -> int`, which is `MODE_POWER[mode]`, or `settings.spl` in Manual.
- Produces: `class AutoTdpLoop`, with:
  - `__init__(self, client, settings, reapplier, clock=time, sleep=asyncio.sleep, logger=None)`;
  - `start(self) -> None`;
  - `async stop(self, restore: bool = True) -> None`: when `restore` is set, writes `set_mode` with
    `mode_write(settings.mode, (spl, sppt, fppt))`;
  - `reset(self) -> None`: the new ceiling and FPS limit from settings; clears a pause;
  - `status(self) -> dict`: `{"on": bool, "watts": int|None, "fps": float|None, "best": float|None,
    "paused": str, "supported": bool}`;
  - `async run_once(self) -> None`: one sample, for tests.
- Produces: `Reapplier.on_applied: Optional[Callable[[], None]]`, called at the end of a successful (not dropped)
  `apply_all`.
- Loop rules:
  - skip when `reapplier._busy()`;
  - a write takes `reapplier.lock`, and is dropped when `generation` changed since the sample;
  - `set_power` refused: stop with `restore=True` and keep the message in `status["paused"]`;
  - `supported=False`: stop without writes and report unsupported;
  - 3 consecutive failures (transport, `ok` false, or `fps` None) write `mode_write(...)` once, set paused to
    "Auto TDP paused: <reason>", and stop sampling until `reset()`.

- [ ] **Step 1: Failing tests** (fake client handlers for `frame_sample`; `run_once` driven directly):
  - `test_writes_three_equal_limits`: a drop below the threshold writes `("set_power", {"spl":15,"sppt":15,"fppt":15})` from 13.
  - `test_ceiling_per_mode`: silent 13, balanced 17, turbo 35, manual = spl 22.
  - `test_stop_restores_mode`: `stop()` writes `("set_mode", mode_write(...))`.
  - `test_skips_while_busy`: busy means no calls.
  - `test_pause_after_three_failures_restores_mode`: 3 fps-None replies write `set_mode` once, set paused; no more samples until `reset()`.
  - `test_reapply_hook_resets_and_resumes`: `reapplier.on_applied()` clears the pause and restarts at the ceiling.
  - `test_loop_write_dropped_after_user_change`: generation bumped between sample and write, so no `set_power`.
  - `test_reset_lowers_to_new_ceiling`: at 15 W in Manual, with spl lowered to 10 and `reset()`, the next write is ≤ 10.
  - `test_unsupported_stops_without_writes`.
  - `test_power_refused_stops_and_restores`.
- [ ] **Step 2: Run.** Expected: ImportError / FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the whole Python suite. Expected: PASS.
- [ ] **Step 5: Commit** "Auto TDP loop (300 ms, three equal limits, pause restores the mode)".

### Task 5: Plugin wiring (`main.py`)

**Files:**
- Modify: `main.py`
- Test: `tests/test_main.py`

**Interfaces:**
- Consumes: `AutoTdpLoop`, `Reapplier.on_applied` (Task 4).
- Produces: `async Plugin.set_auto_tdp(self, on) -> dict`. It refuses as other writes do (`_refuse_write`), saves
  `auto_tdp`, starts the loop, or stops it with restore.
- Produces: `get_state` adds `"auto_tdp": {"on": bool, "supported": bool}`.
- Produces: `get_sensors` adds `"autoTdp": loop.status()`.
- Wiring:
  - `_attach` creates the loop and sets `reapplier.on_applied = loop.reset`;
  - `_startup_sequence` starts the loop after `reapplier.start()` when `settings.auto_tdp`;
  - `set_mode`, `set_fps` and the Manual slider path (all through `set_mode`) call `loop.reset()` after a successful
    write;
  - `reset_defaults` turns Auto TDP off (saves False, stops without a second restore);
  - `_unload` awaits `loop.stop(restore=True)` before `reapplier.stop()` and `client.stop()`.
- Ruling: "Reset to Armoury Crate defaults" turns Auto TDP off, because Armoury's defaults have none.

- [ ] **Step 1: Failing tests:**
  - `test_set_auto_tdp_saves_and_starts`.
  - `test_set_auto_tdp_off_restores_mode`.
  - `test_mode_change_resets_loop`.
  - `test_reset_defaults_turns_auto_tdp_off`.
  - `test_unload_restores_mode_watts`: the last write before the helper stops is `set_mode` with the mode's watts.
  - `test_state_and_sensors_report_auto_tdp`.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the whole suite. Expected: PASS.
- [ ] **Step 5: Commit** "Plugin: Auto TDP switch, status, reset on changes, restore on unload".

### Task 6: Panel

**Files:**
- Modify: `src/curve.ts`
- Modify: `src/index.tsx`
- Test: `tests/frontend/curve.test.mjs`

**Interfaces:**
- Consumes: `state.auto_tdp`, `sensors.autoTdp` (Task 5). The callable is `set_auto_tdp(on: boolean)`.
- Produces in `curve.ts`:
  - `autoTdpDescription(fpsLimit: number): string`;
  - `autoTdpLine(s: {watts: number|null, fps: number|null, best: number|null, paused: string}): string`;
  - `modePowerLine(mode, autoOn = false)`.
- Exact copy (spec section 6):
  - `"Holds 60 FPS at the lowest watts."` (with the limit's number);
  - `"FPS limit Off: the most frames, trimming only watts that add none."`;
  - `"Auto TDP: 11 W · 60 FPS"`, with `--` for a null or 0 fps;
  - `"Auto TDP: 14 W · 72 FPS (best 74)"` when `best` is set (rounded);
  - a non-empty `paused` replaces the line;
  - `"Power limit: up to 17 W (Auto TDP)"`;
  - unsupported: `"Auto TDP needs the AMD driver's frame-rate reading, which this driver does not offer."`
- Toggle:
  - a `ToggleField` "Auto TDP" under the FPS limit picker, `checked` fed like the Armoury toggle (the stored state);
  - disabled while working, busy or unsupported;
  - a single press.

- [ ] **Step 1: Failing node tests** for the three functions with the exact strings above.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement** the functions and the panel row.
- [ ] **Step 4: Run** `node --test tests/frontend/*.test.mjs`, `npx tsc --noEmit` and `npm run build`. Expected: all clean.
- [ ] **Step 5: Commit** "Panel: Auto TDP toggle and live line".

### Task 7: Install and live test (controller only)

- [ ] Run `tools/install-dev.ps1` elevated, then confirm the installed `main.py` and helper hashes match and Decky answers.
- [ ] With the owner's game in front and FPS limit 60, Auto TDP on in Balanced: within 30 s the watts settle below 17
  with fps ≥ 57 (read from `get_sensors().autoTdp`, polled once a second for 60 s).
- [ ] With FPS limit Off: `best` is reported and the watts trim below the ceiling only while fps stays within 5 % of it.
- [ ] Alt-tab to the desktop: the watts return to the ceiling within 3 s.
- [ ] Turn it off: `get_state` shows off, and the last write was the mode with its watts.
- [ ] Report the measured numbers to the owner. Restore their settings.
