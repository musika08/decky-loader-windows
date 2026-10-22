r"""Linux `timedatectl` and `date` for plugins on Windows.

Reading works everywhere: `timedatectl [status]`, `timedatectl show [-p NAME]... [--value]` (Timezone as an IANA name
from Windows' ICU, NTP = Windows Time set to sync, NTPSynchronized = that and the Windows Time service running, since
Windows has no cheap "synchronized" flag), and `date [-u] [-R] [-I...] [+FORMAT]` with GNU format codes in the C
locale. %Z prints a numeric zone (+08, -0330) like glibc does for zones without an abbreviation: Windows zone names
are not abbreviations.

Changing the clock needs SeSystemtimePrivilege, and the sync setting needs administrator rights, so `timedatectl
set-time` and `set-ntp` only work in plugins with the root flag (Decky runs those elevated); others get "Access
denied" as on Linux without polkit rights. Nothing is ever elevated here. set-ntp switches Windows Time between "NTP"
and "NoSync", the switch behind Settings > Time > "Set time automatically"; any other setting (NT5DS on a domain)
already counts as on. `date` never sets the clock. set-timezone and the rest are not supported.
"""
import calendar
import getopt
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Protocol, TextIO

W32TIME_KEY = r"SYSTEM\CurrentControlSet\Services\W32Time\Parameters"
NO_SYNC = "NoSync"
ON_TYPE = "NTP"
DENIED = ("Access denied (on Windows only plugins with the root flag, which Decky runs as administrator, "
          "can change the clock)")

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


class Clock(Protocol):
    def now(self) -> datetime: ...
    def timezone_name(self) -> str: ...
    def ntp_type(self) -> str | None: ...
    def service_running(self) -> bool: ...
    def elevated(self) -> bool: ...
    def set_ntp_type(self, value: str) -> None: ...
    def start_service(self) -> None: ...
    def w32tm(self, *args: str) -> int: ...
    def set_local_time(self, value: datetime) -> None: ...


