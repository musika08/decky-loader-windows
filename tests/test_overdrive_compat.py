import json

from traction.overdrive_compat import MIN_VERSION, old_overdrive


def plugins_with(tmp_path, version=None, raw=None) -> str:
    folder = tmp_path / "plugins" / "overdrive"
    folder.mkdir(parents=True)
    if raw is not None:
        (folder / "package.json").write_text(raw, encoding="utf-8")
    else:
        (folder / "package.json").write_text(json.dumps({"name": "overdrive", "version": version}), encoding="utf-8")
    return str(tmp_path / "plugins")


def test_old_overdrive_versions(tmp_path):
    assert MIN_VERSION == (0, 2, 0)
    cases = {"0.1.0": True, "0.2.0": False, "0.10.0": False, "x": True, "0.1.9": True, "1.0.0": False,
             "0.2.1": False, "": True, None: True, 2: True, "0.2": True, "0.2.0-beta": False}
    for i, (version, old) in enumerate(cases.items()):
        assert old_overdrive(plugins_with(tmp_path / str(i), version)) is old, version


def test_no_overdrive_is_not_old(tmp_path):
    assert old_overdrive(str(tmp_path / "plugins")) is False
    (tmp_path / "plugins" / "overdrive").mkdir(parents=True)          # a folder without package.json
    assert old_overdrive(str(tmp_path / "plugins")) is False
    assert old_overdrive("") is False


def test_unreadable_package_json_counts_as_old(tmp_path):
    assert old_overdrive(plugins_with(tmp_path / "a", raw="{")) is True
    assert old_overdrive(plugins_with(tmp_path / "b", raw="[]")) is True
    assert old_overdrive(plugins_with(tmp_path / "c", raw='﻿{"version": "0.2.0"}')) is False   # BOM accepted
