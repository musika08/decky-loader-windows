"""Overdrive's settings file (overdrive.json in Decky's per-plugin settings dir) and the fan curve rules.

Every field is checked on load on its own: a missing, wrong-typed or unknown value falls back to its default
(numbers out of range are clamped), the other fields are kept. Nothing invalid is ever sent to the helper.
The curve rules mirror helper\\src\\Validation.cs (CheckCurve, MinSpeedAt) exactly, messages included.
"""
import json
import math
import os
from typing import Optional

MODES = ("silent", "balanced", "turbo", "manual")
CURVE_MODES = ("balanced", "turbo", "silent")          # firmware curves; manual uses balanced's
PRESETS = ("default", "quiet", "cool", "custom")
ALLOWED_FPS = (0, 30, 40, 45, 50, 60, 75, 90, 120)     # 0 = off (the helper maps it to the driver's value)
POWER_MIN, POWER_MAX, POWER_DEFAULT = 5, 50, 30
CURVE_POINTS, CURVE_TEMP_MIN, CURVE_TEMP_MAX, CURVE_SPEED_MAX = 8, 20, 110, 100
QUIET_DELTA, COOL_DELTA = -10, 15

# helper\src\ArmouryServices.cs (G-Helper AsusService.cs services + servicesAC). The helper refuses a restore that
# names anything else, so a hand-edited name is dropped here instead of blocking the switch.
ARMOURY_SERVICES = (
    "ArmouryCrateControlInterface", "ArmouryCrateProArtService", "AsHidService", "ASUSOptimization",
    "AsusAppService", "ASUSLinkNear", "ASUSLinkRemote", "ASUSSoftwareManager", "ASUSLiveUpdateAgent", "ASUSSwitch",
    "ASUSSystemAnalysis", "ASUSSystemDiagnosis", "ASUSXGMobileService", "AsusCertService",
    "ArmouryCrateSEService", "ArmouryCrateService", "LightingService",
)
START_TYPES = ("Automatic", "AutomaticDelayedStart", "Manual", "Disabled")


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def clamp_power(spl: int, sppt: int, fppt: int) -> "tuple[int, int, int]":
    """Validation.ClampPower: each to 5..50 W, then fPPT >= sPPT >= SPL by raising the later ones."""
    spl, sppt, fppt = (clamp(x, POWER_MIN, POWER_MAX) for x in (spl, sppt, fppt))
    sppt = max(sppt, spl)
    fppt = max(fppt, sppt)
    return spl, sppt, fppt


def min_speed_at(temp: int) -> int:
    """Validation.MinSpeedAt (G-Helper Fans.cs): ceil((temp - 70) * 1.2), at least 0, in integer tenths."""
    tenths = (temp - 70) * 12
    return 0 if tenths <= 0 else (tenths + 9) // 10


def check_curve(temps, speeds) -> Optional[str]:
    """Validation.CheckCurve: None when valid, else the helper's reason string (first failing point wins)."""
    if (not isinstance(temps, list) or not isinstance(speeds, list) or len(temps) != CURVE_POINTS
            or len(speeds) != CURVE_POINTS or not all(_is_int(x) for x in temps + speeds)):
        return "The fan curve needs 8 points."
    for i in range(CURVE_POINTS):
        n = i + 1
        if temps[i] < CURVE_TEMP_MIN or temps[i] > CURVE_TEMP_MAX:
            return f"Point {n}: temperature must be between {CURVE_TEMP_MIN} and {CURVE_TEMP_MAX} °C."
        if speeds[i] < 0 or speeds[i] > CURVE_SPEED_MAX:
            return f"Point {n}: fan speed must be between 0 and {CURVE_SPEED_MAX} %."
        # Firmware curves repeat their last temperature: non-decreasing temps, one speed per temperature.
        if i > 0 and temps[i] < temps[i - 1]:
            return f"Point {n}: temperature can't be lower than point {i}."
        if i > 0 and speeds[i] < speeds[i - 1]:
            return f"Point {n}: fan speed can't be lower than point {i}."
        if i > 0 and temps[i] == temps[i - 1] and speeds[i] != speeds[i - 1]:
            return f"Point {n}: same temperature as point {i}, so the fan speed must be the same."
        m = min_speed_at(temps[i])
        if speeds[i] < m:
            return f"Point {n}: at {temps[i]} °C the fan must be at least {m} %."
    return None


