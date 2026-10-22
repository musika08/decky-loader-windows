import io, sys
from datetime import datetime, timedelta, timezone
from typing import Any
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only module")

if sys.platform == "win32":
    from decky_loader.localplatform import wintimedate as td

MANILA = timezone(timedelta(hours=8))
NOW = datetime(2026, 3, 5, 7, 4, 9, 123456, tzinfo=MANILA)


class FakeClock:
    def __init__(self, ntp: str | None = "NTP", running: bool = True, elevated: bool = False):
        self.ntp, self.running, self.is_elevated = ntp, running, elevated
        self.calls: list[Any] = []

    def now(self) -> datetime:
        return NOW

    def timezone_name(self) -> str:
        return "Asia/Manila"

    def ntp_type(self) -> str | None:
        return self.ntp

    def service_running(self) -> bool:
        return self.running

    def elevated(self) -> bool:
        return self.is_elevated

    def set_ntp_type(self, value: str) -> None:
        self.calls.append(("type", value))
        self.ntp = value

    def start_service(self) -> None:
        self.calls.append(("start",))
        self.running = True

    def w32tm(self, *args: str) -> int:
        self.calls.append(("w32tm", *args))
        return 0

    def set_local_time(self, value: datetime) -> None:
        self.calls.append(("time", value))


def run(name: str, args: list[str], clock: Any) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = td.run(name, args, out, err, clock)
    return code, out.getvalue(), err.getvalue()

# --- reading ---

def test_show_like_deckyclocker():
    assert run("timedatectl", ["show", "-p", "NTP", "-p", "NTPSynchronized", "-p", "Timezone"], FakeClock()) == \
        (0, "Timezone=Asia/Manila\nNTP=yes\nNTPSynchronized=yes\n", "")
    assert run("timedatectl", ["show", "-p", "NTP"], FakeClock(ntp="NoSync"))[1] == "NTP=no\n"
    assert run("timedatectl", ["show", "-p", "NTPSynchronized"], FakeClock(running=False))[1] == "NTPSynchronized=no\n"
    assert run("timedatectl", ["show", "-p", "NTP"], FakeClock(ntp=None))[1] == "NTP=no\n"
    assert run("timedatectl", ["show", "--property=Timezone", "--value"], FakeClock())[1] == "Asia/Manila\n"
    _, out, _ = run("timedatectl", ["show"], FakeClock())
    assert out.splitlines() == ["Timezone=Asia/Manila", "LocalRTC=no", "CanNTP=yes", "NTP=yes", "NTPSynchronized=yes",
                                "TimeUSec=Thu 2026-03-05 07:04:09 +08"]


def test_status():
    code, out, _ = run("timedatectl", [], FakeClock())
    assert code == 0
    assert "               Local time: Thu 2026-03-05 07:04:09 +08\n" in out
    assert "           Universal time: Wed 2026-03-04 23:04:09 UTC\n" in out
    assert "                Time zone: Asia/Manila (+08, +0800)\n" in out
    assert "System clock synchronized: yes\n" in out and "NTP service: active" in out


def test_date_like_deckyclocker():
    assert run("date", ["+%Y-%m-%d %H:%M:%S"], FakeClock()) == (0, "2026-03-05 07:04:09\n", "")


def test_date_formats():
    clock = FakeClock()
    assert run("date", [], clock)[1] == "Thu Mar  5 07:04:09 +08 2026\n"
    assert run("date", ["-u", "+%F %T %Z %z"], clock)[1] == "2026-03-04 23:04:09 UTC +0000\n"
    assert run("date", ["-R"], clock)[1] == "Thu, 05 Mar 2026 07:04:09 +0800\n"
    assert run("date", ["-Iseconds"], clock)[1] == "2026-03-05T07:04:09+08:00\n"
    assert run("date", ["-I"], clock)[1] == "2026-03-05\n"
    assert run("date", ["+%s"], clock)[1] == f"{int(NOW.timestamp())}\n"


