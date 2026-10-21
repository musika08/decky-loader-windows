import subprocess
from unittest import mock

from decky_loader import helpers


def test_parse_matching_version():
    assert helpers.parse_pythonpath_output("3.14\nC:\\a\nC:\\b\n", (3, 14)) == ["C:\\a", "C:\\b"]


def test_mismatched_system_python_is_ignored():
    assert helpers.parse_pythonpath_output("3.12\nC:\\a", (3, 14)) == []


def test_store_alias_is_skipped():
    alias = r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe"
    with mock.patch.object(helpers.localplatform, "ON_WINDOWS", True), \
         mock.patch.object(helpers.shutil, "which", return_value=alias), \
         mock.patch.object(subprocess, "run") as run:
        assert helpers.get_system_pythonpaths() == []
        run.assert_not_called()