def _device_curve(v) -> Optional[dict]:
    """A curve as the device returned it: 8 byte-sized ints each. Not required to pass check_curve (ruling P6)."""
    if not isinstance(v, dict):
        return None
    temps, speeds = v.get("temps"), v.get("speeds")
    if not isinstance(temps, list) or not isinstance(speeds, list):
        return None
    if len(temps) != CURVE_POINTS or len(speeds) != CURVE_POINTS:
        return None
    if not all(_is_int(x) and 0 <= x <= 255 for x in temps + speeds):
        return None
    return {"temps": list(temps), "speeds": list(speeds)}


def _valid_curve(v) -> Optional[dict]:
    c = _device_curve(v)
    if c is None or check_curve(c["temps"], c["speeds"]) is not None:
        return None
    return c


def preset_curve(name: str, base: Optional[dict]) -> "tuple[Optional[dict], bool]":
    """Curve for a preset derived from a device default curve, and whether it was raised to the safe minimum.

    default = the device curve; quiet = speeds - 10; cool = speeds + 15; each clamped to 0..100 and raised to
    G-Helper's minimum per point; temps unchanged. (None, False) when the base curve cannot be made valid."""
    base = _device_curve(base)
    if base is None or name not in ("default", "quiet", "cool"):
        return None, False
    delta = {"default": 0, "quiet": QUIET_DELTA, "cool": COOL_DELTA}[name]
    temps = base["temps"]
    shifted = [clamp(s + delta, 0, CURVE_SPEED_MAX) for s in base["speeds"]]
    speeds = [max(s, min_speed_at(t)) for s, t in zip(shifted, temps)]
    if check_curve(temps, speeds) is not None:
        return None, False
    adjusted = speeds != shifted
    return {"temps": list(temps), "speeds": speeds}, adjusted


def curve_mode(mode: str) -> str:
    """The firmware curve a mode uses (the helper maps manual to balanced)."""
    return mode if mode in CURVE_MODES else "balanced"


class Settings:
    def __init__(self, path: str):
        self.path = path
        self.mode = "balanced"
        self.spl = POWER_DEFAULT
        self.sppt = POWER_DEFAULT
        self.fppt = POWER_DEFAULT
        self.fps = 0
        self.fan_preset = "default"
        self.fan_curve: Optional[dict] = None          # the custom curve
        self.default_curve: Optional[dict] = None      # {mode: {temps, speeds}} read from the device on first run
        self.armoury_replaced = False
        self.armoury_recorded: dict = {}
        self.armoury_running: list = []
        # False until the owner changes something: an untouched install only reads, it never writes the device.
        self.user_set = False

    FIELDS = ("mode", "spl", "sppt", "fppt", "fps", "fan_preset", "fan_curve", "default_curve",
              "armoury_replaced", "armoury_recorded", "armoury_running", "user_set")

    @classmethod
    def load(cls, path: str) -> "Settings":
        s = cls(path)
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = None
        if isinstance(raw, dict):
            s._take(raw)
        return s

    def _take(self, raw: dict) -> None:
        if raw.get("mode") in MODES:
            self.mode = raw["mode"]
        power = []
        for key in ("spl", "sppt", "fppt"):
            v = raw.get(key)
            if _is_int(v):
                power.append(v)
            elif isinstance(v, float) and math.isfinite(v) and v == int(v):
                power.append(int(v))
            else:
                power.append(POWER_DEFAULT)
        self.spl, self.sppt, self.fppt = clamp_power(*power)
        if _is_int(raw.get("fps")) and raw["fps"] in ALLOWED_FPS:
            self.fps = raw["fps"]
        self.fan_curve = _valid_curve(raw.get("fan_curve"))
        if raw.get("fan_preset") in PRESETS:
            self.fan_preset = raw["fan_preset"]
        if self.fan_preset == "custom" and self.fan_curve is None:
            self.fan_preset = "default"
        dc = raw.get("default_curve")
        if isinstance(dc, dict):
            curves = {m: _device_curve(dc.get(m)) for m in CURVE_MODES}
            curves = {m: c for m, c in curves.items() if c is not None}
            self.default_curve = curves or None
        if isinstance(raw.get("armoury_replaced"), bool):
            self.armoury_replaced = raw["armoury_replaced"]
        rec = raw.get("armoury_recorded")
        if isinstance(rec, dict):
            self.armoury_recorded = {k: v for k, v in rec.items() if k in ARMOURY_SERVICES and v in START_TYPES}
        run = raw.get("armoury_running")
        if isinstance(run, list):
            seen = []
            for name in run:
                if isinstance(name, str) and name in self.armoury_recorded and name not in seen:
                    seen.append(name)
            self.armoury_running = seen
        if isinstance(raw.get("user_set"), bool):
            self.user_set = raw["user_set"]

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.FIELDS}

    def save(self) -> None:
        """Atomic: written next to the target, then renamed over it."""
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        tmp = self.path + ".new"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
