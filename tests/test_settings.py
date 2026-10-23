import json

from overdrive import settings as st
from overdrive.settings import Settings

# A plausible device curve whose top points sit below G-Helper's minimum (ruling P6).
LOW_CURVE = {"temps": [30, 40, 50, 60, 70, 80, 90, 100], "speeds": [0, 0, 5, 8, 10, 11, 12, 20]}


def test_missing_file_gives_defaults(tmp_path):
    s = Settings.load(str(tmp_path / "overdrive.json"))
    assert (s.mode, s.spl, s.sppt, s.fppt, s.fps) == ("balanced", 30, 30, 30, 0)
    assert (s.fan_preset, s.fan_curve, s.default_curve) == ("default", None, None)
    assert (s.armoury_replaced, s.armoury_recorded, s.armoury_running) == (False, {}, [])
    assert s.user_set is False


def test_empty_and_garbage_files_give_defaults(tmp_path):
    for text in ("", "{not json", "[1, 2]", "null"):
        p = tmp_path / "overdrive.json"
        p.write_text(text, encoding="utf-8")
        s = Settings.load(str(p))
        assert s.to_dict() == Settings(str(p)).to_dict()


def test_settings_roundtrip(tmp_path):
    p = str(tmp_path / "sub" / "overdrive.json")
    s = Settings.load(p)
    s.mode = "manual"
    s.spl, s.sppt, s.fppt = 12, 20, 35
    s.fps = 60
    s.fan_preset = "custom"
    s.fan_curve = {"temps": [30, 40, 50, 60, 70, 80, 90, 100], "speeds": [10, 20, 30, 40, 50, 60, 70, 80]}
    s.default_curve = {"balanced": LOW_CURVE, "turbo": LOW_CURVE, "silent": LOW_CURVE}
    s.armoury_replaced = True
    s.armoury_recorded = {"ArmouryCrateSEService": "AutomaticDelayedStart", "LightingService": "Manual"}
    s.armoury_running = ["ArmouryCrateSEService"]
    s.user_set = True
    s.save()
    again = Settings.load(p)
    assert again.to_dict() == s.to_dict()
    with open(p, encoding="utf-8") as f:
        assert json.load(f)["armoury_replaced"] is True


def test_corrupt_settings_fall_back_per_field(tmp_path):
    p = tmp_path / "overdrive.json"
    p.write_text(json.dumps({
        "mode": "turbo",                       # good, kept
        "spl": 3,                              # out of range -> clamped to 5
        "sppt": "20",                          # wrong type -> default 30
        "fppt": 80,                            # out of range -> clamped to 50
        "fps": 33,                             # not an allowed step -> 0
        "fan_preset": "loud",                  # unknown -> default
        "fan_curve": {"temps": [30, 40, 50, 60, 70, 80, 90, 100], "speeds": [50, 40, 30, 20, 10, 5, 5, 5]},
        "default_curve": {"balanced": LOW_CURVE, "turbo": {"temps": [1, 2], "speeds": [3]}, "evil": LOW_CURVE},
        "armoury_replaced": "yes",             # not a bool -> False
        "armoury_recorded": {"ArmouryCrateService": "Automatic", "Evil": "Disabled", "AsusAppService": "Weird",
                             "LightingService": 4},
        "armoury_running": ["ArmouryCrateService", "AsusAppService", 7],
        "user_set": True,
        "unknown": 1,
    }), encoding="utf-8")
    s = Settings.load(str(p))
    assert s.mode == "turbo"
    assert (s.spl, s.sppt, s.fppt) == (5, 30, 50)
    assert s.fps == 0
    assert s.fan_preset == "default"
    assert s.fan_curve is None
    assert s.default_curve == {"balanced": LOW_CURVE}
    assert s.armoury_replaced is False
    assert s.armoury_recorded == {"ArmouryCrateService": "Automatic"}
    assert s.armoury_running == ["ArmouryCrateService"]   # only names with a recorded type
    assert s.user_set is True


