"""Install/update failure handling on top of #987: rollback fallbacks, the startup workspace sweep, batch installs,
folder-name checks, uninstall serialization, and loopback requests skipping the system proxy."""
import asyncio
import json
import os
import shutil
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zipfile import ZipFile

import pytest

from decky_loader import browser as browser_mod
from decky_loader import helpers
from decky_loader.browser import PluginInstallContext, is_valid_plugin_folder
from decky_loader.localplatform.localplatform import ON_WINDOWS
from decky_loader.utilities import Utilities

from test_plugin_updates import PluginUpdateFixture  # tests dir is on sys.path under pytest


def make_link(link: Path, target: Path) -> None:
    '''A junction on Windows (no privilege needed), a symlink elsewhere.'''
    if sys.platform == "win32":
        import _winapi  # pyright: ignore [reportMissingImports]
        _winapi.CreateJunction(str(target), str(link))  # pyright: ignore [reportUnknownMemberType]
    else:
        os.symlink(target, link, target_is_directory=True)


class RollbackTests(PluginUpdateFixture):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        delay = patch.object(browser_mod, "RENAME_RETRY_DELAY", 0)
        delay.start()
        self.addCleanup(delay.stop)

    def fail_new_version(self) -> None:
        async def load(file: str, folder: str, *, ensure_started: bool = False) -> None:
            await self.load(file, folder, ensure_started=ensure_started)
            if Path(file).read_text() == "new version":
                raise RuntimeError("startup failed")
        self.loader.import_plugin.side_effect = load

    async def test_stuck_failed_version_is_deleted_and_old_restored(self) -> None:
        # Moving the failed version aside keeps failing (Windows path) and the plain rmtree fails once (Linux path):
        # the tolerant delete still clears the folder, the backup goes back and the old plugin restarts.
        self.fail_new_version()
        real_rename, real_rmtree = os.rename, shutil.rmtree
        calls = {"strict_rmtree": 0}

        def rename(src: str, dst: str) -> None:
            if dst.endswith("failed"):
                raise PermissionError("held open")
            real_rename(src, dst)

        def rmtree(p: str, ignore_errors: bool = False, **kw: Any) -> None:
            if not ignore_errors and Path(p) == self.old:
                calls["strict_rmtree"] += 1
                raise PermissionError("held open")
            real_rmtree(p, ignore_errors)

        with patch("decky_loader.browser.rename", side_effect=rename), \
             patch("decky_loader.browser.rmtree", side_effect=rmtree):
            with self.assertRaisesRegex(RuntimeError, "startup failed"):
                await self.update()
        self.assertEqual(self.snapshot(), self.old_contents)
        self.assertEqual(self.loader.import_plugin.await_count, 2)  # new (failed), then the restored old one
        self.assertEqual(self.workspaces(), [])
        self.assertFalse(self.loader.watcher.disabled)

    async def test_undeletable_failed_version_raises_one_clear_error(self) -> None:
        # Nothing can move or delete the failed version, so the backup cannot go back: one error that names both,
        # the backup is kept, and the plugin now in place is still started.
        self.fail_new_version()
        real_rename = os.rename

        def rename(src: str, dst: str) -> None:
            if dst.endswith("failed"):
                raise PermissionError("held open")
            real_rename(src, dst)

        with patch("decky_loader.browser.rename", side_effect=rename), \
             patch("decky_loader.browser.rmtree", side_effect=lambda *a, **k: None):  # pyright: ignore [reportUnknownLambdaType]
            with self.assertRaisesRegex(RuntimeError, "could not be restored") as raised:
                await self.update()
        self.assertIn("startup failed", str(raised.exception))
        self.assertIsInstance(raised.exception.__cause__, OSError)
        backups = self.workspaces("/previous/main.py")
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "old version")
        self.assertEqual(self.loader.import_plugin.await_count, 2)
        self.assertFalse(self.loader.watcher.disabled)


