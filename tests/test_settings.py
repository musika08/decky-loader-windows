import datetime
import json
import os

import pytest

from overdrive import settings as st
from overdrive import settings_file
from overdrive.settings import Settings

# A plausible device curve whose top points sit below G-Helper's minimum (ruling P6).
LOW_CURVE = {"temps": [30, 40, 50, 60, 70, 80, 90, 100], "speeds": [0, 0, 5, 8, 10, 11, 12, 20]}
GPU_LOW = {"temps": [48, 54, 59, 62, 62, 62, 62, 62], "speeds": [1, 9, 18, 22, 22, 22, 22, 22]}


def test_missing_file_gives_defaults(tmp_path):
    s = Settings.load(str(tmp_path / "overdrive.json"))
    assert (s.mode, s.spl, s.sppt, s.fppt, s.fps) == ("balanced", 30, 30, 30, 0)
    assert (s.fan_preset, s.fan_curve, s.default_curve) == ("default", None, None)
    assert (s.fan_curve_gpu, s.default_curve_gpu) == (None, None)
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
    s.fan_curve_gpu = {"temps": [35, 45, 55, 65, 75, 85, 95, 105], "speeds": [5, 15, 25, 35, 45, 55, 65, 75]}
    s.default_curve_gpu = {"balanced": GPU_LOW, "turbo": GPU_LOW, "silent": GPU_LOW}
    s.armoury_replaced = True
    s.armoury_recorded = {"ArmouryCrateSEService": "AutomaticDelayedStart", "LightingService": "Manual"}
    s.armoury_running = ["ArmouryCrateSEService"]
    s.user_set = True
    s.save()
    again = Settings.load(p)
    assert again.to_dict() == s.to_dict()
    with open(p, encoding="utf-8") as f:
        on_disk = json.load(f)
    # the Armoury Crate record is in the record shared with Traction now: a fresh file never holds it
    assert not any(k.startswith("armoury_") for k in on_disk)
    assert on_disk["fan_curve_gpu"] == s.fan_curve_gpu and on_disk["default_curve_gpu"]["turbo"] == GPU_LOW
    with open(p + ".bak", encoding="utf-8") as f:
        assert json.load(f) == on_disk   # the .bak carries the GPU fields too


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


def test_auto_tdp_round_trip(tmp_path):
    p = str(tmp_path / "overdrive.json")
    s = Settings.load(p)
    assert s.auto_tdp is False
    s.auto_tdp = True
    s.save()
    assert Settings.load(p).auto_tdp is True
    with open(p, encoding="utf-8") as f:
        assert json.load(f)["auto_tdp"] is True


def test_auto_tdp_bad_value_is_false(tmp_path):
    p = tmp_path / "overdrive.json"
    for bad in ("yes", 1, None, [True]):
        p.write_text(json.dumps({"auto_tdp": bad}), encoding="utf-8")
        assert Settings.load(str(p)).auto_tdp is False


def test_auto_tdp_missing_is_false(tmp_path):
    p = tmp_path / "overdrive.json"
    p.write_text(json.dumps({"mode": "turbo", "user_set": True}), encoding="utf-8")
    s = Settings.load(str(p))
    assert s.auto_tdp is False and s.mode == "turbo"


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
    assert st.check_curve([30, 25] + ok[2:], [0] * 8) == "Point 2: temperature can't be lower than point 1."
    assert st.check_curve([30, 30] + ok[2:], [0, 0, 10, 20, 30, 40, 50, 60]) is None   # equal temps, equal speeds
    assert st.check_curve([30, 30] + ok[2:], [0, 5, 10, 20, 30, 40, 50, 60]) == \
        "Point 2: same temperature as point 1, so the fan speed must be the same."
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


# The Ally's own curves (read-only probe on the device, 2026-10-23): they repeat their last temperature.
ALLY_CURVES = {
    "balanced": {"temps": [48, 54, 59, 62, 62, 62, 62, 62], "speeds": [1, 9, 18, 22, 22, 22, 22, 22]},
    "turbo": {"temps": [48, 55, 58, 62, 66, 73, 73, 94], "speeds": [9, 26, 39, 48, 56, 65, 65, 78]},
    "silent": {"temps": [47, 49, 54, 54, 54, 54, 54, 54], "speeds": [1, 9, 13, 13, 13, 13, 13, 13]},
}


