"""CPU usage in % between two polls, from Win32 GetSystemTimes (idle, kernel and user time; kernel includes idle).

The first sample only records the times, so it gives None; every later one gives the busy share since the sample
before it, as a whole percent 0..100. The reader is injectable so tests never call Windows."""
import ctypes
from typing import Callable, Optional, Tuple

Times = Tuple[int, int, int]   # idle, kernel, user (100 ns units)


def _filetime_int(ft) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def system_times() -> Optional[Times]:
    """GetSystemTimes, or None when it cannot be called (not Windows, or the call failed)."""
    try:
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return _filetime_int(idle), _filetime_int(kernel), _filetime_int(user)
    except Exception:
        return None


class CpuUsage:
    def __init__(self, reader: Callable[[], Optional[Times]] = system_times):
        self._reader = reader
        self._last: Optional[Times] = None

    def sample(self) -> Optional[int]:
        try:
            now = self._reader()
        except Exception:
            now = None
        last, self._last = self._last, now
        if now is None or last is None:
            return None
        idle = now[0] - last[0]
        total = (now[1] - last[1]) + (now[2] - last[2])
        if total <= 0 or idle < 0:
            return None
        busy = (total - idle) * 100.0 / total
        return int(round(min(100.0, max(0.0, busy))))
