import io, sys
from typing import Any
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only module")

if sys.platform == "win32":
    from decky_loader.localplatform import winping


@pytest.mark.parametrize("args,linux", [
    (["-c", "3", "-W", "2", "8.8.8.8"], True),     # Network Sentinel
    (["-c3", "host"], True),
    (["-q", "-n", "host"], True),
    (["host", "-c", "1"], True),
    (["-n", "1", "-w", "500", "host"], False),      # Windows syntax
    (["host", "-n", "1"], False),
    (["host"], False),
    (["-t", "host"], False),
    (["-c", "1"], False),
    (["-c", "1", "a", "b"], False),
    (["-x", "host"], False),
])
def test_is_linux_syntax(args: list[str], linux: bool):
    assert winping.is_linux_syntax(args) is linux


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def google(host: str) -> str:
    return "8.8.8.8"


def same(host: str) -> str:
    return host


def run(args: list[str], replies: list[Any], resolve: Any = google) -> tuple[int, str, str, list[Any]]:
    clock = Clock()
    calls: list[Any] = []

    def echo(ip: str, size: int, ttl: int | None, timeout: float) -> Any:
        calls.append((ip, size, ttl, timeout))
        reply = replies.pop(0)
        clock.t += reply.rtt_ms / 1000 if reply.status == winping.IP_SUCCESS else timeout
        return reply

    out, err = io.StringIO(), io.StringIO()
    code = winping.run(args, out, err, echo, resolve, clock, clock.sleep, lambda: 1700000000.5)
    return code, out.getvalue(), err.getvalue(), calls


def ok(ms: float, ttl: int = 117) -> Any:
    return winping.Reply(winping.IP_SUCCESS, "8.8.8.8", ttl, 56, ms)


TIMEOUT = winping.Reply(winping.IP_REQ_TIMED_OUT, "8.8.8.8", 0, 0, 0)


def test_output_matches_iputils():
    code, out, err, calls = run(["-c", "3", "-W", "2", "dns.google"], [ok(12.3456), ok(0.5), ok(150.2)])
    assert code == 0 and err == ""
    assert out.splitlines() == [
        "PING dns.google (8.8.8.8) 56(84) bytes of data.",
        "64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=12.3 ms",
        "64 bytes from 8.8.8.8: icmp_seq=2 ttl=117 time=0.500 ms",
        "64 bytes from 8.8.8.8: icmp_seq=3 ttl=117 time=150 ms",
        "",
        "--- dns.google ping statistics ---",
        "3 packets transmitted, 3 received, 0% packet loss, time 2150ms",
        "rtt min/avg/max/mdev = 0.500/54.349/150.200/67.950 ms",
    ]
    assert [c[3] for c in calls] == [2.0, 2.0, 2.0]


def test_network_sentinel_style_parse():
    # What Network Sentinel reads: avg from the rtt line, jitter from mdev, loss before "%", time= values
    _, out, _, _ = run(["-c", "3", "-W", "2", "8.8.8.8"], [ok(10), TIMEOUT, ok(20)])
    lines = out.split("\n")
    rtt = next(l for l in lines if "rtt min/avg/max" in l).split("=")[1].split("/")
    assert float(rtt[1].strip().split()[0]) == 15.0 and float(rtt[3].strip().split()[0]) == 5.0
    loss = next(l for l in lines if "packet loss" in l)
    assert float(loss.split("%")[0].split()[-1]) == pytest.approx(33.3333)
    assert [float(l.split("time=")[1].split()[0]) for l in lines if "time=" in l] == [10.0, 20.0]


def test_no_reply_exits_1_without_rtt_line():
    code, out, _, _ = run(["-c", "2", "-W", "1", "8.8.8.8"], [TIMEOUT, TIMEOUT])
    assert code == 1
    assert "2 packets transmitted, 0 received, 100% packet loss" in out and "rtt" not in out


def test_unreachable_counts_errors():
    unreachable = winping.Reply(winping.IP_DEST_HOST_UNREACHABLE, "192.168.1.1", 0, 0, 0)
    code, out, _, _ = run(["-c", "1", "10.1.2.3"], [unreachable], same)
    assert code == 1
    assert "From 192.168.1.1 icmp_seq=1 Destination Host Unreachable" in out
    assert "1 packets transmitted, 0 received, +1 errors, 100% packet loss" in out


def test_quiet_timestamps_outstanding_size_ttl():
    code, out, _, calls = run(["-q", "-c", "1", "-s", "100", "-t", "5", "8.8.8.8"], [ok(1)])
    assert code == 0 and "bytes from" not in out and out.startswith("PING 8.8.8.8 (8.8.8.8) 100(128) bytes of data.")
    assert calls == [("8.8.8.8", 100, 5, winping.DEFAULT_TIMEOUT)]
    _, out, _, _ = run(["-D", "-O", "-c", "2", "8.8.8.8"], [ok(1), TIMEOUT])
    assert "[1700000000.500000] 64 bytes from 8.8.8.8" in out and "[1700000000.500000] no answer yet for icmp_seq=2" in out


def test_deadline_stops_and_fails_when_short():
    code, _, _, calls = run(["-c", "5", "-w", "2", "-W", "1", "8.8.8.8"], [TIMEOUT, TIMEOUT, TIMEOUT, TIMEOUT])
    assert code == 1 and len(calls) == 2


def test_errors():
    def unknown(host: str) -> str:
        raise OSError("nope")
    assert run(["-c", "1", "nowhere.invalid"], [], unknown)[:3] == (2, "", "ping: nowhere.invalid: Name or service not known\n")
    assert run(["-6", "-c", "1", "host"], [])[0] == 2
    assert run(["-c", "1", "::1"], [])[0] == 2
    assert run(["-c", "0", "host"], [])[0] == 2
    assert run(["-c", "1"], [])[0] == 2
    assert run(["-Z", "host"], [])[0] == 2


def always_ok(ip: str, size: int, ttl: int | None, timeout: float) -> Any:
    return ok(1)


def test_short_interval_needs_root():
    out, err = io.StringIO(), io.StringIO()
    assert winping.run(["-c", "1", "-i", "0.1", "host"], out, err, elevated=lambda: False) == 2
    assert err.getvalue() == "ping: cannot flood; minimal interval allowed for user is 200ms\n"
    clock = Clock()
    replies = [ok(1), ok(1)]
    def echo(ip: str, size: int, ttl: int | None, timeout: float) -> Any:
        return replies.pop(0)
    assert winping.run(["-c", "2", "-i", "0.1", "host"], io.StringIO(), io.StringIO(), echo, google, clock, clock.sleep,
                       elevated=lambda: True) == 0
    assert winping.run(["-c", "1", "-i", "0.2", "host"], io.StringIO(), io.StringIO(), always_ok, google, clock,
                       clock.sleep, elevated=lambda: False) == 0


def test_real_loopback_ping():
    out, err = io.StringIO(), io.StringIO()
    assert winping.run(["-c", "1", "-W", "2", "127.0.0.1"], out, err) == 0, err.getvalue()
    assert "64 bytes from 127.0.0.1: icmp_seq=1 ttl=" in out.getvalue()
    assert "1 packets transmitted, 1 received, 0% packet loss" in out.getvalue()