def test_ally_firmware_curves_and_their_presets_pass():
    for mode, c in ALLY_CURVES.items():
        assert st.check_curve(c["temps"], c["speeds"]) is None, mode
        for name in ("default", "quiet", "cool"):
            curve, adjusted = st.preset_curve(name, c)
            assert curve is not None, (mode, name)
            assert st.check_curve(curve["temps"], curve["speeds"]) is None, (mode, name)
            assert adjusted is False, (mode, name)          # all well above G-Helper's minimum
    assert st.preset_curve("default", ALLY_CURVES["turbo"])[0] == ALLY_CURVES["turbo"]
    assert st.preset_curve("quiet", ALLY_CURVES["turbo"])[0]["speeds"] == [0, 16, 29, 38, 46, 55, 55, 68]
    assert st.preset_curve("cool", ALLY_CURVES["turbo"])[0]["speeds"] == [24, 41, 54, 63, 71, 80, 80, 93]


def test_preset_unusable_default_curve():
    bad = {"temps": [40, 30, 50, 60, 70, 80, 90, 100], "speeds": [0] * 8}   # temps falling
    assert st.preset_curve("quiet", bad) == (None, False)
    assert st.preset_curve("default", None) == (None, False)


# ---------- live finding: a BOM-prefixed file was reset to defaults and the Armoury record lost ----------

RECORD = {"ArmouryCrateSEService": "AutomaticDelayedStart", "LightingService": "Automatic"}
NOW = datetime.datetime(2026, 10, 23, 8, 30, 5, tzinfo=datetime.timezone.utc)


def _full_settings():
    return {"mode": "manual", "spl": 12, "sppt": 20, "fppt": 35, "fps": 45, "fan_preset": "quiet",
            "fan_curve": None, "default_curve": None,
            "fan_curve_gpu": {"temps": [35, 45, 55, 65, 75, 85, 95, 105], "speeds": [5, 15, 25, 35, 45, 55, 65, 75]},
            "default_curve_gpu": {"balanced": dict(GPU_LOW)}, "armoury_replaced": True,
            "armoury_recorded": dict(RECORD), "armoury_running": ["ArmouryCrateSEService"], "auto_tdp": True,
            "controller_mode": "auto", "grip_vibration": False, "cpu_boost_on_value": 4, "user_set": True}


def test_bom_prefixed_file_loads_every_field(tmp_path):
    p = tmp_path / "overdrive.json"
    # what Windows PowerShell 5.1 `Set-Content -Encoding UTF8` writes
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps(_full_settings(), indent=2).encode("utf-8"))
    s = Settings.load(str(p))
    assert s.to_dict() == _full_settings()
    assert s.armoury_recorded == RECORD and s.armoury_running == ["ArmouryCrateSEService"]
    assert s.warning == ""
    assert sorted(x.name for x in tmp_path.iterdir()) == ["overdrive.json"]   # readable: no copy kept
    s.save()
    assert not p.read_bytes().startswith(b"\xef\xbb\xbf")
    assert not (tmp_path / "overdrive.json.bak").read_bytes().startswith(b"\xef\xbb\xbf")


def test_save_is_atomic_and_writes_bak(tmp_path, monkeypatch):
    p = tmp_path / "overdrive.json"
    s = Settings.load(str(p))
    s.mode, s.armoury_recorded = "turbo", dict(RECORD)
    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(st.os, "replace", lambda a, b: (replaced.append((a, b)), real_replace(a, b)))
    s.save()
    assert replaced == [(str(p) + ".new", str(p)), (str(p) + ".bak.new", str(p) + ".bak")]
    assert json.loads(p.read_text(encoding="utf-8")) == json.loads((tmp_path / "overdrive.json.bak").read_text())
    assert sorted(x.name for x in tmp_path.iterdir()) == ["overdrive.json", "overdrive.json.bak"]


def test_bak_failure_does_not_fail_the_save(tmp_path, monkeypatch):
    p = tmp_path / "overdrive.json"
    s = Settings.load(str(p))
    real = settings_file._write_atomic

    def no_bak(path, text):
        if path.endswith(".bak"):
            raise OSError("disk full")
        real(path, text)
    monkeypatch.setattr(settings_file, "_write_atomic", no_bak)
    s.mode = "silent"
    s.save()
    assert Settings.load(str(p)).mode == "silent"


