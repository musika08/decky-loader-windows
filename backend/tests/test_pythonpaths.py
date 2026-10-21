import shutil, subprocess
from unittest import mock

from decky_loader import helpers


def test_windows_never_probes_system_python():
    # The elevated loader must not run whatever `python` the user's PATH resolves to
    with mock.patch.object(helpers.localplatform, "ON_WINDOWS", True), \
         mock.patch.object(shutil, "which", return_value=r"C:\Python\python.exe"), \
         mock.patch.object(subprocess, "run") as run:
        assert helpers.get_system_pythonpaths() == []
        run.assert_not_called()


def test_linux_still_probes_system_python():
    proc = mock.Mock(stdout=b"/usr/lib/python3\n\n/usr/lib/python3/site-packages\n")
    with mock.patch.object(helpers.localplatform, "ON_WINDOWS", False), \
         mock.patch.object(helpers.localplatform, "ON_LINUX", True), \
         mock.patch.object(helpers.localplatform, "ON_MAC", False), \
         mock.patch.object(helpers.localplatform.localplatform, "_get_user_id", return_value=1000, create=True), \
         mock.patch.object(subprocess, "run", return_value=proc) as run:
        assert helpers.get_system_pythonpaths() == ["/usr/lib/python3", "/usr/lib/python3/site-packages"]
        assert run.call_args.args[0][0] == "python3"
