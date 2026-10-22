<h1 align="center">
  <a name="logo" href="https://deckbrew.xyz/"><img src="https://deckbrew.xyz/static/icon-45ca1f5aea376a9ad37e92db906f283e.png" alt="Deckbrew logo" width="200"></a>
  <br>
  Decky Loader
  <br>
  <a name="download button" href="https://github.com/SteamDeckHomebrew/decky-installer/releases/latest/download/decky_installer.desktop"><img src="./docs/images/download_button.svg"  alt="Download decky" width="150px" style="padding-top: 15px;"></a>
</h1>

<p align="center">
  <a href="https://github.com/SteamDeckHomebrew/decky-loader/releases"><img src="https://img.shields.io/github/downloads/SteamDeckHomebrew/decky-loader/total" /></a>
  <a href="https://github.com/SteamDeckHomebrew/decky-loader/stargazers"><img src="https://img.shields.io/github/stars/SteamDeckHomebrew/decky-loader" /></a>
  <a href="https://github.com/SteamDeckHomebrew/decky-loader/commits/main"><img src="https://img.shields.io/github/last-commit/SteamDeckHomebrew/decky-loader.svg" /></a>
  <a href="https://weblate.werwolv.net/engage/decky/"><img src="https://weblate.werwolv.net/widgets/decky/-/decky/svg-badge.svg" alt="Translation status" /></a>
  <a href="https://github.com/SteamDeckHomebrew/decky-loader/blob/main/LICENSE"><img src="https://img.shields.io/github/license/SteamDeckHomebrew/decky-loader" /></a>
  <a href="https://deckbrew.xyz/discord"><img src="https://img.shields.io/discord/960281551428522045?color=%235865F2&label=discord" /></a>
  <br>
  <br>
  🌐 <a href="./README.md">English</a> · <a href="./README_zh-Hans.md">简体中文</a>
  <br>
  <br>
<!--  <img src="https://media.discordapp.net/attachments/966017112244125756/1012466063893610506/main.jpg" alt="Decky screenshot" width="80%">-->
</p>

## 🩵 Backers and Sponsors

