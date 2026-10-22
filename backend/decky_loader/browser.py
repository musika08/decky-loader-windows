# Full imports
import json
# import pprint
# from pprint import pformat

# Partial imports
from aiohttp import ClientError, ClientTimeout
from asyncio import Lock, TimeoutError, sleep
from hashlib import sha256
from io import BytesIO
from logging import getLogger
import os
import stat
from os import R_OK, W_OK, path, listdir, access, mkdir, rename
from secrets import token_hex
from shutil import rmtree
from tempfile import mkdtemp
from time import time
from zipfile import ZipFile
from enum import IntEnum
from typing import Dict, List, TypedDict

# Local modules
from .localplatform.localplatform import chown, chmod, get_chown_plugin_path, ON_WINDOWS
from .loader import Loader, Plugins
from .helpers import get_session, get_ssl_context, download_remote_binary_to_path, get_loader_version
from .enums import UserType
from .settings import SettingsManager

logger = getLogger("Browser")

# Windows: how often to retry renaming a plugin directory that something still holds open
RENAME_ATTEMPTS = 10
RENAME_RETRY_DELAY = 0.5

WORKSPACE_PREFIX = ".decky-install-"
# Written next to the backup before the swap, so a startup sweep knows which plugin folder the backup came from
BACKUP_FOLDER_MARKER = "previous.folder"
# Characters Windows refuses in file names (plus both separators), and names it silently alters
_ILLEGAL_FOLDER_CHARS = set('<>:"|?*/\\')

def is_valid_plugin_folder(folder: str) -> bool:
    """A single path component that names the same folder on Linux and Windows."""
    return (
        folder not in ("", ".", "..")
        and not any(c in _ILLEGAL_FOLDER_CHARS or ord(c) < 32 for c in folder)
        and not folder.endswith((".", " "))
    )

def _is_link(p: str) -> bool:
    """Symlink, junction or any other reparse point. Never follows it."""
    try:
        st = os.lstat(p)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)

def _remove_no_follow(p: str) -> None:
    """Delete `p` without ever following a link: a link (or junction) is removed itself, never its target."""
    if _is_link(p):
        try:
            os.rmdir(p) # a directory symlink or junction on Windows
        except OSError:
            os.unlink(p)
        return
    # rmtree never descends into symlinks, and on Windows unlinks junctions instead of walking them
    rmtree(p)

class PluginInstallType(IntEnum):
    INSTALL = 0
    REINSTALL = 1
    UPDATE = 2
    DOWNGRADE = 3
    OVERWRITE = 4

class PluginInstallRequest(TypedDict):
    name: str
    artifact: str
    version: str
    hash: str
    install_type: PluginInstallType

class PluginInstallContext:
    def __init__(self, artifact: str, name: str, version: str, hash: str) -> None:
        self.artifact = artifact
        self.name = name
        self.version = version
        self.hash = hash

