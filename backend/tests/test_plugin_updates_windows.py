"""Windows file semantics of the staged plugin update (#987): ACL inheritance, renames over open files, cleanup."""
import ctypes
import os
import sys
from unittest.mock import patch

import pytest

from decky_loader import browser as browser_mod

if sys.platform != "win32":
    pytest.skip("Windows only", allow_module_level=True)

from test_plugin_updates import PluginUpdateFixture  # noqa: E402  (tests dir is on sys.path under pytest)

from decky_loader.localplatform.wininstall import create_secure_dir  # noqa: E402

# A protected DACL unlike the one tempfile.mkdtemp(0o700) gives (SYSTEM, Administrators, OWNER RIGHTS):
# it grants BUILTIN\Users read/execute, like %ProgramData%\decky\plugins, and needs no admin to create.
PLUGINS_SDDL = "D:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;AU)(A;OICI;0x1200a9;;;BU)"


def dacl_sddl(path: str) -> str:
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
                                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                             ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    SE_FILE_OBJECT, DACL_SECURITY_INFORMATION = 1, 4
    sd = ctypes.c_void_p()
    rc = advapi.GetNamedSecurityInfoW(path, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, None, None, None, None,
                                      ctypes.byref(sd))
    assert rc == 0, rc
    try:
        out = ctypes.c_void_p()
        assert advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(sd, 1, DACL_SECURITY_INFORMATION,
                                                                           ctypes.byref(out), None)
        try:
            return ctypes.wstring_at(out.value or 0)
        finally:
            kernel.LocalFree(out)
    finally:
        kernel.LocalFree(sd)


class WindowsPluginUpdateTests(PluginUpdateFixture):

    def protect_plugin_root(self) -> None:
        '''Recreate the plugins dir with its own protected DACL, as the installer does.'''
        moved = self.root / "plugins-src"
        os.rename(self.plugin_root, moved)
        create_secure_dir(str(self.plugin_root), PLUGINS_SDDL)
        os.rename(moved / "original-folder", self.old)

    async def test_updated_plugin_inherits_the_plugins_dir_acl(self) -> None:
        self.protect_plugin_root()
        reference_dir = self.plugin_root / "reference"
        reference_dir.mkdir()
        (reference_dir / "f.txt").write_text("x")
        await self.update()
        self.assertEqual((self.old / "main.py").read_text(), "new version")
        # Same inherited ACEs as anything created in the plugins dir; mkdtemp's protected DACL would show up here.
        self.assertEqual(dacl_sddl(str(self.old)), dacl_sddl(str(reference_dir)))
        self.assertEqual(dacl_sddl(str(self.old / "main.py")), dacl_sddl(str(reference_dir / "f.txt")))
        self.assertIn(";;;BU)", dacl_sddl(str(self.old / "main.py")))
        self.assert_clean()

    async def test_first_install_inherits_the_plugins_dir_acl(self) -> None:
        self.protect_plugin_root()
        self.manifest["name"] = "Fresh"
        self.make_zip()
        del self.plugins["Example"]
        reference = self.plugin_root / "reference.txt"
        reference.write_text("x")

        async def load(file: str, folder: str, *, ensure_started: bool = False) -> None:
            self.assertTrue(ensure_started)
            self.assertEqual(folder, "new-folder")

        self.loader.import_plugin.side_effect = load
        await self.install(f"file://{self.archive}", name="Fresh")
        installed = self.plugin_root / "new-folder"
        self.assertEqual(dacl_sddl(str(installed / "main.py")), dacl_sddl(str(reference)))
        self.assertEqual(self.workspaces(), [])

    async def test_briefly_open_file_is_waited_out(self) -> None:
        held = open(self.old / "main.py", "rb")  # no FILE_SHARE_DELETE: blocks renaming its directory
        calls = 0

        def rename_then_release(src: str, dst: str) -> None:
            nonlocal calls
            calls += 1
            try:
                os.rename(src, dst)
            except PermissionError:
                held.close()
                raise

        try:
            with patch.object(browser_mod, "RENAME_RETRY_DELAY", 0), \
                 patch("decky_loader.browser.rename", side_effect=rename_then_release):
                await self.update()
        finally:
            held.close()
        self.assertGreater(calls, 2)  # the backup rename failed once, then the swap went through
        self.assertEqual((self.old / "main.py").read_text(), "new version")
        self.assert_clean()

    async def test_file_held_open_keeps_and_restarts_old_plugin(self) -> None:
        held = open(self.old / "main.py", "rb")
        try:
            with patch.object(browser_mod, "RENAME_RETRY_DELAY", 0):
                with self.assertRaises(PermissionError):
                    await self.update()
            self.assertEqual(self.snapshot(), self.old_contents)
            # The stopped old plugin is started again from its untouched folder.
            self.old_plugin.stop.assert_awaited_once()
            self.loader.import_plugin.assert_awaited_once()
            self.assertEqual(self.loader.import_plugin.await_args.args[1], "original-folder")
        finally:
            held.close()
        self.assert_clean()

    async def test_rename_never_replaces_an_existing_folder(self) -> None:
        # os.rename onto an existing directory fails on Windows; a first install must not clobber a stray folder.
        self.manifest["name"] = "Fresh"
        self.make_zip()
        stray = self.plugin_root / "new-folder"
        stray.mkdir()
        (stray / "keep.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "already exists"):
            await self.install(f"file://{self.archive}", name="Fresh")
        self.assertEqual((stray / "keep.txt").read_text(), "keep")
        self.assertEqual(self.workspaces(), [])

    async def test_cleanup_failure_does_not_fail_a_finished_update(self) -> None:
        with patch("decky_loader.browser.rmtree", side_effect=PermissionError("in use")):
            await self.update()
        self.assertEqual((self.old / "main.py").read_text(), "new version")
        self.assertFalse(self.loader.watcher.disabled)
        # The leftover workspace stays inside the plugins dir, where the loader ignores it.
        leftovers = self.workspaces()
        self.assertEqual(len(leftovers), 1)
        self.assertFalse((leftovers[0] / "plugin.json").exists())
