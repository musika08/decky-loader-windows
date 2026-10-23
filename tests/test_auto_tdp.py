from overdrive.auto_tdp import PERIOD, AutoTdp


class Feeder:
    def __init__(self, ctl, now=1000.0):
        self.ctl = ctl
        self.now = now

    def feed(self, fps, power=None):
        self.now += PERIOD
        return self.ctl.step(fps, power, self.now)


def make(ceiling=17, limit=60):
    ctl = AutoTdp(ceiling, limit, 1000.0)
    return ctl, Feeder(ctl)


def trim_to(f, ctl, watts):
    """Reach `watts` in hold mode by on-target samples with headroom."""
    while ctl.watts > watts:
        f.feed(59, 1)


def test_hold_steps_up_two_at_once():
    ctl, f = make()
    trim_to(f, ctl, 11)
    assert ctl.watts == 11
    assert f.feed(53, 5) == 13
    assert ctl.watts == 13


def test_hold_steps_down_after_eight_then_each():
    ctl, f = make()
    for _ in range(7):
        assert f.feed(59, 5) is None
    assert f.feed(59, 5) == 16
    assert f.feed(59, 5) == 15


def test_hold_no_step_down_without_headroom():
    ctl, f = make()
    for _ in range(30):
        assert f.feed(59, 17) is None
    for _ in range(30):
        assert f.feed(59, None) is None
    assert ctl.watts == 17


def test_hold_clamps():
    ctl, f = make()
    for _ in range(200):
        f.feed(60, 0)
    assert ctl.watts == 6
    for _ in range(20):
        f.feed(10, 0)
    assert ctl.watts == 17
    assert f.feed(10, 0) is None


def test_hold_zero_fps_climbs_to_ceiling():
    ctl, f = make()
    for _ in range(200):
        f.feed(60, 0)
    assert ctl.watts == 6
    got = [f.feed(0, None) for _ in range(8)]
    assert got == [8, 10, 12, 14, 16, 17, None, None]


def test_most_frames_measures_median_then_trims():
    ctl, f = make(limit=0)
    assert ctl.phase == "measure" and ctl.target is None
    for fps in [70, 72, 74, 71, 73, 75, 72, 74, 73]:
        assert f.feed(fps, 5) is None
        assert ctl.phase == "measure" and ctl.target is None
    assert f.feed(72, 5) is None
    assert ctl.target == 72.5 and ctl.phase == "trim"
    for _ in range(7):
        assert f.feed(72, 5) is None
    assert f.feed(72, 5) == 16
    assert f.feed(72, 5) == 15
    assert ctl.phase == "trim"


def _measured(ctl, f, fps=72):
    for _ in range(10):
        f.feed(fps, 5)
    assert ctl.phase == "trim"


def test_most_frames_rechecks_after_60s():
    ctl, f = make(limit=0)
    _measured(ctl, f)
    for _ in range(10):
        f.feed(72, 5)
    assert ctl.watts < 17
    f.now += 60
    assert f.feed(72, 5) == 17
    assert ctl.phase == "measure" and ctl.target is None
    assert f.feed(72, 5) is None
    assert ctl.watts == 17


def test_most_frames_rechecks_when_back_at_ceiling():
    ctl, f = make(limit=0)
    _measured(ctl, f)
    for _ in range(10):
        f.feed(72, 5)
    low = ctl.watts
    assert low < 17
    got = None
    for _ in range(10):
        got = f.feed(30, 5)
        if ctl.phase == "measure":
            break
    assert ctl.phase == "measure"
    assert got == 17 and ctl.watts == 17 and ctl.target is None


def test_most_frames_no_game_stays_at_ceiling():
    ctl, f = make(limit=0)
    for _ in range(10):
        assert f.feed(0, 5) is None
    assert ctl.phase == "trim" and ctl.target is None and ctl.watts == 17
    for _ in range(50):
        assert f.feed(0, 5) is None
    assert ctl.watts == 17
    f.now += 60
    assert f.feed(0, 5) is None
    assert ctl.phase == "measure"


def test_reset_starts_at_new_ceiling():
    ctl, f = make()
    for _ in range(5):
        f.feed(59, 5)
    ctl.reset(13, 60, 2000.0)
    assert ctl.watts == 13 and ctl.up_count == 0 and ctl.down_count == 0 and ctl.phase == "hold"
    ctl.reset(13, 0, 2000.0)
    assert ctl.phase == "measure" and ctl.target is None and ctl.watts == 13