class BatchInstallTests(PluginUpdateFixture):
    async def test_one_failure_does_not_stop_the_rest(self) -> None:
        good = sha256(self.archive.read_bytes()).hexdigest()
        self.browser.install_requests["batch"] = [
            PluginInstallContext(f"file://{self.archive}", "Example", "2", "wrong"),
            PluginInstallContext(f"file://{self.archive}", "Example", "2", good),
        ]
        with self.assertRaisesRegex(RuntimeError, r"Could not install Example: SHA-256 mismatch"):
            await self.browser.confirm_plugin_install("batch")
        self.assertEqual((self.old / "main.py").read_text(), "new version")  # the second request went through
        events = [c.args[0] for c in self.loader.ws.emit.await_args_list]
        self.assertEqual(events.count("loader/plugin_download_error"), 1)
        self.assertEqual(events.count("loader/plugin_download_finish"), 1)

    async def test_single_request_keeps_its_own_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            await self.update("wrong")
        self.loader.ws.emit.assert_any_await("loader/plugin_download_error", "Example", "SHA-256 mismatch for Example")


class FolderNameTests(PluginUpdateFixture):
    def zip_with_folder(self, folder: str) -> None:
        with ZipFile(self.archive, "w") as archive:
            archive.writestr(f"{folder}/plugin.json", json.dumps(self.manifest))
            archive.writestr(f"{folder}/main.py", "new version")

    async def test_windows_illegal_folder_names_are_rejected(self) -> None:
        for folder in ("bad:name", "a<b", "a>b", "a|b", 'a"b', "a?b", "a*b", "trailing.", "trailing "):
            with self.subTest(folder=folder):
                self.zip_with_folder(folder)
                with self.assertRaisesRegex(ValueError, "Invalid plugin directory"):
                    await self.update()
                self.assertEqual(self.snapshot(), self.old_contents)
                self.assertEqual(self.workspaces(), [])

    def test_valid_folder_names(self) -> None:
        for folder in ("my-plugin", "Plugin Name", "plugin.v2", "ünïcode"):
            self.assertTrue(is_valid_plugin_folder(folder), folder)
        for folder in ("", ".", "..", "a/b", "a\\b", "x\x01"):
            self.assertFalse(is_valid_plugin_folder(folder), folder)

    async def test_staged_folder_must_be_inside_staging(self) -> None:
        # If extraction leaves no real directory where the plugin should be, nothing is swapped in.
        with patch.object(ZipFile, "extractall"):
            with self.assertRaisesRegex(ValueError, "Invalid plugin directory"):
                await self.update()
        self.assertEqual(self.snapshot(), self.old_contents)


class UninstallLockTests(PluginUpdateFixture):
    async def test_uninstall_waits_for_a_running_install(self) -> None:
        await self.browser._install_lock.acquire()  # pyright: ignore [reportPrivateUsage]
        task = asyncio.create_task(self.browser.uninstall_plugin("Example"))
        await asyncio.sleep(0.05)
        self.assertFalse(task.done())
        self.assertTrue(self.old.exists())
        self.browser._install_lock.release()  # pyright: ignore [reportPrivateUsage]
        await asyncio.wait_for(task, 5)
        self.assertFalse(self.old.exists())

    async def test_uninstall_restores_the_watcher_state(self) -> None:
        self.loader.watcher.disabled = True  # e.g. live reload turned off
        await self.browser.uninstall_plugin("Example")
        self.assertTrue(self.loader.watcher.disabled)


