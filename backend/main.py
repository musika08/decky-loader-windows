# This file is needed to make the relative imports in decky_loader/ work properly.
if __name__ == "__main__":
    import multiprocessing, os, sys, time
    multiprocessing.freeze_support()

    def cli_console() -> None:
        # Windowed builds have no stdout; show CLI output in the console we were started from, if any
        from decky_loader.localplatform.winsupervisor import attach_parent_console
        attach_parent_console()

    frozen = bool(getattr(sys, "frozen", False))
    if sys.platform == "win32" and not frozen:
        from decky_loader.localplatform import wininstall, winsupervisor
        # Our Python runtime (installed, or an installer folder) only runs with -I: its ._pth file pins sys.path, but
        # without -I PYTHON* environment variables (PYTHONINSPECT, PYTHONSTARTUP...) would still apply. Refuse rather
        # than re-exec: a re-exec would leave whatever waits on this process (the task, the supervisor) behind.
        problem = wininstall.isolation_error(sys.executable, os.path.abspath(__file__), list(getattr(sys, "orig_argv", sys.argv)))
        if problem is not None:
            cli_console()
            print(problem, file=sys.stderr)
            sys.exit(2)
        # What PyInstaller's runtime hook sets in a packaged build (DECKY_REPO, DECKY_BRANCH...)
        winsupervisor.apply_build_config(os.path.join(os.path.dirname(os.path.abspath(__file__)), winsupervisor.BUILD_CONFIG))

    if sys.platform != "win32" or sys.argv[1:2] == ["--loader"]:
        from decky_loader.main import main
        main()
    elif sys.argv[1:2] == ["--finish-update"] and len(sys.argv) == 4:
        if not frozen:
            # Only a PyInstaller exe updates itself; from source this would overwrite the Python interpreter
            print("--finish-update only applies to the portable exe", file=sys.stderr)
            sys.exit(2)
        # No cli_console(): closing a console window must not kill the finisher mid-copy
        from decky_loader.localplatform.winsupervisor import finish_update
        sys.exit(finish_update(sys.argv[2], int(sys.argv[3])))
    elif len(sys.argv) == 1:
        from decky_loader.localplatform import winsupervisor
        winsupervisor.setup_logging()
        # Before anything is spawned: two supervisors would fight over the loader's port forever
        if not winsupervisor.acquire_supervisor_lock():
            sys.exit(0)
        exe = sys.executable
        if frozen:
            winsupervisor.cleanup_stale_update(exe)
        try:
            sys.exit(winsupervisor.run_supervisor(winsupervisor.make_spawn(exe), time.sleep, exe, winsupervisor.launch_detached))
        except KeyboardInterrupt:
            sys.exit(0)
    elif sys.argv[1:2] == ["--install"] and (len(sys.argv) == 2 or (len(sys.argv) == 4 and sys.argv[2] == "--for-sid")):
        cli_console()
        from decky_loader.localplatform import wininstall
        sid = sys.argv[3] if len(sys.argv) == 4 else None
        sys.exit(wininstall.run_logged("install", list(getattr(sys, "orig_argv", sys.argv)), lambda: wininstall.install(sid)))
    elif sys.argv[1:] == ["--uninstall"]:
        cli_console()
        from decky_loader.localplatform import wininstall
        sys.exit(wininstall.run_logged("uninstall", list(getattr(sys, "orig_argv", sys.argv)), wininstall.uninstall))
    elif sys.argv[1:2] == ["--plugin-host"] and len(sys.argv) == 3:
        from decky_loader.plugin.plugin_host import main as plugin_host_main
        plugin_host_main(sys.argv[2])
    elif sys.argv[1:2] == ["--compat-tool"] and len(sys.argv) >= 4:
        # A Linux tool (ps, kill, ping...) that a plugin host's subprocess call was redirected to (winplugincompat)
        from decky_loader.localplatform.wincompattools import main as compat_tool_main
        sys.exit(compat_tool_main(sys.argv[2], sys.argv[3], sys.argv[4:]))
    elif sys.argv[1:2] == ["--check-imports"] and len(sys.argv) > 2:
        cli_console()
        import importlib
        missing: list[str] = []
        for mod in sys.argv[2:]:
            try:
                importlib.import_module(mod)
            except Exception:
                missing.append(mod)
        if missing:
            print("missing: " + ", ".join(missing))
        sys.exit(1 if missing else 0)
    else:
        cli_console()
        print(f"Unknown arguments: {sys.argv[1:]}", file=sys.stderr)
        sys.exit(2)