def test_unreadable_file_is_copied_before_it_is_overwritten(tmp_path):
    p = tmp_path / "overdrive.json"
    garbage = b'{"mode": "turbo", "armoury_recorded": {'          # truncated
    p.write_bytes(garbage)
    s = Settings.load(str(p), now=NOW)
    copy = tmp_path / "overdrive.json.unreadable-20261023T083005Z"
    assert copy.read_bytes() == garbage
    assert p.read_bytes() == garbage                               # load itself never rewrites the file
    assert s.to_dict() == Settings(str(p)).to_dict()
    assert s.warning == ("Overdrive settings could not be read and were reset; a copy was kept as "
                         "overdrive.json.unreadable-20261023T083005Z.")
    s.save()
    assert copy.read_bytes() == garbage
    assert json.loads(p.read_text(encoding="utf-8"))["mode"] == "balanced"
    # a second unreadable load in the same second never overwrites the first copy
    p.write_bytes(b"\x00\x01")
    Settings.load(str(p), now=NOW)
    assert copy.read_bytes() == garbage
    assert (tmp_path / "overdrive.json.unreadable-20261023T083005Z-1").read_bytes() == b"\x00\x01"


def test_unreadable_file_is_not_overwritten_when_no_copy_can_be_kept(tmp_path, monkeypatch):
    p = tmp_path / "overdrive.json"
    p.write_bytes(b"{nope")

    def fail(path, log, now=None, owner="Overdrive"):
        raise OSError("access denied")
    monkeypatch.setattr(settings_file, "keep_unreadable", fail)
    s = Settings.load(str(p))
    assert "a copy could not be kept yet" in s.warning
    with pytest.raises(OSError):
        s.save()
    assert p.read_bytes() == b"{nope"


def test_unreadable_link_is_not_copied(tmp_path, monkeypatch):
    p = tmp_path / "overdrive.json"
    p.write_bytes(b"{nope")
    monkeypatch.setattr(settings_file, "_is_link", lambda path: True)
    s = Settings.load(str(p), now=NOW)
    assert sorted(x.name for x in tmp_path.iterdir()) == ["overdrive.json"]
    assert s.warning == ("Overdrive settings could not be read and were reset; overdrive.json is a link, so no copy "
                         "was kept.")


def test_armoury_record_recovered_from_bak(tmp_path):
    p = tmp_path / "overdrive.json"
    # an Overdrive 0.1 file, the record not moved to the shared one yet (switch off: provisional)
    p.write_text(json.dumps({"armoury_recorded": dict(RECORD), "armoury_running": ["ArmouryCrateSEService"]}),
                 encoding="utf-8")
    s = Settings.load(str(p))
    s.mode, s.fps = "turbo", 60
    s.save()
    p.write_bytes(b"\xff\xfe garbage")
    again = Settings.load(str(p), now=NOW)
    assert again.armoury_recorded == RECORD
    assert again.armoury_running == ["ArmouryCrateSEService"]
    assert again.armoury_replaced is False
    assert (again.mode, again.fps) == ("balanced", 0)                   # other fields stay at their defaults
    assert again.warning.endswith("The Armoury Crate record was recovered from overdrive.json.bak.")
    again.save()
    assert Settings.load(str(p)).armoury_recorded == RECORD                # the next save keeps the record


def test_armoury_record_recovered_from_newest_readable_copy(tmp_path):
    p = tmp_path / "overdrive.json"
    old = tmp_path / "overdrive.json.bak"
    old.write_text(json.dumps({"armoury_replaced": False, "armoury_recorded": {}}), encoding="utf-8")
    os.utime(old, (1_000_000, 1_000_000))
    # an older build's copy of a BOM file: readable now, and newer than the .bak
    bom = tmp_path / "overdrive.json.unreadable-20261001T000000Z"
    bom.write_bytes(b"\xef\xbb\xbf" + json.dumps(_full_settings()).encode("utf-8"))
    os.utime(bom, (2_000_000, 2_000_000))
    p.write_bytes(b"")
    s = Settings.load(str(p), now=NOW)
    assert (s.armoury_replaced, s.armoury_recorded, s.armoury_running) == \
        (True, RECORD, ["ArmouryCrateSEService"])
    assert s.mode == "balanced"
    assert "recovered from overdrive.json.unreadable-20261001T000000Z" in s.warning


# ---------- GPU fan curve ----------

def test_gpu_fields_fall_back_per_field(tmp_path):
    p = tmp_path / "overdrive.json"
    p.write_text(json.dumps({
        "fan_curve": {"temps": [30, 40, 50, 60, 70, 80, 90, 100], "speeds": [10, 20, 30, 40, 50, 60, 70, 80]},
        "fan_curve_gpu": {"temps": [30, 40, 50, 60, 70, 80, 90, 100], "speeds": [50, 40, 30, 20, 10, 5, 5, 5]},
        "default_curve_gpu": {"balanced": GPU_LOW, "turbo": {"temps": [1, 2], "speeds": [3]}, "evil": GPU_LOW},
        "fan_preset": "custom",
    }), encoding="utf-8")
    s = Settings.load(str(p))
    assert s.fan_curve_gpu is None                          # decreasing speeds: invalid, dropped
    assert s.default_curve_gpu == {"balanced": GPU_LOW}      # bad and unknown modes dropped, the rest kept
    assert s.fan_curve is not None and s.fan_preset == "custom"