[Become a backer or sponsor](https://opencollective.com/steamdeckhomebrew) to support our work! Contributing to our collective effort will help Decky Loader developers cover the costs of web servers, acquire new development hardware, and more.

<!-- SPONSORS COMMENTED OUT UNTIL WE GET SOME SPONSORS TO AVOID BLANK SVG SPACE -->
<a href="https://opencollective.com/steamdeckhomebrew"><img alt="Steam Deck Homebrew sponsors on Open Collective" src="https://opencollective.com/steamdeckhomebrew/sponsors.svg?button=true&avatarHeight=46&width=750"></a>

<a href="https://opencollective.com/steamdeckhomebrew"><img alt="Steam Deck Homebrew backers on Open Collective" src="https://opencollective.com/steamdeckhomebrew/backers.svg?button=false&avatarHeight=46&width=750"></a>

## 📖 About

Decky Loader is a homebrew plugin launcher for the Steam Deck. It can be used to [stylize your menus](https://github.com/suchmememanyskill/SDH-CssLoader), [change system sounds](https://github.com/EMERALD0874/SDH-AudioLoader), [adjust your screen saturation](https://github.com/libvibrant/vibrantDeck), [change additional system settings](https://github.com/NGnius/PowerTools), and [more](https://plugins.deckbrew.xyz/).

For more information about Decky Loader as well as documentation and development tools, please visit [our wiki](https://wiki.deckbrew.xyz).

### 🎨 Features

🧹 Clean injecting and loading of multiple plugins.  
🔒 Stays installed between system updates and reboots.  
🔗 Allows two-way communication between plugins and the loader.  
🐍 Supports Python functions run from TypeScript React.  
🌐 Allows plugins to make fetch calls that bypass CORS completely.

### 🤔 Common Issues

- Syncthing may use port 8080 on Steam Deck, which Decky Loader needs to function. If you are using Syncthing as a service, please change its port to something else.
  - 8384 is the recommended port for Syncthing.
- If you are using any software that uses port 1337 or 8080, please change its port to something else or uninstall it.
- Sometimes Decky will disappear on SteamOS updates. This can easily be fixed by just re-running the installer and installing the stable branch again. If this doesn't work, try installing the prerelease instead. If that doesn't work, then [check the existing issues](https://github.com/SteamDeckHomebrew/decky-loader/issues) and if there isn't one then you can [file a new issue](https://github.com/SteamDeckHomebrew/decky-loader/issues/new?assignees=&labels=bug&template=bug_report.yml&title=%5BBUG%5D+%3Ctitle%3E).

## 💾 Installation

- This installation can be done without an admin/sudo password set.

1. Prepare a mouse and keyboard if possible.
   - Keyboards and mice can be connected to the Steam Deck via USB-C or Bluetooth.
   - Many Bluetooth keyboard and mouse apps are available for iOS and Android. (KDE connect is preinstalled on the steam deck)
   - The Steam Link app is available on [Windows](https://media.steampowered.com/steamlink/windows/latest/SteamLink.zip), [macOS](https://apps.apple.com/us/app/steam-link/id1246969117), and [Linux](https://flathub.org/apps/details/com.valvesoftware.SteamLink). It works well as a remote desktop substitute.
   - If you have no other options, use the right trackpad as a mouse and press <img src="./docs/images/light/steam.svg#gh-dark-mode-only" height=16><img src="./docs/images/dark/steam.svg#gh-light-mode-only" height=16>+<img src="./docs/images/light/x.svg#gh-dark-mode-only" height=16><img src="./docs/images/dark/x.svg#gh-light-mode-only" height=16> to open the on-screen keyboard as needed.
1. Press the <img src="./docs/images/light/steam.svg#gh-dark-mode-only" height=16><img src="./docs/images/dark/steam.svg#gh-light-mode-only" height=16> button and open the Power menu.
1. Select "Switch to Desktop".
1. Navigate to this Github page on a browser of your choice.
1. Download the [installer file](https://github.com/SteamDeckHomebrew/decky-installer/releases/latest/download/decky_installer.desktop). (If using firefox, it will be named `decky_installer.desktop.download`. Rename it to `decky_installer.desktop` before running it)
1. Drag the file onto your desktop and double click it to run it.
1. Either type your admin password or allow Decky to temporarily set your admin password to `Decky!` (this password will be removed after the installer finishes)
1. Choose the version of Decky Loader you want to install.
   - **Latest Release**  
     Intended for most users. This is the latest stable version of Decky Loader.
   - **Latest Pre-Release**  
     Intended for plugin developers. Pre-releases are unlikely to be fully stable but contain the latest changes. For more information on plugin development, please consult [the wiki page](https://wiki.deckbrew.xyz/en/loader-dev/development).
1. Open the Return to Gaming Mode shortcut on your desktop.

- There is also a fast install for those who can use Konsole. Run `curl -L https://github.com/SteamDeckHomebrew/decky-installer/releases/latest/download/install_release.sh | sh` and type your password when prompted.

### 👋 Uninstallation

We are sorry to see you go! If you are considering uninstalling because you are having issues, please consider [opening an issue](https://github.com/SteamDeckHomebrew/decky-loader/issues) or [joining our Discord](https://deckbrew.xyz/discord) so we can help you and other users.

1. Press the <img src="./docs/images/light/steam.svg#gh-dark-mode-only" height=16><img src="./docs/images/dark/steam.svg#gh-light-mode-only" height=16> button and open the Power menu.
1. Select "Switch to Desktop".
1. Run the installer file again, and select `uninstall decky loader`.

- There is also a fast uninstall for those who can use Konsole. Run `curl -L https://github.com/SteamDeckHomebrew/decky-installer/releases/latest/download/uninstall.sh | sh` and type your password when prompted.

## 🪟 Windows Installation

Decky Loader also runs on Windows, next to the Steam client. It is not installed through the Steam Deck installer.

**Requirements**

- A Windows account with administrator rights. Installed mode registers an elevated task, so it cannot be set up from a standard account.
- Steam's remote debugging enabled. Decky connects to Steam's debugger at `http://localhost:8080`, so it must be reachable there. See [the Decky wiki](https://wiki.deckbrew.xyz) for how to enable it.

**Install**

1. Build the installer folder from a checkout of this repository: `powershell -ExecutionPolicy Bypass -File tools\update-decky.ps1 -Rebuild -NoInstall` (it builds the frontend, runs the backend tests and calls `tools\build_win_runtime.py`). This makes `build\installer\DeckyLoader` next to the checkout, with a `python` folder (a private copy of the Python runtime the backend venv was made from, which must be the python.org build signed by the Python Software Foundation; the build checks the signature) and an `app` folder (Decky's source and its dependencies). Installed mode does not use a PyInstaller exe; the single-file `.exe` builds are for portable mode and cannot be installed.
1. If you were running a portable copy of Decky, close it first. Both use port 1337, so the installed copy cannot start while it runs.
1. From any terminal, in the installer folder, run `python\python.exe -I app\main.py --install` (`tools\update-decky.ps1` without `-NoInstall` does this for you). Windows will ask for administrator approval if needed. This copies the `python` and `app` folders to `%ProgramFiles%\Decky Loader` and registers a scheduled task named `Decky Loader`, which runs `%ProgramFiles%\Decky Loader\python\pythonw.exe -I "%ProgramFiles%\Decky Loader\app\main.py"` at every logon, without a window, and starts it right away. The runtime there only loads code from its own `Lib`, `DLLs` and `..\app` folders (a `python313._pth` file), never from `PYTHONPATH` or the current directory, and `-I` makes it ignore every other `PYTHON*` environment variable too (Decky refuses to start on that runtime without `-I`). The elevated install runs in a console window of its own that closes when it finishes; if it fails, the reason is in `%ProgramData%\decky\logs\install.log` (or a `%TEMP%\decky-install-*.log` file when that folder is not there yet), and `tools\update-decky.ps1` prints it. It also creates the empty file `.cef-enable-remote-debugging` in your Steam folder if it is missing, which turns on Steam's debugger that Decky connects to (restart Steam once for it to take effect). Uninstalling leaves that file in place. You can delete the extracted folder afterwards.
1. Open Steam in Big Picture mode. The Decky tab appears in the Quick Access menu.
1. Install plugins from Decky's built-in store.

**Restarting**

Restarting Steam or closing Big Picture does not stop Decky. If Decky itself restarts while Steam still shows it (for example after an update), it restarts Steam's webhelper, so the Steam interface reloads briefly. If Decky was stopped, for example with the shutdown option in its settings, start it again without logging out by running `schtasks /Run /TN "Decky Loader"`.

**Where things live**

The program is in `%ProgramFiles%\Decky Loader` (`python` is its Python runtime, `app` is Decky itself). Plugins and Decky's settings are in `%ProgramData%\decky` (`plugins` and `settings`), which only administrators can change.

**Updating**

Decky's built-in updater is not available in installed mode; its Updates page says so instead of offering an update. To update, run `tools\update-decky.ps1`, or build a new installer folder and run `python\python.exe -I app\main.py --install` from it again. This stops the running Decky, replaces `%ProgramFiles%\Decky Loader`, and starts the new version. Plugins and settings are kept.

**Developer options**

The Developer settings' remote debugging toggle forwards port 8081 on all network interfaces to Steam's debugger, as on Linux, so anyone on your network can reach it without authentication. It is off by default. Decky does not change firewall rules, so Windows may block or ask about incoming connections. The crash screen's "enable SSH" option starts the OpenSSH Server Windows service (`sshd`) if it is already installed (Settings > System > Optional features); Decky never installs it.

**Upgrading from an older Windows build**

Installs made by older builds kept the program in `%ProgramData%\decky\services` and started it through `wscript.exe`, or installed `PluginLoader.exe` with its `_internal` folder in `%ProgramFiles%\Decky Loader`. Running `--install` from a new installer folder replaces both: it stops them, removes `%ProgramData%\decky\services`, `%ProgramData%\decky\tmp` and the old `PluginLoader.exe` and `_internal`, and keeps your plugins and settings.

Plugins in `%USERPROFILE%\homebrew\plugins` from portable mode are not moved to the install. Reinstall them from Decky's store. Plugin settings and data in `%USERPROFILE%\homebrew` are kept. Decky's own settings (plugin order, update branch and similar) live in `%ProgramData%\decky\settings` in installed mode, so they do not carry over from portable mode.

**Uninstall**

Run the installed copy: `"%ProgramFiles%\Decky Loader\python\python.exe" -I "%ProgramFiles%\Decky Loader\app\main.py" --uninstall`, and approve the Windows administrator prompt. This removes the scheduled task and `%ProgramFiles%\Decky Loader` (and any `%ProgramData%\decky\services` or `tmp` left by an older build). Files that are still in use are deleted at the next restart. Plugins and settings in `%ProgramData%\decky` are left in place.

**Portable mode**

Portable mode is the single-file exe: `PluginLoader-win.exe` (x86_64) or `PluginLoader-win-arm64.exe` (ARM). The `-noconsole` variants (`PluginLoader-win-noconsole.exe`, `PluginLoader-win-arm64-noconsole.exe`) do the same without a console window. Run it without any arguments. Plugins that need the `root` flag then run without administrator rights, so some of them will not work fully, and Decky's built-in updater can update it in place. Installed mode is recommended.

**Security notes**

In installed mode, Decky runs with administrator rights. Like Decky on Linux, which runs as root, it accepts commands from local programs on `127.0.0.1`. This means software already running on your PC can use Decky to install plugins that run with administrator rights. If you do not want that, use portable mode instead.

**Known limits**

In installed mode, plugins without the `root` flag run with standard user rights and cannot write inside their own plugin folder (`%ProgramData%\decky\plugins`). They should keep their files in their settings and data folders under `%USERPROFILE%\homebrew`.

Plugins that depend on Linux-specific features (Proton, flatpak, SteamOS-only tools) do not apply on Windows.

**Plugin compatibility on Windows**

Plugins are written for SteamOS. To let more of them load unchanged, Decky adds a few stand-ins inside each plugin's process:

- `fcntl`: `flock` and `lockf` work as whole-file locks. Shared locks (`LOCK_SH`) are exclusive on Windows, and `lockf` locks taken through two different file descriptors conflict even inside one process (on Linux they belong to the process). `fcntl.fcntl` flag calls do nothing, and `ioctl` fails.
- `pwd` and `grp`: describe the current Windows user, with the user profile as home directory. `os.getuid()` and similar return 0 for `root` plugins running with administrator rights and 1000 otherwise. `os.chown` does nothing.
- `/tmp` and `/home/deck`: for plugins without administrator rights, Decky creates `C:\tmp` and links `C:\home\deck` and `C:\home\<your user name>` to your user profile, if those names are free. Existing files and folders are never replaced. Plugins with administrator rights do not get these, and should not use `/tmp` at all, because `C:\tmp` may be owned by a standard user. Use `DECKY_PLUGIN_RUNTIME_DIR` instead.
- Steam paths: for plugins without administrator rights, `~/.steam/root`, `~/.steam/steam` and `~/.local/share/Steam` lead to your Steam install folder, as on SteamOS. Missing `.steam`, `.local` and `.local\share` folders are created as normal folders. A plain folder already in one of those three places is renamed to `<name>.decky-old`; links and files are left alone.
- `chmod` is on the plugin's `PATH` and does nothing. It is a `.cmd` script, found by `shell=True` commands and `shutil.which`.
- Program names in list-form calls: Windows only adds `.exe` to a program name, so `subprocess.run(["chmod", ...])` or `asyncio.create_subprocess_exec("chmod", ...)` would not find `chmod.cmd`. When a plain name (no folder, no extension) is not found that way, Decky looks it up on the call's `PATH`, in `PATHEXT` order. With `env=`, the `PATH` in that environment is searched, as `execvpe` does on Linux. Only folders with a drive letter or a `\\server\share` prefix count; Linux-style entries such as `/usr/bin` are skipped. It accepts `.exe` and `.com` files from any absolute `PATH` folder, but `.cmd` and `.bat` scripts only from Decky's own stand-in folder, because `cmd.exe` would read their arguments as commands. For the same reason a stand-in script is not run when an argument contains `" % ! ^ & | < > ( )` or a control character; the call then fails as it would without Decky. If nothing is found, a few programs that install outside `PATH` are looked for where their installer puts them: `tailscale` in `Program Files\Tailscale` (the real Program Files folder, never `%ProgramFiles%`). Calls with `executable=` are never changed.
- Linux tools: list-form calls of `ps`, `kill`, `pgrep`, `pkill`, `killall`, `pidof`, `timedatectl` and `date` (also as `/usr/bin/...`, `/bin/...` and similar) run Decky's own versions of them, with the plugin's own rights. `ping` does too when its options are Linux ones (`-c`, `-W`, `-q`, `-D`, `-O`); other `ping` calls keep Windows' `ping.exe`. Only the options plugins use are there; anything else fails like bad usage does on Linux.
  - `ps`, `kill` and the others only see processes in your Windows session that belong to your account. `SIGSTOP` (and `TSTP`, `TTIN`, `TTOU`) suspends every thread of a process and `SIGCONT` resumes it; `ps` shows a suspended process as `T`. `SIGINT`, `SIGQUIT`, `SIGTERM` and `SIGKILL` end the process at once, since Windows has no graceful `SIGTERM`. Signals without a Windows equivalent (`HUP`, `USR1`, `USR2`...) fail, and process groups (negative ids) are not supported. Decky itself and the Steam client are never suspended or ended: Decky's interface runs inside Steam, and the `steam` command plugins use to restart it does not exist on Windows.
  - `ping` answers in the format of Linux's `ping` (iputils), so plugins can read it. IPv4 only, and each lost packet costs its full `-W` timeout (4 seconds if not given).
  - `timedatectl show` and `timedatectl status` report the time zone (as a name such as `Europe/Berlin`), whether Windows sets the time automatically (`NTP`), and, as `NTPSynchronized`, whether it does and the Windows Time service is running. `date` supports `+FORMAT` with the GNU format codes, `-u`, `-R` and `-I`; time zone abbreviations print as offsets such as `+08`, because Windows has none. `timedatectl set-time` and `set-ntp` only work for plugins with the `root` flag, which Decky runs as administrator; `set-ntp` turns Settings' "Set time automatically" on or off. For other plugins they fail with "Access denied", and Decky never asks for administrator rights. `date` never changes the clock.
- `sudo`, `su` and `runuser`: in front of a list-form call, Decky drops them when they would not change the user: `sudo -u <your user name>` or `sudo -u deck` in a plugin without the `root` flag, and plain `sudo` or `sudo -u root` in a plugin with it. `su <user> -c '...'` only works for a simple command (no pipes, redirections or variables). Anything else, such as another user, or root in a plugin without administrator rights, fails with an explanation instead of switching users or asking for administrator rights. A `root` plugin's `sudo -u <your user name>` also fails, because Decky can't drop administrator rights for a single command.
- Python scripts: a list-form call whose program is the full path of a Python script (`.py`, or a file whose `#!` line names python) runs on Decky's own Python, as Linux would run it through its `#!` line.
- Commands with `shell=True` (also `os.popen` and `asyncio.create_subprocess_shell`) run through `cmd.exe`, which does not know single quotes. A simple command for one of the Linux tools above (for example `"date '+%Y-%m-%d'"`) runs as a list-form call instead. In other commands, single-quoted parts such as `-subj '/O=KDE Connect'` become double-quoted ones, unless they contain `" % ! ^ & | < > ( )` or a control character, which `cmd.exe` would still interpret; then the command is left as it is.

Not covered: `xdg-open` (a `.cmd` stand-in would run its arguments through `cmd.exe`; use `os.startfile` instead), `/proc`, `/sys`, `/dev`, `systemctl`, `nmcli`, `ip`, D-Bus, flatpak, `pty`/`termios`, Linux signals sent with `os.kill`, Linux programs bundled with a plugin, and shell scripts (`.sh`). Plugins that need these must be ported.

## 🚀 Getting Started

Now that you have Decky Loader installed, you can start using plugins. Each plugin is maintained by a different developer and has its own uses, but most follow a general structure outlined below.

### 📦 Plugins

1. Press the <img src="./docs/images/light/qam.svg#gh-dark-mode-only" height=16><img src="./docs/images/dark/qam.svg#gh-light-mode-only" height=16> button and navigate to the <img src="./docs/images/light/plug.svg#gh-dark-mode-only" height=16><img src="./docs/images/dark/plug.svg#gh-light-mode-only" height=16> icon. This is the Decky menu used for interacting with plugins and the loader itself.
1. Select the <img src="./docs/images/light/store.svg#gh-dark-mode-only" height=16><img src="./docs/images/dark/store.svg#gh-light-mode-only" height=16> icon to open the Plugins Browser. This is where you can find and install plugins.
   - You can also install from URL in the Settings menu. We do not recommend installing plugins from untrusted sources.
1. To install a plugin, select the "Install" button on the plugin you want. You can also select a version from a dropdown menu, but this is not recommended.
1. To update, uninstall, and reload plugins, navigate to the Decky menu and select the <img src="./docs/images/light/gear.svg#gh-dark-mode-only" height=16><img src="./docs/images/dark/gear.svg#gh-light-mode-only" height=16> icon.
   - Keep in mind that uninstalling a plugin will only remove its plugin files, not any other files it may have created.

### 🛠️ Plugin Development

There is no complete plugin development documentation yet. However a good starting point is the [plugin template repository](https://github.com/SteamDeckHomebrew/decky-plugin-template). Consider [joining our Discord](https://deckbrew.xyz/discord) if you have any questions.

### 🤝 Contributing

Please consult [the wiki page regarding development](https://wiki.deckbrew.xyz/en/loader-dev/development) for more information on installing development versions of Decky Loader. You can also install the Steam Deck UI on a Windows or Linux computer for testing by following [this YouTube guide](https://youtu.be/1IAbZte8e7E?t=112).

1. Clone the repository using the latest commit to main before starting your PR.
1. In your clone of the repository, run these commands.
   ```bash
   cd frontend
   pnpm i # NOTE: you may need to approve esbuild's build script with pnpm approve-builds
   pnpm run build
   ```
1. If you are modifying the UI, these commands will need to be run before deploying the changes to your Steam Deck.
1. Use the VS Code tasks or `deck.sh` script to deploy your changes to your Steam Deck to test them.
1. You will be testing your changes with the Python script version. You will need to build, deploy, and reload each time.

⚠️ If you are recieving build errors due to an out of date library, you should run this command inside of your repository.

```bash
pnpm update @decky/ui --latest
```

Source control and deploying plugins are left to each respective contributor for the cloned repos in order to keep dependencies up to date.

## 📜 Credits

The original idea for the plugin loader concept is based on the work of [marios8543's Steam Deck UI Inject project](https://github.com/marios8543/steamdeck-ui-inject).
