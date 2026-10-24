"""The sampling values Controller Auto uses, the same as Overdrive's Auto TDP (auto_tdp.py PERIOD, auto_tdp_loop.py
SAMPLE_TIMEOUT, ERROR_LOG_EVERY and _finite at Overdrive ba0307a). Traction has no Auto TDP, so they live here."""
import math

PERIOD = 0.3             # seconds between two decisions
SAMPLE_TIMEOUT = 2.0     # frame_sample and foreground: a short timeout (they do not count toward a helper kill)
ERROR_LOG_EVERY = 60.0   # seconds between stack traces of a sample that keeps raising


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
