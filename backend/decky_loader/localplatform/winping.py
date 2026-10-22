r"""Linux (iputils) ping for plugins on Windows, on top of IcmpSendEcho, which needs no administrator rights.

Plugins run `ping -c N -W S host` and parse iputils' output ("64 bytes from 1.2.3.4: icmp_seq=1 ttl=118 time=12.3 ms",
"3 packets transmitted, 3 received, 0% packet loss, time 2003ms", "rtt min/avg/max/mdev = ..."). Windows' ping.exe
takes other options (-n, -w in ms) and prints localized text, so calls in Linux syntax run this instead (see
is_linux_syntax). IPv4 only. Replies are waited for one at a time: a lost packet costs its full -W timeout.
"""
import getopt
import math
import socket
import time
from typing import Callable, NamedTuple, TextIO

DEFAULT_SIZE = 56
MIN_USER_INTERVAL = 0.2 # seconds; below this only for elevated (root) plugins, like iputils for root
DEFAULT_TIMEOUT = 4.0 # seconds, when -W is not given (Windows ping's default)
# getopt spec of the iputils options handled here
SHORT_OPTS = "46c:i:nqs:t:W:w:DOv"
# Options only iputils has (Windows ping has no -W, -q, -D or -O, and -c means a routing compartment there)
LINUX_ONLY = frozenset({"-c", "-W", "-q", "-D", "-O"})

# IcmpSendEcho status codes (ipexport.h)
IP_SUCCESS = 0
IP_DEST_NET_UNREACHABLE = 11002
IP_DEST_HOST_UNREACHABLE = 11003
IP_DEST_PROT_UNREACHABLE = 11004
IP_DEST_PORT_UNREACHABLE = 11005
IP_REQ_TIMED_OUT = 11010
IP_TTL_EXPIRED_TRANSIT = 11013
UNREACHABLE_TEXT = {
    IP_DEST_NET_UNREACHABLE: "Destination Net Unreachable",
    IP_DEST_HOST_UNREACHABLE: "Destination Host Unreachable",
    IP_DEST_PROT_UNREACHABLE: "Destination Protocol Unreachable",
    IP_DEST_PORT_UNREACHABLE: "Destination Port Unreachable",
    IP_TTL_EXPIRED_TRANSIT: "Time to live exceeded",
}


class Reply(NamedTuple):
    status: int        # IP_SUCCESS, IP_REQ_TIMED_OUT or another IcmpSendEcho status
    address: str       # who answered
    ttl: int
    data_size: int
    rtt_ms: float


Echo = Callable[[str, int, int | None, float], Reply]


def _is_elevated() -> bool:
    from .winprocess import is_elevated
    return is_elevated()


def is_linux_syntax(args: list[str]) -> bool:
    '''True for `ping` arguments only iputils understands: they parse as iputils options with one host, and use an
    option Windows' ping doesn't have. `ping -n 1 -w 500 host` (Windows) and plain `ping host` stay with ping.exe.'''
    try:
        opts, rest = getopt.gnu_getopt(args, SHORT_OPTS)
    except getopt.GetoptError:
        return False
    return len(rest) == 1 and any(opt in LINUX_ONLY for opt, _ in opts)


