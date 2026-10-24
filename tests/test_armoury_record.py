import json
import os

import pytest

from overdrive.armoury_record import (ARMOURY_SERVICES, RECORD_DIR, START_TYPES, ArmouryLocked, ArmouryRecord)
from overdrive import settings


def test_record_dir_and_shared_lists():
    assert RECORD_DIR == r"C:\ProgramData\decky\armoury"
    # Overdrive's settings take the list from the shared module (one place for both plugins)
    assert settings.ARMOURY_SERVICES is ARMOURY_SERVICES and settings.START_TYPES is START_TYPES


def test_record_roundtrip_and_validation(tmp_path):
    folder = str(tmp_path / "armoury")
    r = ArmouryRecord(folder)
    assert not r.exists()
    assert r.load() == "" and (r.replaced, r.recorded, r.running) == (False, {}, [])
    r.replaced = True
    r.recorded = {"ArmouryCrateService": "Automatic", "Bogus": "Automatic"}
    r.running = ["ArmouryCrateService"]
    r.save()
    assert r.exists()
    on_disk = json.loads((tmp_path / "armoury" / "state.json").read_text(encoding="utf-8"))
    assert set(on_disk) == {"replaced", "recorded", "running"}
    assert json.loads((tmp_path / "armoury" / "state.json.bak").read_text(encoding="utf-8")) == on_disk
    again = ArmouryRecord(folder)
    assert again.load() == ""
    assert again.replaced is True
    assert again.recorded == {"ArmouryCrateService": "Automatic"}
    assert again.running == ["ArmouryCrateService"]


def test_record_drops_bad_values(tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({
        "replaced": "yes", "recorded": {"ArmouryCrateService": "Automatic", "AsusAppService": "Weird", "Evil": "Manual"},
        "running": ["ArmouryCrateService", "AsusAppService", 7, "ArmouryCrateService"]}), encoding="utf-8")
    r = ArmouryRecord(str(tmp_path))
    r.load()
    assert (r.replaced, r.recorded, r.running) == (False, {"ArmouryCrateService": "Automatic"},
                                                     ["ArmouryCrateService"])


def test_record_reads_bom(tmp_path):
    (tmp_path / "state.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"replaced": True}).encode())
    r = ArmouryRecord(str(tmp_path))
    assert r.load() == "" and r.replaced is True


def test_record_recovers_from_bak(tmp_path):
    folder = str(tmp_path)
    r = ArmouryRecord(folder)
    r.recorded, r.running = {"LightingService": "Automatic"}, ["LightingService"]   # provisional
    r.save()
    (tmp_path / "state.json").write_bytes(b"{ broken")
    again = ArmouryRecord(folder)
    note = again.load()
    assert note == "The Armoury Crate record was recovered from state.json.bak."
    assert again.note == note
    assert (again.replaced, again.recorded, again.running) == (False, {"LightingService": "Automatic"},
                                                                 ["LightingService"])
    # the next save keeps a copy of the unreadable file first
    again.save()
    copies = [x.name for x in tmp_path.iterdir() if x.name.startswith("state.json.unreadable-")]
    assert len(copies) == 1 and (tmp_path / copies[0]).read_bytes() == b"{ broken"
    assert ArmouryRecord(folder).load() == ""


def test_record_unreadable_without_copy_says_so(tmp_path):
    (tmp_path / "state.json").write_bytes(b"[1, 2]")   # JSON, but not an object
    r = ArmouryRecord(str(tmp_path))
    assert r.load() == "The Armoury Crate record could not be read and no readable copy was found."
    assert (r.replaced, r.recorded) == (False, {})


def test_unreadable_record_not_overwritten_when_no_copy_can_be_kept(tmp_path, monkeypatch):
    from overdrive import settings_file
    (tmp_path / "state.json").write_bytes(b"{ broken")
    r = ArmouryRecord(str(tmp_path))
    r.load()

    def no_copy(*a, **k):
        raise OSError("no space")
    monkeypatch.setattr(settings_file, "keep_unreadable", no_copy)
    r.replaced = True
    with pytest.raises(OSError):
        r.save()
    assert (tmp_path / "state.json").read_bytes() == b"{ broken"


def test_refresh_reads_only_when_the_file_changed(tmp_path):
    folder = str(tmp_path)
    reader, writer = ArmouryRecord(folder), ArmouryRecord(folder)
    assert reader.refresh() is True          # first look always loads
    assert reader.refresh() is False         # nothing there, nothing changed
    writer.replaced = True
    writer.save()
    assert reader.refresh() is True and reader.replaced is True
    assert reader.refresh() is False
    writer.replaced = False
    writer.save()
    assert reader.refresh() is True and reader.replaced is False


def test_save_retries_while_another_process_reads(tmp_path, monkeypatch):
    from overdrive import settings_file
    r = ArmouryRecord(str(tmp_path))
    real, tries = settings_file.save_json, []

    def busy_then_ok(*a, **k):
        tries.append(1)
        if len(tries) < 3:
            raise PermissionError("in use")
        return real(*a, **k)
    monkeypatch.setattr(settings_file, "save_json", busy_then_ok)
    r.replaced = True
    r.save()
    assert len(tries) == 3 and ArmouryRecord(str(tmp_path)).load() == ""


def test_lock_second_holder_gets_ArmouryLocked(tmp_path):
    folder = str(tmp_path / "armoury")
    a, b = ArmouryRecord(folder), ArmouryRecord(folder)
    with a.lock():
        assert os.path.exists(os.path.join(folder, "switch.lock"))
        with pytest.raises(ArmouryLocked):
            with b.lock(timeout=0.1):
                pass
    with b.lock(timeout=0.1):                # released on exit
        with pytest.raises(ArmouryLocked):
            with a.lock(timeout=0):
                pass


def test_lock_released_when_the_body_raises(tmp_path):
    a, b = ArmouryRecord(str(tmp_path)), ArmouryRecord(str(tmp_path))
    with pytest.raises(ValueError):
        with a.lock():
            raise ValueError("x")
    with b.lock(timeout=0):
        pass