class PluginBrowser:
    def __init__(self, plugin_path: str, plugins: Plugins, loader: Loader, settings: SettingsManager) -> None:
        self.plugin_path = plugin_path
        self.plugins = plugins
        self.loader = loader
        self.settings = settings
        self._install_lock = Lock()
        self.install_requests: Dict[str, PluginInstallContext | List[PluginInstallContext]] = {}

    async def _download_remote_binaries_for_plugin_with_name(self, pluginBasePath: str):
        rv = False
        try:
            packageJsonPath = path.join(pluginBasePath, 'package.json')
            pluginBinPath = path.join(pluginBasePath, 'bin')

            logger.debug(f"Checking package.json at {packageJsonPath}")

            if access(packageJsonPath, R_OK):
                with open(packageJsonPath, "r", encoding="utf-8") as f:
                    packageJson = json.load(f)
                    if "remote_binary" in packageJson and len(packageJson["remote_binary"]) > 0:
                        # create bin directory if needed.
                        chmod(pluginBasePath, 777)
                        if access(pluginBasePath, W_OK):
                            if not path.exists(pluginBinPath):
                                logger.debug(f"Creating bin directory at {pluginBinPath}")
                                mkdir(pluginBinPath)
                            if not access(pluginBinPath, W_OK):
                                chmod(pluginBinPath, 777)

                        rv = True
                        for remoteBinary in packageJson["remote_binary"]:
                            # Required Fields. If any Remote Binary is missing these fail the install.
                            binName = remoteBinary["name"]
                            # A binary must stay inside the staged bin directory.
                            if (
                                not isinstance(binName, str)
                                or not binName
                                or binName in (".", "..")
                                or "/" in binName
                                or "\\" in binName
                            ):
                                raise ValueError("Invalid remote binary name")

                            binURL = remoteBinary["url"]
                            binHash = remoteBinary["sha256hash"]
                            logger.info(f"Attempting to download {binName} from {binURL}")
                            if not await download_remote_binary_to_path(binURL, binHash, path.join(pluginBinPath, binName)):
                                rv = False
                                raise Exception(f"Error Downloading Remote Binary {binName}@{binURL} with hash {binHash} to {path.join(pluginBinPath, binName)}")

                    else:
                        rv = True
                        logger.info(f"No Remote Binaries to Download")

        except Exception as e:
            rv = False
            logger.debug(str(e))

        return rv

    """Return the filename (only) for the specified plugin"""
    def find_plugin_folder(self, name: str) -> str | None:
        for folder in listdir(self.plugin_path):
            try:
                with open(path.join(self.plugin_path, folder, 'plugin.json'), "r", encoding="utf-8") as f:
                    plugin = json.load(f)

                if plugin['name'] == name:
                    return folder
            except:
                logger.debug(f"skipping {folder}")
    
    def set_plugin_dir_permissions(self, plugin_dir: str) -> bool:
        plugin_json_path = path.join(plugin_dir, 'plugin.json')
        logger.debug(f"Checking plugin.json at {plugin_json_path}")

        root_plugin = False

        if access(plugin_json_path, R_OK):
            with open(plugin_json_path, "r", encoding="utf-8") as f:
                plugin_json = json.load(f)
                if "flags" in plugin_json and "root" in plugin_json["flags"]:
                    root_plugin = True

        logger.debug("root_plugin %d, dir %s", root_plugin, plugin_dir)
        if get_chown_plugin_path():
            return chown(plugin_dir, UserType.EFFECTIVE_USER if root_plugin else UserType.HOST_USER, True) and chown(plugin_dir, UserType.EFFECTIVE_USER, False) and chmod(plugin_dir, 755) and chown(plugin_json_path, UserType.EFFECTIVE_USER, False) and chmod(plugin_json_path, 755)
        else:
            logger.debug("chown disabled by environment")
            return True

    async def uninstall_plugin(self, name: str):
        # Never in the middle of an install's swap, which owns the plugin folder and the watcher.
        async with self._install_lock:
            await self._uninstall_plugin(name)

    async def _uninstall_plugin(self, name: str):
        watcher = self.loader.watcher
        watcher_disabled = watcher.disabled if watcher else False
        if watcher:
            watcher.disabled = True
        plugin_folder = self.find_plugin_folder(name)
        assert plugin_folder is not None
        plugin_dir = path.join(self.plugin_path, plugin_folder)
        try:
            logger.info("uninstalling " + name)
            logger.info(" at dir " + plugin_dir)
            logger.debug("calling frontend unload for %s" % str(name))
            await self.loader.ws.emit("loader/unload_plugin", name)
            # plugins_snapshot = self.plugins.copy()
            # snapshot_string = pformat(plugins_snapshot)
            # logger.debug("current plugins: %s", snapshot_string)
                
            if name in self.plugins:
                plugin_display = self.plugins[name].get_display_name()
                logger.debug(f"Plugin {plugin_display} was found")
                await self.plugins[name].stop(uninstall=True)
                logger.debug(f"Plugin {plugin_display} was stopped")
                del self.plugins[name]
                logger.debug(f"Plugin {plugin_display} was removed from the dictionary")
                self.cleanup_plugin_settings(name)
            logger.debug("removing files %s" % str(name))
            rmtree(plugin_dir)
        except FileNotFoundError:
            logger.warning(f"Plugin {name} not installed, skipping uninstallation")
        except Exception as e:
            logger.error(f"Plugin {name} in {plugin_dir} was not uninstalled")
            logger.error(f"Error at {str(e)}", exc_info=e)
        finally:
            if watcher:
                watcher.disabled = watcher_disabled

    async def _install(self, artifact: str, name: str, version: str, hash: str):
        # Serialize swaps and watcher ownership, including concurrent install requests.
        async with self._install_lock:
            await self._stage_install(artifact, name, version, hash)

    async def _stage_install(self, artifact: str, name: str, version: str, hash: str):
        await self.loader.ws.emit("loader/plugin_download_start", name)
        await self.loader.ws.emit("loader/plugin_download_info", 5, "Store.download_progress_info.start")
        res_zip = None

        isInstalled = self.find_plugin_folder(name) is not None

        # Check if the file is a local file or a URL
        if artifact.startswith("file://"):
            logger.info(f"Installing {name} from local ZIP file (Version: {version})")
            await self.loader.ws.emit("loader/plugin_download_info", 10, "Store.download_progress_info.open_zip")
            with open(artifact[7:], "rb") as archive:
                res_zip = BytesIO(archive.read())
        else:
            logger.info(f"Installing {name} from URL (Version: {version})")
            await self.loader.ws.emit("loader/plugin_download_info", 10, "Store.download_progress_info.download_zip")

            async with get_session() as client:
                logger.debug(f"Fetching {artifact}")
                res = await client.get(artifact, ssl=get_ssl_context())
                #TODO track progress of this download in chunks like with decky updates
                #TODO but squish with min 15 and max 75
                if res.status == 200:
                    logger.debug("Got 200. Reading...")
                    data = await res.read()
                    logger.debug(f"Read {len(data)} bytes")
                    res_zip = BytesIO(data)
                else:
                    logger.fatal(f"Could not fetch from URL. {await res.text()}")

            await self.loader.ws.emit("loader/plugin_download_info", 70, "Store.download_progress_info.increment_count")
            storeUrl = ""
            match self.settings.getSetting("store", 0):
                case 0: storeUrl = "https://plugins.deckbrew.xyz/plugins" # default
                case 1: storeUrl = "https://testing.deckbrew.xyz/plugins" # testing
                case 2: storeUrl = self.settings.getSetting("store-url", "https://plugins.deckbrew.xyz/plugins")  # custom
                case _: storeUrl = "https://plugins.deckbrew.xyz/plugins"
            logger.info(f"Incrementing installs for {name} from URL {storeUrl} (version {version})")
            # A failed download-count request should not prevent installation.
            try:
                async with get_session() as client:
                    res = await client.post(
                        storeUrl + f"/{name}/versions/{version}/increment?isUpdate={isInstalled}",
                        headers={"X-Decky-Version": get_loader_version()},
                        ssl=get_ssl_context(),
                        timeout=ClientTimeout(total=5),
                    )
                    if res.status != 200:
                        logger.warning(f"Server did not accept install count increment request. code: {res.status}")
            except (TimeoutError, ClientError) as e:
                logger.warning(f"Failed to increment install count for {name}, continuing installation: {e}")

        await self.loader.ws.emit("loader/plugin_download_info", 75, "Store.download_progress_info.parse_zip")
        if res_zip is None:
            raise RuntimeError(f"Could not fetch {artifact}")

        if hash and sha256(res_zip.getbuffer()).hexdigest() != hash:
            raise ValueError(f"SHA-256 mismatch for {name}")

        workspace = self._make_install_workspace()
        staging = path.join(workspace, "staged")
        backup = path.join(workspace, "previous")
        committed = False

        try:
            # Everything in this phase works on temporary files. The installed
            # plugin keeps running until extraction, binaries and permissions pass.
            with ZipFile(res_zip) as archive:
                folder, name = self._extract_plugin_archive(archive, staging, name, version)

            staged_plugin = path.join(staging, folder)

            await self.loader.ws.emit(
                "loader/plugin_download_info", 95, "Store.download_progress_info.download_remote"
            )
            if not await self._download_remote_binaries_for_plugin_with_name(staged_plugin):
                raise RuntimeError("Could not download remote binaries")

            if not self.set_plugin_dir_permissions(staged_plugin):
                raise RuntimeError("Could not prepare plugin permissions")

            new_install = await self._replace_plugin(staged_plugin, folder, name, backup)
            committed = True

            # An update leaves all existing settings alone. Only a first install
            # needs to add the plugin to the order shown in the frontend.
            if new_install:
                plugin_order: List[str] = self.settings.getSetting("pluginOrder", [])
                if name not in plugin_order:
                    plugin_order.append(name)
                    self.settings.setSetting("pluginOrder", plugin_order)

            await self.loader.ws.emit("loader/plugin_download_finish", name)
        finally:
            self._cleanup_install(workspace, backup, committed)

    def _extract_plugin_archive(
        self, archive: ZipFile, staging: str, name: str, version: str
    ) -> tuple[str, str]:
        """Validate the archive layout and identity, then extract into staging."""
        manifests = [
            entry for entry in archive.namelist()
            if entry.endswith("/plugin.json") and entry.count("/") == 1
        ]
        if len(manifests) != 1:
            raise ValueError("Expected exactly one top-level plugin.json")

        folder = manifests[0].split("/")[0]
        if not is_valid_plugin_folder(folder):
            raise ValueError("Invalid plugin directory")

        # Reject extra roots, traversal and symlinks before writing any files.
        # Otherwise an archive could escape staging and overwrite an installation.
        for entry in archive.infolist():
            parts = entry.filename.rstrip("/").split("/")
            if parts[0] != folder or any(part in ("", ".", "..") for part in parts):
                raise ValueError(f"Invalid ZIP entry: {entry.filename}")

            is_symlink = (entry.external_attr >> 16) & 0o170000 == 0o120000
            if "\\" in entry.filename or is_symlink:
                raise ValueError(f"Invalid ZIP entry: {entry.filename}")

        manifest = json.loads(archive.read(manifests[0]))
        archive_name = manifest["name"]
        if not isinstance(archive_name, str) or not archive_name.strip():
            raise ValueError("Invalid plugin name")

        # Local development installs may not know the name until the ZIP is read.
        if version == "dev":
            name = archive_name
        elif archive_name != name:
            raise ValueError("Plugin name does not match requested plugin")

        archive.extractall(staging)

        # The plugin must have landed as a real directory inside staging, whatever the platform made of its name.
        staged_plugin = path.join(staging, folder)
        real_staging = path.realpath(staging)
        if (not path.isdir(staged_plugin) or _is_link(staged_plugin)
                or path.commonpath([path.realpath(staged_plugin), real_staging]) != real_staging):
            raise ValueError("Invalid plugin directory")
        return folder, name

    async def _replace_plugin(
        self, staged_plugin: str, folder: str, name: str, backup: str
    ) -> bool:
        """Swap in a prepared plugin, restoring the previous version on failure.

        Return True for a first install, False when replacing an existing plugin.
        """
        previous_folder = self.find_plugin_folder(name)

        # Settings and data paths depend on this directory name, so keep it even
        # when the new archive uses a different folder name.
        target_folder = previous_folder or folder
        target = path.join(self.plugin_path, target_folder)
        if previous_folder is None and path.exists(target):
            raise ValueError(f"Plugin directory already exists: {target_folder}")

        watcher = self.loader.watcher
        watcher_disabled = watcher.disabled if watcher else False
        if watcher:
            watcher.disabled = True

        swapped = False

        try:
            if previous_folder:
                await self.loader.ws.emit(
                    "loader/plugin_download_info", 80,
                    "Store.download_progress_info.uninstalling_previous",
                )
                await self.loader.ws.emit("loader/unload_plugin", name)

            # Stop without uninstalling. Uninstall would run plugin cleanup hooks
            # and remove the user's disabled, hidden, frozen and ordering settings.
            if name in self.plugins:
                await self.plugins[name].stop()
                self.plugins.pop(name, None)

            if previous_folder:
                with open(path.join(path.dirname(backup), BACKUP_FOLDER_MARKER), "w", encoding="utf-8") as marker:
                    marker.write(previous_folder)
                await self._rename_dir(target, backup)

            await self.loader.ws.emit(
                "loader/plugin_download_info", 90, "Store.download_progress_info.installing_plugin"
            )
            await self._rename_dir(staged_plugin, target)
            swapped = True

            # Keep the backup until backend initialization succeeds. Starting a
            # process alone does not tell us whether the plugin can actually load.
            await self.loader.import_plugin(
                path.join(target, "main.py"), target_folder, ensure_started=True
            )
        except BaseException as error:
            # Cancellation needs the same recovery as a failed load. Stop the new
            # backend before removing its files, then put the old directory back.
            failed_plugin = self.plugins.pop(name, None) if swapped else None
            if failed_plugin:
                await failed_plugin.stop()

            if swapped:
                await self._discard_failed_version(target, path.join(path.dirname(backup), "failed"))

            restore_error: OSError | None = None
            if path.exists(backup):
                try:
                    await self._rename_dir(backup, target)
                except OSError as e:
                    restore_error = e
                    logger.error(f"Could not restore the previous version of {name} from {backup}: {e}")

            # Start whatever plugin is now in place: normally the restored previous version.
            if previous_folder and path.isfile(path.join(target, "plugin.json")):
                try:
                    await self.loader.import_plugin(
                        path.join(target, "main.py"), target_folder, ensure_started=True
                    )
                except Exception:
                    logger.exception("Could not reload plugin %s after a failed update", name)

            if restore_error is not None:
                raise RuntimeError(
                    f"Updating {name} failed ({error!r}) and the previous version could not be restored; "
                    f"it is kept at {backup}"
                ) from restore_error
            raise
        finally:
            # Restore the previous state, which may already have been disabled.
            if watcher:
                watcher.disabled = watcher_disabled

        return previous_folder is None

    async def _discard_failed_version(self, target: str, aside: str) -> None:
        """Get a failed new version out of `target`, best effort, so the previous one can go back."""
        try:
            if ON_WINDOWS:
                # A rename waits out a briefly held file like the swap did; the workspace cleanup removes it.
                await self._rename_dir(target, aside)
            else:
                rmtree(target)
            return
        except OSError as e:
            logger.warning(f"Could not move the failed version out of {target}, deleting what can be deleted: {e}")
        for attempt in range(RENAME_ATTEMPTS):
            rmtree(target, ignore_errors=True)
            if not path.lexists(target):
                return
            if attempt < RENAME_ATTEMPTS - 1:
                await sleep(RENAME_RETRY_DELAY)
        logger.error(f"Could not remove the failed version at {target}")

    def _workspace_parent(self) -> str:
        return self.plugin_path if ON_WINDOWS else path.dirname(path.realpath(self.plugin_path))

    def recover_install_workspaces(self) -> None:
        """Startup sweep: put back previous versions an interrupted update left behind, then drop the workspaces."""
        parent = self._workspace_parent()
        try:
            entries = [e for e in listdir(parent) if e.startswith(WORKSPACE_PREFIX)]
        except OSError as e:
            logger.warning(f"Could not look for leftover install workspaces in {parent}: {e}")
            return
        for entry in entries:
            workspace = path.join(parent, entry)
            try:
                if _is_link(workspace) or not path.isdir(workspace):
                    logger.warning(f"Removing {workspace}, which is not an install workspace")
                    _remove_no_follow(workspace)
                    continue
                if not self._recover_previous(workspace):
                    continue # keep it: it holds the only copy of a plugin
                _remove_no_follow(workspace)
                logger.info(f"Removed leftover install workspace {workspace}")
            except OSError as e:
                logger.warning(f"Could not clean up install workspace {workspace}: {e}")

    def _recover_previous(self, workspace: str) -> bool:
        """Return True when nothing in `workspace` still needs to be kept."""
        backup = path.join(workspace, "previous")
        manifest = path.join(backup, "plugin.json")
        if not path.lexists(backup):
            return True
        if _is_link(backup) or _is_link(manifest) or not path.isfile(manifest):
            return True # not a backup this code made
        try:
            with open(manifest, "r", encoding="utf-8") as f:
                plugin_name = json.load(f)["name"]
            assert isinstance(plugin_name, str) and plugin_name.strip()
        except Exception as e:
            logger.error(f"Keeping {backup}: its plugin.json is unreadable ({e})")
            return False
        if self.find_plugin_folder(plugin_name) is not None:
            logger.info(f"{plugin_name} is installed, dropping the stale backup in {workspace}")
            return True

        folder = None
        try:
            with open(path.join(workspace, BACKUP_FOLDER_MARKER), "r", encoding="utf-8") as f:
                folder = f.read().strip()
        except OSError:
            pass
        if folder is None or not is_valid_plugin_folder(folder):
            folder = plugin_name if is_valid_plugin_folder(plugin_name) else None
        target = path.join(self.plugin_path, folder) if folder else None
        if target is None or path.lexists(target):
            logger.error(f"Keeping {backup}: no free folder to restore {plugin_name} into")
            return False
        try:
            rename(backup, target)
        except OSError as e:
            logger.error(f"Keeping {backup}: could not restore {plugin_name} to {target}: {e}")
            return False
        logger.warning(f"Restored {plugin_name} to {target} after an interrupted update")
        return True

    def _make_install_workspace(self) -> str:
        if ON_WINDOWS:
            # Not mkdtemp: on Windows its 0o700 gives the directory a protected DACL (SYSTEM, Administrators,
            # OWNER RIGHTS only) that the extracted files inherit and keep through the rename, so restricted plugin
            # hosts could no longer read their own plugin. A plain mkdir inside the plugins directory inherits
            # exactly the ACEs of an installed plugin folder, and the swap stays a same-directory rename.
            # The live-reload watcher and the plugin scan skip it: neither looks below a top-level folder
            # without a plugin.json.
            for _ in range(100):
                workspace = path.join(self.plugin_path, f"{WORKSPACE_PREFIX}{token_hex(8)}")
                try:
                    mkdir(workspace)
                except FileExistsError:
                    continue
                return workspace
            raise FileExistsError("Could not create a plugin install workspace")

        # A sibling of the plugin root stays on the same filesystem without exposing
        # staged main.py/dist files to the recursive live-reload watcher.
        return mkdtemp(
            prefix=WORKSPACE_PREFIX,
            dir=self._workspace_parent(),
        )

    async def _rename_dir(self, src: str, dst: str) -> None:
        if not ON_WINDOWS:
            rename(src, dst)
            return
        # Windows refuses to rename a directory while any handle is open below it. The plugin's processes are
        # gone by then (stop() waits for its job to empty), but antivirus and the search indexer briefly open
        # freshly written files. os.rename never replaces an existing dst here, which the callers rely on.
        for attempt in range(RENAME_ATTEMPTS):
            try:
                rename(src, dst)
                return
            except PermissionError:
                if attempt == RENAME_ATTEMPTS - 1:
                    raise
                logger.debug(f"{src} is in use, retrying the rename")
                await sleep(RENAME_RETRY_DELAY)

    def _cleanup_install(self, workspace: str, backup: str, committed: bool) -> None:
        # If restoring the old directory failed, the backup may be its only copy.
        # Leave it for manual recovery rather than deleting it with staging files.
        if path.exists(backup) and not committed:
            logger.error("Previous installation retained for recovery at %s", backup)
            return

        if ON_WINDOWS:
            # A file still held open must not turn a finished install into a failure. The leftover folder sits in
            # the plugins directory, where the loader ignores it.
            try:
                rmtree(workspace)
            except OSError as e:
                logger.warning(f"Could not remove install workspace {workspace}: {e}")
            return

        rmtree(workspace)

    async def request_plugin_install(self, artifact: str, name: str, version: str, hash: str, install_type: PluginInstallType):
        request_id = str(time())
        self.install_requests[request_id] = PluginInstallContext(artifact, name, version, hash)

        await self.loader.ws.emit("loader/add_plugin_install_prompt", name, version, request_id, hash, install_type)

    async def request_multiple_plugin_installs(self, requests: List[PluginInstallRequest]):
        request_id = str(time())
        self.install_requests[request_id] = [PluginInstallContext(req['artifact'], req['name'], req['version'], req['hash']) for req in requests]

        await self.loader.ws.emit("loader/add_multiple_plugins_install_prompt", request_id, requests)

    async def confirm_plugin_install(self, request_id: str):
        requestOrRequests = self.install_requests.pop(request_id)
        requests = requestOrRequests if isinstance(requestOrRequests, list) else [requestOrRequests]
        # One failed plugin must not stop the others; the caller still learns about every failure.
        failures: List[tuple[str, Exception]] = []
        for req in requests:
            try:
                await self._install(req.artifact, req.name, req.version, req.hash)
            except Exception as e:
                logger.error(f"Installing {req.name} ({req.version}) failed: {e}", exc_info=e)
                failures.append((req.name, e))
                try:
                    await self.loader.ws.emit("loader/plugin_download_error", req.name, str(e))
                except Exception:
                    logger.exception("Could not report the failed install of %s", req.name)
        if len(requests) == 1 and failures:
            raise failures[0][1]
        if failures:
            raise RuntimeError("Could not install " + "; ".join(f"{name}: {e}" for name, e in failures))

    def cancel_plugin_install(self, request_id: str):
        self.install_requests.pop(request_id)

    def cleanup_plugin_settings(self, name: str):
        """Removes any settings related to a plugin. Propably called when a plugin is uninstalled.

        Args:
            name (string): The name of the plugin
        """
        frozen_plugins = self.settings.getSetting("frozenPlugins", [])
        if name in frozen_plugins:
            frozen_plugins.remove(name)
            self.settings.setSetting("frozenPlugins", frozen_plugins)

        hidden_plugins = self.settings.getSetting("hiddenPlugins", [])
        if name in hidden_plugins:
            hidden_plugins.remove(name)
            self.settings.setSetting("hiddenPlugins", hidden_plugins)

        plugin_order = self.settings.getSetting("pluginOrder", [])

        if name in plugin_order:
            plugin_order.remove(name)
            self.settings.setSetting("pluginOrder", plugin_order)
            
        disabled_plugins: List[str] = self.settings.getSetting("disabled_plugins", [])
        if name in disabled_plugins:
            disabled_plugins.remove(name)
            self.settings.setSetting("disabled_plugins", disabled_plugins)

        logger.debug("Removed any settings for plugin %s", name)