class WindowsClock:
    def now(self) -> datetime:
        return datetime.now().astimezone()

    def timezone_name(self) -> str:
        '''The IANA name (Europe/Berlin) from Windows' ICU, else the Windows zone name.'''
        import ctypes
        try:
            icu = ctypes.WinDLL("icu")
            get = icu.ucal_getDefaultTimeZone
            get.restype = ctypes.c_int32
            get.argtypes = [ctypes.c_wchar_p, ctypes.c_int32, ctypes.POINTER(ctypes.c_int)]
            buf = ctypes.create_unicode_buffer(128)
            status = ctypes.c_int(0)
            length = get(buf, len(buf), ctypes.byref(status))
            if status.value <= 0 and 0 < length < len(buf):
                return buf.value
        except (OSError, AttributeError):
            pass
        import time
        return time.tzname[0]

    def ntp_type(self) -> str | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, W32TIME_KEY) as key:
                value = winreg.QueryValueEx(key, "Type")[0]
        except OSError:
            return None
        return value if isinstance(value, str) else None

    def _service(self, access: int) -> tuple[int, int]:
        import ctypes
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi32.OpenSCManagerW.restype = ctypes.c_void_p
        advapi32.OpenServiceW.restype = ctypes.c_void_p
        advapi32.OpenServiceW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
        scm = advapi32.OpenSCManagerW(None, None, 0x0001) # SC_MANAGER_CONNECT
        if not scm:
            raise ctypes.WinError(ctypes.get_last_error())
        service = advapi32.OpenServiceW(scm, "W32Time", access)
        if not service:
            error = ctypes.get_last_error()
            advapi32.CloseServiceHandle(ctypes.c_void_p(scm))
            raise ctypes.WinError(error)
        return scm, service

    def _close(self, handles: tuple[int, int]) -> None:
        import ctypes
        advapi32 = ctypes.WinDLL("advapi32")
        for handle in reversed(handles):
            advapi32.CloseServiceHandle(ctypes.c_void_p(handle))

    def service_running(self) -> bool:
        import ctypes
        try:
            handles = self._service(0x0004) # SERVICE_QUERY_STATUS
        except OSError:
            return False
        try:
            status = (ctypes.c_uint32 * 7)()
            if not ctypes.WinDLL("advapi32").QueryServiceStatus(ctypes.c_void_p(handles[1]), status):
                return False
            return status[1] == 4 # dwCurrentState == SERVICE_RUNNING
        finally:
            self._close(handles)

    def elevated(self) -> bool:
        from .winprocess import is_elevated
        return is_elevated()

    def set_ntp_type(self, value: str) -> None:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, W32TIME_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "Type", 0, winreg.REG_SZ, value)

    def start_service(self) -> None:
        import ctypes
        handles = self._service(0x0010) # SERVICE_START
        try:
            if not ctypes.WinDLL("advapi32", use_last_error=True).StartServiceW(ctypes.c_void_p(handles[1]), 0, None):
                error = ctypes.get_last_error()
                if error != 1056: # ERROR_SERVICE_ALREADY_RUNNING
                    raise ctypes.WinError(error)
        finally:
            self._close(handles)

    def w32tm(self, *args: str) -> int:
        import ctypes, subprocess
        buf = ctypes.create_unicode_buffer(260)
        if not ctypes.windll.kernel32.GetSystemDirectoryW(buf, len(buf)): # pyright: ignore [reportAttributeAccessIssue, reportUnknownMemberType]
            return 1
        try:
            return subprocess.run([os.path.join(buf.value, "w32tm.exe"), *args], capture_output=True, timeout=60,
                                  creationflags=subprocess.CREATE_NO_WINDOW).returncode
        except (OSError, subprocess.TimeoutExpired):
            return 1

    def set_local_time(self, value: datetime) -> None:
        import ctypes
        from ctypes import wintypes
        _enable_privilege("SeSystemtimePrivilege")

        class SYSTEMTIME(ctypes.Structure):
            _fields_ = [(n, wintypes.WORD) for n in ("wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute",
                                                     "wSecond", "wMilliseconds")]
        st = SYSTEMTIME(value.year, value.month, (value.weekday() + 1) % 7, value.day, value.hour, value.minute,
                        value.second, value.microsecond // 1000)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Twice, as documented: the first call may land on the other side of a daylight saving change
        for _ in range(2):
            if not kernel32.SetLocalTime(ctypes.byref(st)):
                raise ctypes.WinError(ctypes.get_last_error())


def _enable_privilege(name: str) -> None:
    import ctypes
    from ctypes import wintypes

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", ctypes.c_int64), ("Attributes", wintypes.DWORD)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0020 | 0x0008, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        luid = ctypes.c_int64()
        if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            raise ctypes.WinError(ctypes.get_last_error())
        privileges = TOKEN_PRIVILEGES(1, (LUID_AND_ATTRIBUTES * 1)(LUID_AND_ATTRIBUTES(luid.value, 0x2))) # SE_PRIVILEGE_ENABLED
        ok = advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(privileges), 0, None, None)
        error = ctypes.get_last_error()
        if not ok or error == 1300: # ERROR_NOT_ALL_ASSIGNED: the token doesn't hold it
            raise PermissionError(DENIED)
    finally:
        kernel32.CloseHandle(token)

# --- formatting -------------------------------------------------------------------------

def zone_text(value: datetime) -> str:
    '''glibc's %Z for a zone without an abbreviation: +08, -0330, or UTC.'''
    offset = value.utcoffset() or timedelta(0)
    if value.tzinfo is timezone.utc:
        return "UTC"
    minutes = int(offset.total_seconds()) // 60
    sign = "-" if minutes < 0 else "+"
    hours, mins = divmod(abs(minutes), 60)
    return f"{sign}{hours:02d}{mins:02d}" if mins else f"{sign}{hours:02d}"