def test_gpu_default_curve_not_a_dict_is_ignored(tmp_path):
    p = tmp_path / "overdrive.json"
    p.write_text(json.dumps({"default_curve_gpu": [1, 2, 3], "fan_curve_gpu": "loud"}), encoding="utf-8")
    s = Settings.load(str(p))
    assert (s.default_curve_gpu, s.fan_curve_gpu) == (None, None)


def test_custom_preset_kept_with_only_a_gpu_curve(tmp_path):
    p = tmp_path / "overdrive.json"
    gpu = {"temps": [35, 45, 55, 65, 75, 85, 95, 105], "speeds": [5, 15, 25, 35, 45, 55, 65, 75]}
    p.write_text(json.dumps({"fan_preset": "custom", "fan_curve_gpu": gpu}), encoding="utf-8")
    s = Settings.load(str(p))
    assert (s.fan_preset, s.fan_curve, s.fan_curve_gpu) == ("custom", None, gpu)
    assert s.has_custom() and s.custom_curve("gpu") == gpu and s.custom_curve("cpu") is None


def test_unreadable_file_keeps_gpu_defaults_out_of_recovery(tmp_path):
    """Recovery only takes the Armoury record from the .bak; the GPU fields start fresh like the CPU ones."""
    p = tmp_path / "overdrive.json"
    p.write_text(json.dumps({"armoury_recorded": dict(RECORD)}), encoding="utf-8")   # not moved yet
    s = Settings.load(str(p))
    s.fan_curve_gpu = {"temps": [35, 45, 55, 65, 75, 85, 95, 105], "speeds": [5, 15, 25, 35, 45, 55, 65, 75]}
    s.default_curve_gpu = {"balanced": GPU_LOW}
    s.save()
    with open(str(p) + ".bak", encoding="utf-8") as f:
        assert json.load(f)["default_curve_gpu"] == {"balanced": GPU_LOW}
    p.write_text("{broken", encoding="utf-8")
    again = Settings.load(str(p), now=NOW)
    assert again.armoury_recorded == RECORD
    assert (again.fan_curve_gpu, again.default_curve_gpu) == (None, None)   # read from the device again


def test_gpu_presets_derive_from_the_gpu_default():
    from overdrive.reapply import fan_note, fan_plan
    s = Settings("")
    s.default_curve = {m: dict(LOW_CURVE) for m in st.CURVE_MODES}
    s.default_curve_gpu = {m: dict(GPU_LOW) for m in st.CURVE_MODES}
    s.fan_preset = "cool"
    cpu, _, _ = fan_plan(s, "cpu")
    gpu, _, _ = fan_plan(s, "gpu")
    assert cpu == st.preset_curve("cool", LOW_CURVE)[0]
    assert gpu == st.preset_curve("cool", GPU_LOW)[0]
    assert gpu["temps"] == GPU_LOW["temps"] and gpu["speeds"][0] == 16
    s.fan_preset = "default"
    assert fan_plan(s, "gpu") == (None, "", "")          # the firmware curve already passes: nothing to write
    assert fan_note(s) == "Adjusted to the safe minimum"   # only the CPU's LOW curve was raised
    s.default_curve = {m: dict(GPU_LOW) for m in st.CURVE_MODES}
    assert fan_note(s) == ""


# ---------- Recommended preset (owner-approved fixed curve, 2026-10-23) ----------

RECOMMENDED = {"temps": [40, 50, 60, 65, 70, 75, 80, 90], "speeds": [10, 15, 25, 35, 45, 55, 70, 90]}


def test_recommended_curve_is_fixed_and_valid():
    assert st.RECOMMENDED_CURVE == RECOMMENDED
    assert st.check_curve(RECOMMENDED["temps"], RECOMMENDED["speeds"]) is None
    # G-Helper's minimum ceil((t - 70) * 1.2): 75 -> 6, 80 -> 12, 90 -> 24, all met
    assert [st.min_speed_at(t) for t in (75, 80, 90)] == [6, 12, 24]
    for t, s in zip(RECOMMENDED["temps"], RECOMMENDED["speeds"]):
        assert s >= st.min_speed_at(t)
    # The same curve whatever the device curve, also with none, and never "adjusted"
    for base in (None, LOW_CURVE, GPU_LOW):
        assert st.preset_curve("recommended", base) == (RECOMMENDED, False)
    curve, _ = st.preset_curve("recommended", None)
    curve["speeds"][0] = 99
    assert st.RECOMMENDED_CURVE == RECOMMENDED            # callers get a copy