@pytest.mark.parametrize("fmt,text", [
    ("%a %A %b %B %h", "Thu Thursday Mar March Mar"), ("%d %e %-d %_m %0e", "05  5 5  3 05"),
    ("%H %I %k %l %p %P", "07 07  7  7 AM am"), ("%j %U %W %V %G %g %u %w", "064 09 09 10 2026 26 4 4"),
    ("%D %R %r %x %X", "03/05/26 07:04 07:04:09 AM 03/05/26 07:04:09"), ("%C %y %q %N", "20 26 1 123456000"),
    ("%:z %::z %%", "+08:00 +08:00:00 %"), ("%^a %^B", "THU MARCH"), ("a%nb%tc", "a\nb\tc"), ("%Q %", "%Q %"),
])
def test_gnu_strftime(fmt: str, text: str):
    assert td.gnu_strftime(fmt, NOW) == text


def test_zone_text():
    assert td.zone_text(datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=-3, minutes=-30)))) == "-0330"
    assert td.zone_text(datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=1)))) == "+01"
    assert td.zone_text(datetime(2026, 1, 1, tzinfo=timezone.utc)) == "UTC"


def test_date_never_sets_the_clock():
    clock = FakeClock(elevated=True)
    assert run("date", ["-s", "2020-01-01"], clock)[0] == 1
    assert run("date", ["010112002020"], clock)[0] == 1
    assert run("date", ["-d", "tomorrow"], clock)[0] == 1
    assert clock.calls == []

# --- changing ---

def test_unelevated_changes_are_denied():
    clock = FakeClock(elevated=False)
    code, _, err = run("timedatectl", ["set-ntp", "false"], clock)
    assert code == 1 and err.startswith("Failed to set ntp: Access denied")
    code, _, err = run("timedatectl", ["set-time", "2026-03-05 10:00:00"], FakeClock(ntp="NoSync"))
    assert code == 1 and err.startswith("Failed to set time: Access denied")
    assert clock.calls == []


def test_set_ntp_elevated():
    clock = FakeClock(ntp="NTP", running=False, elevated=True)
    assert run("timedatectl", ["set-ntp", "true"], clock)[0] == 0
    assert clock.calls == [] # already on: nothing touched (DeckyClocker does this on every load)
    assert run("timedatectl", ["set-ntp", "false"], clock)[0] == 0
    assert clock.calls == [("type", "NoSync")]
    clock.calls.clear()
    assert run("timedatectl", ["set-ntp", "yes"], clock)[0] == 0
    assert clock.calls == [("type", "NTP"), ("start",), ("w32tm", "/config", "/update"), ("w32tm", "/resync", "/nowait")]
    clock.calls.clear()
    assert run("timedatectl", ["set-ntp", "off"], clock)[0] == 0
    assert clock.calls == [("type", "NoSync"), ("w32tm", "/config", "/update")]
    assert run("timedatectl", ["set-ntp", "maybe"], clock)[0] == 1


def test_domain_sync_counts_as_on():
    clock = FakeClock(ntp="NT5DS", elevated=True)
    assert run("timedatectl", ["set-ntp", "true"], clock)[0] == 0 and clock.calls == []


def test_set_time_elevated():
    clock = FakeClock(ntp="NTP", elevated=True)
    code, _, err = run("timedatectl", ["set-time", "2026-03-05 10:00:00"], clock)
    assert code == 1 and err == "Failed to set time: Automatic time synchronization is enabled\n"
    clock.ntp = "NoSync"
    assert run("timedatectl", ["set-time", "2026-03-05 10:11:12"], clock)[0] == 0
    assert run("timedatectl", ["set-time", "13:14"], clock)[0] == 0
    assert run("timedatectl", ["set-time", "2026-02-30 10:00:00"], clock)[0] == 1
    assert clock.calls == [("time", datetime(2026, 3, 5, 10, 11, 12)), ("time", datetime(2026, 3, 5, 13, 14))]


def test_set_time_failure_is_reported():
    class Failing(FakeClock):
        def set_local_time(self, value: datetime) -> None:
            raise PermissionError(td.DENIED)
    code, _, err = run("timedatectl", ["set-time", "2026-03-05 10:00:00"], Failing(ntp="NoSync", elevated=True))
    assert code == 1 and "Access denied" in err


def test_unsupported_commands():
    assert run("timedatectl", ["set-timezone", "UTC"], FakeClock(elevated=True))[0] == 1
    assert run("timedatectl", ["-H", "host", "show"], FakeClock())[0] == 1


def test_real_clock_reads():
    clock = td.WindowsClock()
    assert clock.timezone_name()
    assert clock.ntp_type() is None or isinstance(clock.ntp_type(), str)
    assert isinstance(clock.service_running(), bool)
    assert clock.now().tzinfo is not None