def icmp_echo() -> Echo:
    '''An Echo sending real ICMP echo requests with IcmpSendEcho.'''
    import ctypes
    from ctypes import wintypes

    class IP_OPTION_INFORMATION(ctypes.Structure):
        _fields_ = [("Ttl", ctypes.c_ubyte), ("Tos", ctypes.c_ubyte), ("Flags", ctypes.c_ubyte),
                    ("OptionsSize", ctypes.c_ubyte), ("OptionsData", ctypes.c_void_p)]

    class ICMP_ECHO_REPLY(ctypes.Structure):
        _fields_ = [("Address", ctypes.c_ulong), ("Status", ctypes.c_ulong), ("RoundTripTime", ctypes.c_ulong),
                    ("DataSize", ctypes.c_ushort), ("Reserved", ctypes.c_ushort), ("Data", ctypes.c_void_p),
                    ("Options", IP_OPTION_INFORMATION)]

    iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    iphlpapi.IcmpCreateFile.restype = wintypes.HANDLE
    iphlpapi.IcmpCreateFile.argtypes = []
    iphlpapi.IcmpSendEcho.restype = wintypes.DWORD
    iphlpapi.IcmpSendEcho.argtypes = [wintypes.HANDLE, ctypes.c_ulong, ctypes.c_void_p, wintypes.WORD,
                                      ctypes.POINTER(IP_OPTION_INFORMATION), ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    handle = iphlpapi.IcmpCreateFile()
    if not handle or handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())

    def echo(ip: str, size: int, ttl: int | None, timeout: float) -> Reply:
        payload = bytes(i & 0xFF for i in range(size))
        reply_size = ctypes.sizeof(ICMP_ECHO_REPLY) + size + 64
        buffer = ctypes.create_string_buffer(reply_size)
        options = IP_OPTION_INFORMATION(Ttl=ttl) if ttl is not None else None
        address = int.from_bytes(socket.inet_aton(ip), "little") # IPAddr: network byte order in memory
        start = time.perf_counter()
        count = iphlpapi.IcmpSendEcho(handle, address, payload, size, ctypes.byref(options) if options else None,
                                      buffer, reply_size, max(1, int(timeout * 1000)))
        rtt = (time.perf_counter() - start) * 1000
        if not count:
            error = ctypes.get_last_error()
            if error == IP_REQ_TIMED_OUT:
                return Reply(IP_REQ_TIMED_OUT, ip, 0, 0, rtt)
            if error in UNREACHABLE_TEXT:
                return Reply(error, ip, 0, 0, rtt)
            raise ctypes.WinError(error)
        reply = ICMP_ECHO_REPLY.from_buffer(buffer)
        source = socket.inet_ntoa(int(reply.Address).to_bytes(4, "little"))
        return Reply(int(reply.Status), source, int(reply.Options.Ttl), int(reply.DataSize), rtt)

    return echo


def resolve_ipv4(host: str) -> str:
    return str(socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0])


def _rtt(ms: float) -> str:
    '''iputils' precision: 3 decimals below 1 ms, 2 below 10, 1 below 100, none above.'''
    for limit, digits in ((1, 3), (10, 2), (100, 1)):
        if ms < limit:
            return f"{ms:.{digits}f}"
    return f"{ms:.0f}"


def _number(text: str, what: str, minimum: float, maximum: float, integer: bool) -> float:
    try:
        value = int(text) if integer else float(text)
    except ValueError:
        raise ValueError(f"invalid argument: '{text}'")
    if not minimum <= value <= maximum:
        raise ValueError(f"invalid argument: '{text}': out of range: {minimum:g} <= value <= {maximum:g}")
    return value