def test_settings_accept_recommended_preset(tmp_path):
    assert st.PRESETS == ("default", "recommended", "quiet", "cool", "custom")
    p = tmp_path / "overdrive.json"
    p.write_text(json.dumps({"fan_preset": "recommended"}), encoding="utf-8")
    s = Settings.load(str(p))
    assert s.fan_preset == "recommended"
    s.save()
    assert Settings.load(str(p)).fan_preset == "recommended"


def test_recommended_plan_for_both_fans_needs_no_device_curve(tmp_path):
    from overdrive.reapply import fan_note, fan_plan
    s = Settings.load(str(tmp_path / "overdrive.json"))
    s.fan_preset = "recommended"
    for fan in st.FANS:
        assert fan_plan(s, fan) == (RECOMMENDED, "", "")
    s.default_curve = {m: LOW_CURVE for m in st.CURVE_MODES}
    s.default_curve_gpu = {m: GPU_LOW for m in st.CURVE_MODES}
    for fan in st.FANS:
        assert fan_plan(s, fan) == (RECOMMENDED, "", "")
    assert fan_note(s) == ""


def test_settings_new_fields_defaults_and_bad_values(tmp_path):
    p = tmp_path / "overdrive.json"
    s = Settings.load(str(p))
    assert (s.controller_mode, s.grip_vibration, s.cpu_boost_on_value) == ("gamepad", True, 2)
    for mode in ("gamepad", "desktop", "auto"):
        p.write_text(json.dumps({"controller_mode": mode}), encoding="utf-8")
        assert Settings.load(str(p)).controller_mode == mode
    for bad in ("Desktop", "mouse", 1, None, ["auto"]):
        p.write_text(json.dumps({"controller_mode": bad}), encoding="utf-8")
        assert Settings.load(str(p)).controller_mode == "gamepad"
    for bad in ("no", 0, 1, None):
        p.write_text(json.dumps({"grip_vibration": bad}), encoding="utf-8")
        assert Settings.load(str(p)).grip_vibration is True
    p.write_text(json.dumps({"grip_vibration": False}), encoding="utf-8")
    assert Settings.load(str(p)).grip_vibration is False
    for good in range(1, 7):
        p.write_text(json.dumps({"cpu_boost_on_value": good}), encoding="utf-8")
        assert Settings.load(str(p)).cpu_boost_on_value == good
    for bad in (0, 7, -1, "3", 3.0, True, None):
        p.write_text(json.dumps({"cpu_boost_on_value": bad}), encoding="utf-8")
        assert Settings.load(str(p)).cpu_boost_on_value == 2
    s.controller_mode, s.grip_vibration, s.cpu_boost_on_value = "auto", False, 5
    s.save()
    again = Settings.load(str(p))
    assert (again.controller_mode, again.grip_vibration, again.cpu_boost_on_value) == ("auto", False, 5)


# ---------- the move to the shared Armoury Crate record ----------

def test_old_armoury_fields_are_kept_until_moved(tmp_path):
    p = tmp_path / "overdrive.json"
    p.write_text(json.dumps({"mode": "turbo", "armoury_replaced": True, "armoury_recorded": dict(RECORD),
                             "armoury_running": ["ArmouryCrateSEService"]}), encoding="utf-8")
    s = Settings.load(str(p))
    assert s.holds_armoury()
    s.fps = 60
    s.save()                                    # not moved yet: written back unchanged
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert (on_disk["armoury_replaced"], on_disk["armoury_recorded"]) == (True, RECORD)
    s.armoury_migrated()
    s.save()
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert not any(k.startswith("armoury_") for k in on_disk) and on_disk["fps"] == 60
    again = Settings.load(str(p))
    assert not again.holds_armoury() and again.to_dict() == s.to_dict()


def test_empty_old_armoury_fields_are_dropped(tmp_path):
    p = tmp_path / "overdrive.json"
    p.write_text(json.dumps({"armoury_replaced": False, "armoury_recorded": {}, "armoury_running": []}),
                 encoding="utf-8")
    s = Settings.load(str(p))
    assert not s.holds_armoury()
    s.save()
    assert not any(k.startswith("armoury_") for k in json.loads(p.read_text(encoding="utf-8")))