class WorkspaceSweepTests(PluginUpdateFixture):
    def workspace(self, suffix: str = "x") -> Path:
        ws = Path(self.browser._workspace_parent()) / f"{browser_mod.WORKSPACE_PREFIX}{suffix}"  # pyright: ignore [reportPrivateUsage]
        ws.mkdir()
        return ws

    def stranded_backup(self, name: str, folder: str | None) -> Path:
        ws = self.workspace(name.lower())
        (ws / "previous").mkdir()
        (ws / "previous" / "plugin.json").write_text(json.dumps({"name": name, "author": "t", "flags": []}))
        (ws / "previous" / "main.py").write_text(f"{name} code")
        if folder is not None:
            (ws / browser_mod.BACKUP_FOLDER_MARKER).write_text(folder)
        return ws

    def test_stranded_backup_is_put_back_under_its_folder(self) -> None:
        ws = self.stranded_backup("Lost", "lost-folder")
        (ws / "staged").mkdir()
        self.browser.recover_install_workspaces()
        self.assertEqual((self.plugin_root / "lost-folder" / "main.py").read_text(), "Lost code")
        self.assertFalse(ws.exists())

    def test_backup_without_marker_uses_the_plugin_name(self) -> None:
        ws = self.stranded_backup("Lost", None)
        self.browser.recover_install_workspaces()
        self.assertEqual((self.plugin_root / "Lost" / "main.py").read_text(), "Lost code")
        self.assertFalse(ws.exists())

    def test_backup_of_an_installed_plugin_is_dropped(self) -> None:
        ws = self.stranded_backup("Example", "original-folder")
        self.browser.recover_install_workspaces()
        self.assertEqual(self.snapshot(), self.old_contents)
        self.assertFalse(ws.exists())

    def test_backup_is_kept_when_its_folder_is_taken(self) -> None:
        ws = self.stranded_backup("Lost", "original-folder")
        self.browser.recover_install_workspaces()
        self.assertTrue((ws / "previous" / "main.py").exists())
        self.assertEqual(self.snapshot(), self.old_contents)

    def test_leftovers_are_removed(self) -> None:
        ws = self.workspace("leftover")
        (ws / "staged" / "p").mkdir(parents=True)
        (ws / "staged" / "p" / "main.py").write_text("x")
        (ws / "failed").mkdir()
        self.browser.recover_install_workspaces()
        self.assertFalse(ws.exists())
        self.assertEqual(self.snapshot(), self.old_contents)

    def test_links_are_never_followed(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep")
        (outside / "plugin.json").write_text(json.dumps({"name": "Outside", "author": "t", "flags": []}))
        # A link inside a workspace, a workspace that is a link, and a "previous" that is a link.
        ws = self.workspace("inner")
        make_link(ws / "link", outside)
        make_link(Path(self.browser._workspace_parent()) / f"{browser_mod.WORKSPACE_PREFIX}link", outside)  # pyright: ignore [reportPrivateUsage]
        ws2 = self.workspace("prev")
        make_link(ws2 / "previous", outside)
        self.browser.recover_install_workspaces()
        self.assertEqual((outside / "keep.txt").read_text(), "keep")
        self.assertTrue((outside / "plugin.json").exists())
        self.assertFalse((self.plugin_root / "Outside").exists())
        self.assertEqual([p for p in Path(self.browser._workspace_parent()).iterdir()  # pyright: ignore [reportPrivateUsage]
                          if p.name.startswith(browser_mod.WORKSPACE_PREFIX)], [])


@pytest.mark.parametrize("url, loopback", [
    ("http://127.0.0.1:8080/x", True),
    ("http://127.5.6.7/", True),
    ("http://localhost:1337/", True),
    ("http://LOCALHOST/", True),
    ("http://app.localhost/", True),
    ("http://[::1]:9000/", True),
    ("http://[::ffff:127.0.0.1]/", True),
    ("https://plugins.deckbrew.xyz/plugins", False),
    ("http://192.168.1.10/", False),
    ("http://localhost.example.com/", False),
    ("http://[::2]/", False),
    ("not a url", False),
])
def test_is_loopback_url(url: str, loopback: bool):
    assert helpers.is_loopback_url(url) is loopback


@pytest.mark.parametrize("url, trust_env", [("http://127.0.0.1:5000/api", False), ("http://localhost/", False),
                                            ("https://example.com/", True)])
def test_plugin_requests_skip_the_proxy_only_for_loopback(url: str, trust_env: bool):
    sessions: list[dict[str, Any]] = []

    class FakeSession:
        def __init__(self, **kwargs: Any) -> None:
            sessions.append(kwargs)

        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *a: Any) -> None:
            pass

        async def request(self, *a: Any, **k: Any) -> Any:
            return MagicMock(status=200, headers={}, text=AsyncMock(return_value="ok"))

    utilities = Utilities.__new__(Utilities)
    with patch.object(helpers, "ClientSession", FakeSession):
        res = asyncio.run(utilities.http_request_legacy("GET", url))
    assert res["body"] == "ok"
    assert sessions == [{"trust_env": trust_env}]


def test_store_and_updater_sessions_still_trust_env():
    with patch.object(helpers, "ClientSession") as session:
        helpers.get_session()
    session.assert_called_once_with(trust_env=True, max_field_size=65536, max_line_size=65536)


def test_windows_flag_matches_platform():
    assert ON_WINDOWS == (sys.platform == "win32")
