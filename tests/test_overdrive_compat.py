import json

from traction import overdrive_compat, settings_file
from traction.overdrive_compat import MIN_VERSION, old_overdrive


def plugins_with(tmp_path, version=None, raw=None, folder_name="overdrive", name="Overdrive") -> str:
    folder = tmp_path / "plugins" / folder_name
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(json.dumps({"api_version": 1, "name": name}), encoding="utf-8")
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


def test_overdrive_holds_armoury_record(tmp_path):
    from traction.overdrive_compat import overdrive_holds_armoury_record as holds
    p = tmp_path / "overdrive.json"
    assert holds(str(p)) is False                                    # missing
    for text, want in (("{", False), ("[]", False), ("{}", False),
                       (json.dumps({"armoury_replaced": True}), True),
                       (json.dumps({"armoury_recorded": {"LightingService": "Automatic"}}), True),
                       (json.dumps({"armoury_running": ["LightingService"]}), True),
                       (json.dumps({"armoury_replaced": False, "armoury_recorded": {}, "armoury_running": []}), False),
                       (json.dumps({"armoury_replaced": 1, "armoury_recorded": [1], "armoury_running": "x"}), False)):
        p.write_text(text, encoding="utf-8")
        assert holds(str(p)) is want, text


# ---------- final review T1: Overdrive is found the way Decky finds plugins (plugin.json "name") ----------

def test_overdrive_found_by_plugin_name_not_folder(tmp_path):
    from traction.overdrive_compat import find_overdrive, overdrive_version
    plugins = plugins_with(tmp_path / "a", "0.1.0", folder_name="overdrive-main")
    assert find_overdrive(plugins) == "overdrive-main"
    assert overdrive_version(plugins) == (0, 1, 0) and old_overdrive(plugins) is True
    plugins = plugins_with(tmp_path / "b", "0.1.0", folder_name="overdrive", name="Something Else")
    assert find_overdrive(plugins) is None
    assert overdrive_version(plugins) is None and old_overdrive(plugins) is False
    # unreadable or odd plugin.json files are skipped, the real one is still found
    root = tmp_path / "c" / "plugins"
    for folder, text in (("aaa", "{"), ("bbb", "[]"), ("ccc", '{"name": 5}')):
        (root / folder).mkdir(parents=True)
        (root / folder / "plugin.json").write_text(text, encoding="utf-8")
    (root / "loose-file").write_text("x", encoding="utf-8")
    plugins_with(tmp_path / "c", "0.2.0", folder_name="zzz")
    assert find_overdrive(str(root)) == "zzz" and old_overdrive(str(root)) is False
    assert find_overdrive(str(tmp_path / "nowhere")) is None and find_overdrive("") is None


def test_installed_overdrive_without_package_json_counts_as_old(tmp_path):
    folder = tmp_path / "plugins" / "od"
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(json.dumps({"name": "Overdrive"}), encoding="utf-8")
    assert old_overdrive(str(tmp_path / "plugins")) is True


def test_overdrive_settings_found_by_scan(tmp_path):
    from traction.overdrive_compat import find_overdrive_settings
    root = tmp_path / "settings"
    (root / "traction").mkdir(parents=True)
    (root / "traction" / "settings.json").write_text("{}", encoding="utf-8")
    assert find_overdrive_settings(str(root)) == ""
    (root / "overdrive-main").mkdir()
    (root / "overdrive-main" / "overdrive.json").write_text("{}", encoding="utf-8")
    assert find_overdrive_settings(str(root)) == str(root / "overdrive-main" / "overdrive.json")
    assert find_overdrive_settings(str(tmp_path / "nowhere")) == "" and find_overdrive_settings("") == ""


# ---------- final review T4: an unreadable overdrive.json fails closed ----------

RECORD = {"armoury_replaced": True, "armoury_recorded": {"LightingService": "Automatic"}}


def test_unreadable_overdrive_json_falls_back_to_bak(tmp_path, monkeypatch):
    from traction.overdrive_compat import overdrive_holds_armoury_record as holds
    p = tmp_path / "overdrive.json"
    bak = tmp_path / "overdrive.json.bak"
    p.write_text("{", encoding="utf-8")                              # invalid JSON
    assert holds(str(p)) is False                                    # no .bak: no record (as Overdrive resets it)
    bak.write_text(json.dumps(RECORD), encoding="utf-8")
    assert holds(str(p)) is True                                     # the .bak holds one
    bak.write_text(json.dumps({"mode": "turbo"}), encoding="utf-8")
    assert holds(str(p)) is False                                    # a readable .bak without one
    bak.write_text("{", encoding="utf-8")
    assert holds(str(p)) is True                                     # both unreadable: fail closed
    # a file that cannot be opened (not "not found") is unreadable too
    p.write_text(json.dumps({"mode": "turbo"}), encoding="utf-8")
    bak.write_text(json.dumps(RECORD), encoding="utf-8")
    real = settings_file._read_json

    def denied(path):
        if path == str(p):
            raise PermissionError(13, "Access is denied", path)
        return real(path)
    monkeypatch.setattr(settings_file, "_read_json", denied)
    assert holds(str(p)) is True
    bak.write_text(json.dumps({}), encoding="utf-8")
    assert holds(str(p)) is False


def test_read_old_record_uses_bak_when_unreadable(tmp_path):
    from traction.overdrive_compat import read_old_record
    p = tmp_path / "overdrive.json"
    assert read_old_record(str(p)) == {}                              # missing: nothing
    p.write_text(json.dumps(RECORD), encoding="utf-8")
    assert read_old_record(str(p)) == RECORD
    p.write_text("{", encoding="utf-8")
    assert read_old_record(str(p)) == {}                              # unreadable, no .bak
    (tmp_path / "overdrive.json.bak").write_text(json.dumps(RECORD), encoding="utf-8")
    assert read_old_record(str(p)) == RECORD
    (tmp_path / "overdrive.json.bak").write_text("[1]", encoding="utf-8")
    assert read_old_record(str(p)) is None                            # neither readable
