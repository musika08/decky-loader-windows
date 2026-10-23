from overdrive.cpu_usage import CpuUsage


def reader(samples):
    it = iter(samples)
    return lambda: next(it)


def test_first_sample_has_no_percent_then_deltas():
    u = CpuUsage(reader([(100, 300, 100), (150, 400, 200), (250, 500, 200)]))
    assert u.sample() is None
    assert u.sample() == 75      # total 200, idle 50
    assert u.sample() == 0       # all idle


def test_failed_read_and_zero_delta_give_none_and_restart():
    u = CpuUsage(reader([(0, 10, 10), None, (10, 20, 20), (10, 20, 20), (20, 60, 20)]))
    assert u.sample() is None
    assert u.sample() is None    # read failed
    assert u.sample() is None    # no previous sample after a failure
    assert u.sample() is None    # no time passed
    assert u.sample() == 75      # total 40, idle 10


def test_reader_exception_is_none():
    def boom():
        raise OSError("no")
    assert CpuUsage(boom).sample() is None


def test_clamped_to_0_100():
    u = CpuUsage(reader([(0, 0, 0), (0, 10, 10)]))
    u.sample()
    assert u.sample() == 100
