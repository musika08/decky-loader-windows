r"""Linux command-line tools for plugins on Windows: the entry point of `<decky> --compat-tool <name> <host pid> args...`.

winplugincompat rewrites a plugin's list-form call of one of NAMES (`["ps", "--ppid", "1"]`, `/usr/bin/timedatectl`)
to that command, so the tool is Decky's own code, run by Decky's own interpreter with the plugin host's token, and
never looked up on PATH. Output is UTF-8 with "\n" line ends, as on Linux.

- ps, kill, pgrep, pkill, killall, pidof: winproctools
- ping (only calls in iputils syntax; others keep Windows' ping.exe): winping
- timedatectl, date: wintimedate
- sudo, su, runuser (and doas, pkexec, always): only reached when the wrapper can't simply be dropped (another user, or root from a plugin
  without administrator rights); they fail with an explanation instead of switching users or elevating.
"""
import io
import sys
from typing import TextIO

PROCESS_TOOLS = ("ps", "kill", "pgrep", "pkill", "killall", "pidof")
TIME_TOOLS = ("timedatectl", "date")
USER_SWITCH = ("sudo", "su", "runuser", "doas", "pkexec")
NAMES = frozenset(PROCESS_TOOLS + TIME_TOOLS + USER_SWITCH + ("ping",))


def _stream(stream: TextIO | None) -> TextIO:
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        # pythonw without a handle for it: nowhere to write
        return io.StringIO()
    return io.TextIOWrapper(buffer, encoding="utf-8", errors="replace", newline="\n", write_through=True)


def run(name: str, host_pid: int, args: list[str], out: TextIO, err: TextIO) -> int:
    if name in PROCESS_TOOLS:
        from .winproctools import TOOLS, real_context
        return TOOLS[name](args, real_context(host_pid), out, err)
    if name == "ping":
        from .winping import run as ping
        return ping(args, out, err)
    if name in TIME_TOOLS:
        from .wintimedate import run as timedate
        return timedate(name, args, out, err)
    if name in USER_SWITCH:
        err.write(f"{name}: running a command as another user is not possible on Windows. Decky only runs it "
                  "directly when it would run as the plugin's own user (or as root in a plugin with the root flag).\n")
        return 1
    err.write(f"{name}: not available on Windows\n")
    return 127


def main(name: str, host_pid: str, args: list[str]) -> int:
    out, err = _stream(sys.stdout), _stream(sys.stderr)
    try:
        return run(name, int(host_pid), args, out, err)
    except Exception as e:
        err.write(f"{name}: {e}\n")
        return 1
    finally:
        for stream in (out, err):
            try:
                stream.flush()
                if isinstance(stream, io.TextIOWrapper):
                    stream.detach() # sys.stdout still owns the buffer
            except Exception:
                pass
