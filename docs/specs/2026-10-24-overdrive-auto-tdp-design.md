# Overdrive Auto TDP: design

Date: 2026-10-24. Status: design approved in conversation; awaiting owner review of this written spec.
Part of Overdrive Stage 2 (see `2026-10-23-overdrive-stage1-design.md` section 1). The CPU temperature limit and
undervolting stay for later.

## 1. Purpose

Auto TDP changes the power limit by itself while a game runs, so the Ally uses no more watts than the game needs. Less
power means a cooler, quieter Ally and longer battery life.

Owner decisions (2026-10-24):
- **FPS limit set (30 to 120):** Auto TDP holds that frame rate at the lowest watts ("hold" behaviour, G-Helper's rules).
- **FPS limit Off:** Auto TDP gives the most frames the game can reach and trims only watts that add no frames
  ("most frames" behaviour, Overdrive's own; G-Helper does nothing here).
- **Most watts:** the current mode's watts: Silent 13, Balanced 17, Turbo 35 W (`MODE_POWER`), or in Manual the
  Sustained slider (5 to 50 W).
- **Fewest watts:** 6 W (G-Helper `tdpMin`).
- **Climb +2 W per step** (G-Helper climbs +1). Owner choice, so a heavy scene recovers from 9 to 17 W in about
  1.2 s instead of 2.4 s. It still steps down 1 W at a time.
- While Auto TDP runs it sets all three limits (Sustained, Slow boost, Fast boost) to the same value, as G-Helper
  `SetTDP` writes `PPT_APUA0`, `PPT_APUA3` and `PPT_APUC1` together.

Source: G-Helper `app/Ally/AllyControl.cs` (`Timer_Elapsed`, `SetTDP`, `GetMaxTDP`, `ToggleAutoTDP`) and
`app/Gpu/AMD/AmdGpuControl.cs` (`StartFPS`, `GetFPS`, `StopFPS`) at the pinned commit
`c09017453d9ed0c52261b67a48d02bd55232e6ac`.

## 2. Frame rate source (checked live on the Ally)

The AMD driver's frame metrics (`ADL2_Adapter_FrameMetrics_Start/Get/Stop`, adapter = the iGPU, display 0), as G-Helper.
Measured on 2026-10-24 with Space Marine 2 in front: 77 to 78 FPS from the driver, against 79.2 FPS from Windows' own
DxgKrnl present events (ETW) over the same seconds. The first reading after Start is 0. The reading is the foreground
game's frame rate. With the game in the background, on the desktop or in Steam's menus it is 0. Stop returned -1 in the
live test; that error is ignored.

Fallback not built now: G-Helper's ETW present counter (`app/Overlay/EtwFpsMonitor.cs`) also worked live and needs
admin. Use it only if the driver reading proves unreliable.

## 3. Architecture

```
Panel: Auto TDP toggle + live line
   | set_auto_tdp(on), get_sensors() (adds autoTdp)
main.py ── AutoTdpLoop (asyncio task, 300 ms) ── AutoTdp controller (pure, py_modules/overdrive/auto_tdp.py)
   | helper: frame_sample (read only), set_power (existing)
asus-helper.exe ── ADL frame metrics (new, own non-prelinked class) + ADL gfx power (existing GetiGpuSensors)
```

- **Helper, new command `frame_sample`:** returns `{fps: float|null, gpuPowerW: int|null}`. Read only. Frame metrics
  start on the first call and stop when the helper exits. The imports sit in their own class that `Load` does not
  prelink, so an older driver without them cannot break the FPS limit (same pattern as PMLog). `fps` is null with one
  error entry when the call fails. New G-Helper code is marked in its file header and in THIRD-PARTY-NOTICES.
- **Python, `AutoTdp` controller (pure, no I/O):** `AutoTdp(ceiling, fps_limit)` with
  `step(fps, gpu_power) -> int | None` (new watts to write, or None). It is fed samples and returns decisions, and is
  tested with sample sequences.
- **Python, loop:** an asyncio task that runs only while Auto TDP is on. Every 300 ms (G-Helper's timer) it calls
  `frame_sample`, steps the controller, and on a decision calls `set_power` with all three limits equal. The writes take
  the same lock as user changes and reapplies, so they never interleave with a mode write. They are not user changes:
  no generation bump, nothing saved.
- Why Python and not a timer in the helper: the helper stays a plain request/reply program, and the rules are tested
  without hardware. A sample is a local pipe round trip, about 3 a second.

## 4. Rules

Ceiling `C` = the mode's watts (Manual: the Sustained slider). Floor = 6 W. Start value = `C`. Every value written is
clamped to 6..C.

### 4.1 Hold (FPS limit L set), as G-Helper except the +2 W climb

On each sample:
- `fps <= min(L * 0.9, L - 4)`: up-count +1; otherwise up-count = 0.
- `fps >= min(L * 0.95, L - 2)`: down-count +1; otherwise down-count = 0.
- up-count >= 1: both counts = 0, watts +2 (owner choice; G-Helper +1).
- down-count >= 8 and gpu power < current watts: up-count = 0, down-count -1, watts -1. After the first 8 samples
  (2.4 s) on target it can step down every sample while the condition holds, as G-Helper's `_downCount--` does.
- No GPU power reading (null): the step-down condition is treated as false (never lower without it).

### 4.2 Most frames (FPS limit Off), Overdrive's own

The hold rules from 4.1, with a target `R` measured at the ceiling instead of a fixed limit:
- **Measure:** at `C` for 10 samples (3 s); `R` = the median fps of those samples.
- **Trim:** apply 4.1 with `L = R`, so it lowers watts while fps stays within about 5 % (or 2 FPS) of `R`, and climbs
  back as soon as fps falls 10 % (or 4 FPS) below it.
- **Recheck:** every 60 s, and whenever trimming has climbed back to `C`, measure `R` again (back to `C` for 3 s).
- **No game (R < 10 FPS):** stay at `C`, measure again after 60 s.

### 4.3 Events

- **Mode change, Manual slider change, FPS limit change, reapply after sleep or AC:** the mode write has just set
  the mode's watts. The controller resets: start at the new `C`, counts 0, Most frames measures again.
- **Auto TDP turned off:** the loop stops and the mode is written again (`mode_write`), putting its normal watts back.
- **Armoury Crate switch in progress (`busy`):** samples are skipped; nothing is written.
- **Helper restart or timeout:** that sample is skipped. Three failures in a row pause Auto TDP: the mode is written
  again (`mode_write`), so the Ally runs at the mode's normal watts as if Auto TDP were off, never stuck low. It resumes
  after the next successful reapply. The panel shows "Auto TDP paused: <reason>".

## 5. Saving

New setting `auto_tdp` (bool, default false), saved like the other settings and reapplied at start: if on, the loop
starts after the reapply. The current Auto TDP watts are never saved.

## 6. Panel

Under FPS limit, in the Performance section:
- Toggle **"Auto TDP"** (single press, shows the stored state).
- Description depends on the FPS limit: "Holds 60 FPS at the lowest watts." / "FPS limit Off: the most frames,
  trimming only watts that add none."
- While on, a live line from the 2 s sensor poll: "Auto TDP: 11 W · 60 FPS" ("--" for FPS when no game is in front).
  In Most frames it adds the target: "Auto TDP: 14 W · 72 FPS (best 74)".
- The "Power limit: N W" line under the mode buttons says "Power limit: up to N W (Auto TDP)" while on.
- Manual sliders stay usable. Moving Sustained changes the ceiling.

## 7. Errors

- `frame_sample` unsupported (driver without frame metrics): the toggle is disabled with "Auto TDP needs the AMD
  driver's frame-rate reading, which this driver does not offer."
- A `set_power` refusal: the loop stops, the mode is written again, and the panel shows the device's message.
- Logs: start, stop and pause at info level. Single watt steps are not logged (they come several times a second).

## 8. Testing

- **Python unit tests, `AutoTdp` with sample sequences:**
  - hold: step up +2 W at once below the threshold, step down 1 W only after 8 on-target samples, then each sample;
  - no step down without GPU power headroom or with null power;
  - clamping to 6..C;
  - Most frames: measure, trim, climb back, recheck at 60 s, no game.
- **Python loop tests with the fake helper and a fake clock:**
  - writes only `set_power` with three equal values;
  - stops and restores the mode when turned off;
  - resets on mode, slider and FPS changes;
  - skips while busy;
  - pauses after three failures and writes the mode again (normal watts);
  - setting saved and reapplied.
- **Helper tests (fake ADL):** `frame_sample` shape; null plus error when the call fails; no writes.
- **Panel node tests:** live-line text, description per FPS limit, toggle disabled with FPS unsupported.
- **Live on the Ally with a real game in front:**
  - Hold at 60 with a scene the game holds: the watts settle below `C` while fps stays at or above 57.
  - FPS limit Off in a game capped by V-Sync or its own limit: the watts settle below `C` with fps within 5 % of the
    full-watts figure.
  - Desktop or game in the background: the watts return to `C`.
  - Turning it off puts the mode's watts back.

## 9. Review focus

1. A scene change that drops fps while watts are low: it must climb by 2 W per 300 ms at once, not wait.
2. Alt-tab or the Steam overlay: fps reads 0, so the watts climb to `C`. There must be no stall at low watts with the
   game hidden.
3. A user mode change racing a loop write: the user's mode and its watts must land last.
4. Most frames with a game whose fps swings widely: `R` from the median, with no runaway trimming below 6 W or
   oscillation faster than one step per sample.
5. Manual Sustained lowered below the current Auto TDP watts: the next write respects the new ceiling at once.

## 10. Out of scope

CPU temperature target, per-game profiles, a separate target frame rate, battery-only Auto TDP, the ETW frame source,
showing the game's frame rate when Auto TDP is off.