def _offset(value: datetime, colons: int) -> str:
    seconds = int((value.utcoffset() or timedelta(0)).total_seconds())
    sign = "-" if seconds < 0 else "+"
    hours, rest = divmod(abs(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if colons == 0:
        return f"{sign}{hours:02d}{minutes:02d}"
    if colons == 1:
        return f"{sign}{hours:02d}:{minutes:02d}"
    return f"{sign}{hours:02d}:{minutes:02d}:{secs:02d}"


def gnu_strftime(fmt: str, value: datetime) -> str:
    '''GNU date's format codes in the C locale, with the -, _ and 0 padding flags and ^ for upper case.'''
    weekday = value.weekday() # Monday 0
    yday = value.timetuple().tm_yday
    iso_year, iso_week, iso_day = value.isocalendar()
    hour12 = value.hour % 12 or 12
    sunday_wday = (weekday + 1) % 7
    numbers: dict[str, tuple[int, int, str]] = { # code: (value, width, default pad)
        "C": (value.year // 100, 2, "0"), "d": (value.day, 2, "0"), "e": (value.day, 2, " "),
        "g": (iso_year % 100, 2, "0"), "G": (iso_year, 4, "0"), "H": (value.hour, 2, "0"), "I": (hour12, 2, "0"),
        "j": (yday, 3, "0"), "k": (value.hour, 2, " "), "l": (hour12, 2, " "), "m": (value.month, 2, "0"),
        "M": (value.minute, 2, "0"), "q": ((value.month - 1) // 3 + 1, 1, "0"), "S": (value.second, 2, "0"),
        "u": (iso_day, 1, "0"), "U": ((yday + 6 - sunday_wday) // 7, 2, "0"), "V": (iso_week, 2, "0"),
        "w": (sunday_wday, 1, "0"), "W": ((yday + 6 - weekday) // 7, 2, "0"), "y": (value.year % 100, 2, "0"),
        "Y": (value.year, 4, "0"),
    }
    composite = {"c": "%a %b %e %H:%M:%S %Y", "D": "%m/%d/%y", "F": "%Y-%m-%d", "r": "%I:%M:%S %p", "R": "%H:%M",
                 "T": "%H:%M:%S", "x": "%m/%d/%y", "X": "%H:%M:%S"}
    out: list[str] = []
    i = 0
    while i < len(fmt):
        c = fmt[i]
        if c != "%" or i + 1 >= len(fmt):
            out.append(c)
            i += 1
            continue
        m = re.match(r"%([-_0^#]*)(:{0,3})([A-Za-z%])", fmt[i:])
        if not m:
            out.append(c)
            i += 1
            continue
        flags, colons, code = m.group(1), len(m.group(2)), m.group(3)
        i += m.end()
        text: str
        if code in numbers:
            number, width, pad = numbers[code]
            if "-" in flags:
                text = str(number)
            else:
                pad = "_" in flags and " " or "0" in flags and "0" or pad
                text = str(number).rjust(width, pad)
        elif code in composite:
            text = gnu_strftime(composite[code], value)
        elif code == "a":
            text = DAYS[weekday][:3]
        elif code == "A":
            text = DAYS[weekday]
        elif code in "bh":
            text = MONTHS[value.month - 1][:3]
        elif code == "B":
            text = MONTHS[value.month - 1]
        elif code == "p":
            text = "AM" if value.hour < 12 else "PM"
        elif code == "P":
            text = "am" if value.hour < 12 else "pm"
        elif code == "n":
            text = "\n"
        elif code == "t":
            text = "\t"
        elif code == "N":
            text = f"{value.microsecond * 1000:09d}"
        elif code == "s":
            text = str(calendar.timegm(value.astimezone(timezone.utc).timetuple()))
        elif code == "z":
            text = _offset(value, colons)
        elif code == "Z":
            text = zone_text(value)
        elif code == "%":
            text = "%"
        else:
            text = m.group(0)
        out.append(text.upper() if "^" in flags else text)
    return "".join(out)

# --- date -------------------------------------------------------------------------------

def date(args: list[str], out: TextIO, err: TextIO, clock: Clock) -> int:
    # -I and --iso-8601 take an optional value, which getopt can't express
    args = ["--iso-8601=date" if a in ("-I", "--iso-8601") else "--iso-8601=" + a[2:] if a.startswith("-I") else a
            for a in args]
    try:
        opts, rest = getopt.gnu_getopt(args, "uRd:s:", ["utc", "universal", "rfc-email", "rfc-2822",
                                                        "iso-8601=", "date=", "set="])
    except getopt.GetoptError as e:
        err.write(f"date: {e.msg} (only part of date is available on Windows)\n")
        return 1
    utc, fmt = False, "%a %b %e %H:%M:%S %Z %Y"
    for opt, value in opts:
        if opt in ("-u", "--utc", "--universal"):
            utc = True
        elif opt in ("-R", "--rfc-email", "--rfc-2822"):
            fmt = "%a, %d %b %Y %H:%M:%S %z"
        elif opt == "--iso-8601":
            iso = {"": "%Y-%m-%d", "date": "%Y-%m-%d", "hours": "%Y-%m-%dT%H%:z", "minutes": "%Y-%m-%dT%H:%M%:z",
                   "seconds": "%Y-%m-%dT%H:%M:%S%:z", "ns": "%Y-%m-%dT%H:%M:%S,%N%:z"}
            if value not in iso:
                err.write(f"date: invalid argument '{value}' for '--iso-8601'\n")
                return 1
            fmt = iso[value]
        elif opt in ("-s", "--set"):
            err.write("date: cannot set date: Operation not permitted (use timedatectl set-time)\n")
            return 1
        elif opt in ("-d", "--date"):
            err.write("date: --date is not supported on Windows\n")
            return 1
    for arg in rest:
        if not arg.startswith("+"):
            err.write("date: cannot set date: Operation not permitted (use timedatectl set-time)\n")
            return 1
        fmt = arg[1:]
    value = clock.now()
    if utc:
        value = value.astimezone(timezone.utc)
    out.write(gnu_strftime(fmt, value) + "\n")
    return 0

# --- timedatectl ------------------------------------------------------------------------

SHOW_ORDER = ("Timezone", "LocalRTC", "CanNTP", "NTP", "NTPSynchronized", "TimeUSec")
TRUE_WORDS, FALSE_WORDS = ("1", "yes", "y", "true", "t", "on"), ("0", "no", "n", "false", "f", "off")


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _properties(clock: Clock) -> dict[str, str]:
    ntp = clock.ntp_type()
    on = ntp is not None and ntp.lower() != NO_SYNC.lower()
    now = clock.now()
    return {"Timezone": clock.timezone_name(), "LocalRTC": "no", "CanNTP": "yes", "NTP": _yes(on),
            "NTPSynchronized": _yes(on and clock.service_running()),
            "TimeUSec": gnu_strftime("%a %Y-%m-%d %H:%M:%S %Z", now)}


def parse_time(text: str, now: datetime) -> datetime:
    '''systemd's set-time formats: "YYYY-MM-DD HH:MM[:SS]", "YYYY-MM-DD" (midnight) or "HH:MM[:SS]" (today).'''
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), pattern)
        except ValueError:
            pass
    for pattern in ("%H:%M:%S", "%H:%M"):
        try:
            t = datetime.strptime(text.strip(), pattern)
            return datetime(now.year, now.month, now.day, t.hour, t.minute, t.second)
        except ValueError:
            pass
    raise ValueError(f'Failed to parse time specification \'{text}\': Invalid argument')


def timedatectl(args: list[str], out: TextIO, err: TextIO, clock: Clock) -> int:
    try:
        opts, rest = getopt.gnu_getopt(args, "p:aH:M:", ["property=", "value", "all", "no-ask-password",
                                                         "adjust-system-clock", "no-pager", "host=", "machine="])
    except getopt.GetoptError as e:
        err.write(f"timedatectl: {e.msg} (only part of timedatectl is available on Windows)\n")
        return 1
    wanted: list[str] = []
    value_only = False
    for opt, value in opts:
        if opt in ("-p", "--property"):
            wanted.extend(p for p in value.split(",") if p)
        elif opt == "--value":
            value_only = True
        elif opt in ("-H", "--host", "-M", "--machine"):
            err.write("timedatectl: remote hosts and machines are not supported on Windows\n")
            return 1
    command = rest[0] if rest else "status"
    if command == "show":
        props = _properties(clock)
        for name in SHOW_ORDER:
            if not wanted or name in wanted:
                out.write((props[name] if value_only else f"{name}={props[name]}") + "\n")
        return 0
    if command == "status":
        props = _properties(clock)
        now = clock.now()
        utc = now.astimezone(timezone.utc)
        out.write(f"               Local time: {gnu_strftime('%a %Y-%m-%d %H:%M:%S %Z', now)}\n"
                  f"           Universal time: {gnu_strftime('%a %Y-%m-%d %H:%M:%S %Z', utc)}\n"
                  f"                 RTC time: n/a\n"
                  f"                Time zone: {props['Timezone']} ({zone_text(now)}, {_offset(now, 0)})\n"
                  f"System clock synchronized: {props['NTPSynchronized']}\n"
                  f"              NTP service: {'active' if props['NTP'] == 'yes' and clock.service_running() else 'inactive'}\n"
                  f"          RTC in local TZ: no\n")
        return 0
    if command == "set-ntp":
        if len(rest) != 2 or rest[1].lower() not in TRUE_WORDS + FALSE_WORDS:
            err.write("timedatectl: set-ntp takes one boolean argument\n")
            return 1
        return _set_ntp(rest[1].lower() in TRUE_WORDS, err, clock)
    if command == "set-time":
        if len(rest) != 2:
            err.write("timedatectl: set-time takes one argument\n")
            return 1
        return _set_time(rest[1], err, clock)
    err.write(f"timedatectl: {command} is not supported on Windows\n")
    return 1


def _set_ntp(enable: bool, err: TextIO, clock: Clock) -> int:
    if not clock.elevated():
        err.write(f"Failed to set ntp: {DENIED}\n")
        return 1
    current = clock.ntp_type()
    on = current is not None and current.lower() != NO_SYNC.lower()
    if on == enable:
        return 0
    try:
        clock.set_ntp_type(ON_TYPE if enable else NO_SYNC)
        if enable:
            clock.start_service()
            clock.w32tm("/config", "/update")
            clock.w32tm("/resync", "/nowait")
        elif clock.service_running():
            clock.w32tm("/config", "/update")
    except OSError as e:
        err.write(f"Failed to set ntp: {e.strerror or e}\n")
        return 1
    return 0


def _set_time(text: str, err: TextIO, clock: Clock) -> int:
    if not clock.elevated():
        err.write(f"Failed to set time: {DENIED}\n")
        return 1
    ntp = clock.ntp_type()
    if ntp is not None and ntp.lower() != NO_SYNC.lower():
        err.write("Failed to set time: Automatic time synchronization is enabled\n")
        return 1
    try:
        value = parse_time(text, clock.now())
    except ValueError as e:
        err.write(f"{e}\n")
        return 1
    try:
        clock.set_local_time(value)
    except OSError as e:
        err.write(f"Failed to set time: {e.strerror or e}\n")
        return 1
    return 0


def run(name: str, args: list[str], out: TextIO, err: TextIO, clock: Clock | None = None) -> int:
    clock = clock or WindowsClock()
    return date(args, out, err, clock) if name == "date" else timedatectl(args, out, err, clock)