def test_power_order_fixed_on_load(tmp_path):
    p = tmp_path / "overdrive.json"
    p.write_text(json.dumps({"spl": 30, "sppt": 20, "fppt": 25}), encoding="utf-8")
    s = Settings.load(str(p))
    assert (s.spl, s.sppt, s.fppt) == (30, 30, 30)


def test_custom_preset_without_curve_falls_back(tmp_path):
    p = tmp_path / "overdrive.json"
    p.write_text(json.dumps({"fan_preset": "custom"}), encoding="utf-8")
    assert Settings.load(str(p)).fan_preset == "default"


def test_bools_are_not_numbers(tmp_path):
    p = tmp_path / "overdrive.json"
    p.write_text(json.dumps({"spl": True, "fps": False}), encoding="utf-8")
    s = Settings.load(str(p))
    assert (s.spl, s.fps) == (30, 0)


def test_check_curve_messages_match_helper():
    ok = [30, 40, 50, 60, 70, 80, 90, 100]
    assert st.check_curve(ok, [0, 5, 10, 20, 30, 40, 50, 60]) is None
    assert st.check_curve(ok[:7], [0] * 7) == "The fan curve needs 8 points."
    assert st.check_curve([10] + ok[1:], [0] * 8) == "Point 1: temperature must be between 20 and 110 °C."
    assert st.check_curve(ok, [0, 101, 0, 0, 0, 0, 0, 0]) == "Point 2: fan speed must be between 0 and 100 %."
    assert st.check_curve([30, 30] + ok[2:], [0] * 8) == "Point 2: temperature must be higher than point 1."
    assert st.check_curve(ok, [10, 5, 10, 20, 30, 40, 50, 60]) == "Point 2: fan speed can't be lower than point 1."
    assert st.check_curve(ok, [0, 5, 10, 20, 30, 40, 50, 35]) == \
        "Point 8: fan speed can't be lower than point 7."
    assert st.check_curve(ok, [0, 5, 10, 20, 30, 30, 30, 30]) == "Point 8: at 100 °C the fan must be at least 36 %."


def test_min_speed_at_matches_ghelper():
    assert [st.min_speed_at(t) for t in (20, 70, 71, 80, 91, 100, 110)] == [0, 0, 2, 12, 26, 36, 48]


def test_presets_derive_from_default_and_pass_validation():
    temps, base = LOW_CURVE["temps"], LOW_CURVE["speeds"]
    for name, delta in (("default", 0), ("quiet", -10), ("cool", 15)):
        curve, adjusted = st.preset_curve(name, LOW_CURVE)
        assert curve["temps"] == temps, name
        assert st.check_curve(curve["temps"], curve["speeds"]) is None, name
        for t, b, s in zip(temps, base, curve["speeds"]):
            want = max(min(max(b + delta, 0), 100), st.min_speed_at(t))
            assert s >= want, (name, t)
        # never decreasing even though raising and clamping happened point by point
        assert curve["speeds"] == sorted(curve["speeds"]), name
    d, adjusted = st.preset_curve("default", LOW_CURVE)
    assert adjusted is True                       # 100 C at 20 % is below 36 %
    assert d["speeds"][:5] == [0, 0, 5, 8, 10]    # untouched where the device was already safe
    q, _ = st.preset_curve("quiet", LOW_CURVE)
    assert q["speeds"][:4] == [0, 0, 0, 0]
    c, _ = st.preset_curve("cool", LOW_CURVE)
    assert c["speeds"][:3] == [15, 15, 20]
    safe = {"temps": temps, "speeds": [10, 20, 30, 40, 50, 60, 70, 80]}
    assert st.preset_curve("default", safe) == (safe, False)


def test_preset_unusable_default_curve():
    bad = {"temps": [30, 30, 50, 60, 70, 80, 90, 100], "speeds": [0] * 8}   # temps not rising
    assert st.preset_curve("quiet", bad) == (None, False)
    assert st.preset_curve("default", None) == (None, False)
