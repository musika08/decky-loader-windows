"""Auto TDP controller: pure rules, no I/O. See docs/specs/2026-10-24-overdrive-auto-tdp-design.md section 4.

Hold rules follow G-Helper AllyControl.Timer_Elapsed except the +2 W climb (owner choice).
"""
from statistics import median
from typing import List, Optional

FLOOR = 6
STEP_UP = 2
STEP_DOWN = 1
DOWN_SAMPLES = 8
MEASURE_SAMPLES = 10
RECHECK_S = 60.0
MIN_GAME_FPS = 10.0
PERIOD = 0.3


class AutoTdp:
    def __init__(self, ceiling: int, fps_limit: int, now: float):
        self.reset(ceiling, fps_limit, now)

    def reset(self, ceiling: int, fps_limit: int, now: float) -> None:
        self.ceiling = int(ceiling)
        self.fps_limit = int(fps_limit)
        self.watts = self.ceiling
        self.up_count = 0
        self.down_count = 0
        self._samples: List[float] = []
        self._measured_at = now
        if self.fps_limit > 0:
            self.phase = "hold"
            self.target: Optional[float] = float(self.fps_limit)
        else:
            self.phase = "measure"
            self.target = None

    def _clamp(self, watts: int) -> int:
        return max(min(FLOOR, self.ceiling), min(self.ceiling, watts))

    def _hold(self, fps: float, gpu_power: Optional[int], limit: float) -> None:
        """Apply spec 4.1 with target `limit`; updates self.watts."""
        if fps <= min(limit * 0.9, limit - 4):
            self.up_count += 1
        else:
            self.up_count = 0
        if fps >= min(limit * 0.95, limit - 2):
            self.down_count += 1
        else:
            self.down_count = 0
        if self.up_count >= 1:
            self.up_count = 0
            self.down_count = 0
            self.watts = self._clamp(self.watts + STEP_UP)
        elif self.down_count >= DOWN_SAMPLES and gpu_power is not None and gpu_power < self.watts:
            self.up_count = 0
            self.down_count -= 1
            self.watts = self._clamp(self.watts - STEP_DOWN)

    def _start_measure(self) -> None:
        self.phase = "measure"
        self.target = None
        self._samples = []
        self.up_count = 0
        self.down_count = 0

    def step(self, fps: float, gpu_power: Optional[int], now: float) -> Optional[int]:
        before = self.watts
        if self.fps_limit > 0:
            self._hold(fps, gpu_power, float(self.fps_limit))
        elif self.phase == "measure":
            self.watts = self.ceiling
            self._samples.append(fps)
            if len(self._samples) >= MEASURE_SAMPLES:
                r = float(median(self._samples))
                self._samples = []
                self._measured_at = now
                self.phase = "trim"
                self.up_count = 0
                self.down_count = 0
                self.target = r if r >= MIN_GAME_FPS else None
        else:
            if now - self._measured_at >= RECHECK_S:
                self._start_measure()
                self.watts = self.ceiling
            elif self.target is not None:
                self._hold(fps, gpu_power, self.target)
                if self.watts > before and self.watts >= self.ceiling:
                    self._start_measure()
        return self.watts if self.watts != before else None