def run(args: list[str], out: TextIO, err: TextIO, echo: Echo | None = None,
        resolve: Callable[[str], str] = resolve_ipv4, clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep, wall: Callable[[], float] = time.time,
        elevated: Callable[[], bool] | None = None) -> int:
    try:
        opts, rest = getopt.gnu_getopt(args, SHORT_OPTS)
    except getopt.GetoptError as e:
        err.write(f"ping: {e.msg} (only part of ping is available on Windows)\n")
        return 2
    if len(rest) != 1:
        err.write("ping: usage error: Destination address required\n" if not rest else "ping: only one destination is supported\n")
        return 2
    host = rest[0]
    count: int | None = None
    interval, timeout, deadline = 1.0, DEFAULT_TIMEOUT, None
    size, ttl, quiet, timestamps, outstanding = DEFAULT_SIZE, None, False, False, False
    try:
        for opt, value in opts:
            if opt == "-c":
                count = int(_number(value, "count", 1, 2**31 - 1, True))
            elif opt == "-i":
                interval = _number(value, "interval", 0, 2**31 - 1, False)
                if interval < MIN_USER_INTERVAL and not (elevated or _is_elevated)():
                    # As iputils does for users without CAP_NET_RAW (refused rather than clamped)
                    err.write(f"ping: cannot flood; minimal interval allowed for user is {MIN_USER_INTERVAL * 1000:g}ms\n")
                    return 2
            elif opt == "-W":
                timeout = _number(value, "timeout", 0.001, 2**31 - 1, False)
            elif opt == "-w":
                deadline = _number(value, "deadline", 0, 2**31 - 1, False)
            elif opt == "-s":
                size = int(_number(value, "size", 0, 65500, True))
            elif opt == "-t":
                ttl = int(_number(value, "ttl", 1, 255, True))
            elif opt == "-q":
                quiet = True
            elif opt == "-D":
                timestamps = True
            elif opt == "-O":
                outstanding = True
            elif opt == "-6":
                err.write("ping: IPv6 is not supported on Windows\n")
                return 2
    except ValueError as e:
        err.write(f"ping: {e}\n")
        return 2
    if ":" in host:
        err.write("ping: IPv6 is not supported on Windows\n")
        return 2
    try:
        ip = resolve(host)
    except (OSError, UnicodeError):
        err.write(f"ping: {host}: Name or service not known\n")
        return 2
    try:
        echo = echo or icmp_echo()
    except OSError as e:
        err.write(f"ping: socket: {e.strerror or e}\n")
        return 2

    def line(text: str) -> None:
        if not quiet:
            out.write((f"[{wall():.6f}] " if timestamps else "") + text + "\n")

    out.write(f"PING {host} ({ip}) {size}({size + 28}) bytes of data.\n")
    sent = received = errors = 0
    rtts: list[float] = []
    start = clock()
    seq = 1
    while count is None or seq <= count:
        wait = timeout
        if deadline is not None:
            remaining = deadline - (clock() - start)
            if remaining <= 0:
                break
            wait = min(wait, remaining)
        sent_at = clock()
        try:
            reply = echo(ip, size, ttl, wait)
        except OSError as e:
            err.write(f"ping: sendmsg: {e.strerror or e}\n")
            errors += 1
            reply = None
        sent += 1
        if reply is not None and reply.status == IP_SUCCESS:
            received += 1
            rtts.append(reply.rtt_ms)
            line(f"{reply.data_size + 8} bytes from {reply.address}: icmp_seq={seq} ttl={reply.ttl} time={_rtt(reply.rtt_ms)} ms")
        elif reply is not None and reply.status in UNREACHABLE_TEXT:
            errors += 1
            line(f"From {reply.address} icmp_seq={seq} {UNREACHABLE_TEXT[reply.status]}")
        elif outstanding:
            line(f"no answer yet for icmp_seq={seq}")
        seq += 1
        if count is not None and seq > count:
            break
        pause = interval - (clock() - sent_at)
        if deadline is not None:
            pause = min(pause, deadline - (clock() - start))
        if pause > 0:
            sleep(pause)
    elapsed = int(round((clock() - start) * 1000))
    loss = (sent - received) * 100 / sent if sent else 0
    out.write(f"\n--- {host} ping statistics ---\n")
    out.write(f"{sent} packets transmitted, {received} received, " + (f"+{errors} errors, " if errors else "")
              + f"{loss:g}% packet loss, time {elapsed}ms\n")
    if rtts:
        avg = sum(rtts) / len(rtts)
        mdev = math.sqrt(max(0.0, sum(r * r for r in rtts) / len(rtts) - avg * avg))
        out.write(f"rtt min/avg/max/mdev = {min(rtts):.3f}/{avg:.3f}/{max(rtts):.3f}/{mdev:.3f} ms\n")
    if not received or (count is not None and deadline is not None and received < count):
        return 1
    return 0
