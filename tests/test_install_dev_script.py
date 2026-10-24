import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _text():
    with open(os.path.join(ROOT, "tools", "install-dev.ps1"), encoding="utf-8") as f:
        return f.read()


def test_decky_restarted_in_finally():
    t = _text()
    fin = t.index("finally")
    assert "Start-ScheduledTask" in t[fin:]
    assert "Start-ScheduledTask" not in t[:fin]
    assert "$stopped" in t[fin:]


def test_delete_is_guarded_and_sources_checked_first():
    t = _text()
    rm = t.index("Remove-Item -LiteralPath $dst")
    assert "ReparsePoint" in t[:rm]
    assert "-ine $expected" in t[:rm]
    stop = t.index("Stop-ScheduledTask")
    assert stop < rm
    assert t.index("missing source file") < stop
    assert t.index("missing source folder") < stop
    assert "traction-helper.exe" in t[:stop]
    assert "StartsWith($dst + '" + chr(92) + "'" in t
