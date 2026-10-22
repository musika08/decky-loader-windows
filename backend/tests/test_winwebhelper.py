import asyncio, sys
from typing import Any
import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only behaviour")

# (pid, parent pid, exe name). 100 is the top-level webhelper (parent: steam.exe 10), 101/102 its children.
# 200 is another user's Steam in session 2.
PROCS = [
    (10, 1, "steam.exe"),
    (101, 100, "steamwebhelper.exe"),
    (100, 10, "steamwebhelper.exe"),
    (102, 100, "SteamWebHelper.exe"),
    (200, 20, "steamwebhelper.exe"),
    (300, 1, "notepad.exe"),
]
SESSIONS = {10: 1, 100: 1, 101: 1, 102: 1, 200: 2, 20: 2, 300: 1}


def _alive(_pid: int) -> bool:
    return False


def _gone(_pid: int) -> bool:
    return True


def _restart(killed: list[int], **kw: Any) -> bool:
    from decky_loader.localplatform import winwebhelper as w
    def terminate(pid: int, session: int) -> None:
        assert session == 1
        killed.append(pid)
    args: dict[str, Any] = dict(processes=lambda: PROCS, session_of=SESSIONS.get, own_session=lambda: 1,
                                terminate=terminate, wait_exit=_alive)
    args.update(kw)
    return w.restart_webhelper(**args)


def test_ends_only_this_sessions_webhelpers_top_level_first():
    killed: list[int] = []
    assert _restart(killed) is True
    assert killed[0] == 100
    assert sorted(killed) == [100, 101, 102]  # never 200 (other session), steam.exe or anything else


def test_unknown_own_session_touches_nothing():
    killed: list[int] = []
    assert _restart(killed, own_session=lambda: None) is False
    assert killed == []


def test_process_with_unreadable_session_is_skipped():
    killed: list[int] = []
    sessions = dict(SESSIONS)
    del sessions[101]
    assert _restart(killed, session_of=sessions.get) is True
    assert sorted(killed) == [100, 102]


def test_nothing_running_returns_false():
    killed: list[int] = []
    assert _restart(killed, processes=lambda: [(300, 1, "notepad.exe")]) is False
    assert killed == []


def test_listing_failure_is_logged_not_raised():
    def boom() -> list[tuple[int, int, str]]:
        raise OSError("snapshot failed")
    killed: list[int] = []
    assert _restart(killed, processes=boom) is False
    assert killed == []


def test_children_already_gone_count_as_stopped():
    def terminate(pid: int, session: int) -> None:
        if pid != 100:
            raise OSError("gone with its parent")
    assert _restart([], terminate=terminate, wait_exit=_gone) is True


def test_a_survivor_is_reported():
    def terminate(pid: int, session: int) -> None:
        if pid == 102:
            raise PermissionError("access denied")
    assert _restart([], terminate=terminate, wait_exit=_alive) is False


def test_terminate_refuses_a_pid_that_is_not_a_webhelper():
    import os
    from decky_loader.localplatform import winwebhelper as w
    # Our own process is not steamwebhelper.exe, so it must be refused before anything is terminated
    with pytest.raises(OSError, match="no longer"):
        w.terminate_webhelper(os.getpid(), w.session_of(os.getpid()) or 0)


def test_terminate_rechecks_the_session_on_the_held_handle():
    import os
    from decky_loader.localplatform import winwebhelper as w
    checked: list[int] = []
    def other_session(pid: int) -> int | None:
        checked.append(pid)
        return 7
    # Refused before the image check or anything is terminated
    with pytest.raises(OSError, match="not in session 1"):
        w.terminate_webhelper(os.getpid(), 1, session_of=other_session)
    assert checked == [os.getpid()]


def test_real_snapshot_and_session_lookup_work():
    import os
    from decky_loader.localplatform import winwebhelper as w
    procs = w.process_names()
    assert any(pid == os.getpid() for pid, _parent, _name in procs)
    assert w.own_session() == w.session_of(os.getpid()) is not None


def test_platform_restart_runs_off_the_loop(monkeypatch: pytest.MonkeyPatch):
    import threading
    from decky_loader.localplatform import localplatformwin, winwebhelper
    seen: list[bool] = []
    main_thread = threading.get_ident()

    def fake() -> bool:
        seen.append(threading.get_ident() != main_thread)
        return True
    monkeypatch.setattr(winwebhelper, "restart_webhelper", fake)
    assert asyncio.run(localplatformwin.restart_webhelper()) is True
    assert seen == [True]
