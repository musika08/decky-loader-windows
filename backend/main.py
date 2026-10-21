# This file is needed to make the relative imports in decky_loader/ work properly.
if __name__ == "__main__":
    import multiprocessing, sys, time
    multiprocessing.freeze_support()

    def cli_console() -> None:
        # Windowed builds have no stdout; show CLI output in the console we were started from, if any
        from decky_loader.localplatform.winsupervisor import attach_parent_console
        attach_parent_console()

    if sys.platform != "win32" or sys.argv[1:2] == ["--loader"]:
        from decky_loader.main import main
        main()
    elif sys.argv[1:2] == ["--finish-update"] and len(sys.argv) == 4:
        cli_console()
        from decky_loader.localplatform.winsupervisor import finish_update
        sys.exit(finish_update(sys.argv[2], int(sys.argv[3])))
    elif len(sys.argv) == 1:
        from decky_loader.localplatform import winsupervisor
        exe = sys.executable
        winsupervisor.cleanup_stale_update(exe)
        try:
            sys.exit(winsupervisor.run_supervisor(winsupervisor.make_spawn(exe), time.sleep, exe, winsupervisor.launch_detached))
        except KeyboardInterrupt:
            sys.exit(0)
    elif sys.argv[1:2] == ["--install"] and (len(sys.argv) == 2 or (len(sys.argv) == 4 and sys.argv[2] == "--for-sid")):
        cli_console()
        from decky_loader.localplatform import wininstall
        sys.exit(wininstall.install(sys.argv[3] if len(sys.argv) == 4 else None))
    elif sys.argv[1:] == ["--uninstall"]:
        cli_console()
        from decky_loader.localplatform import wininstall
        sys.exit(wininstall.uninstall())
    elif sys.argv[1:2] == ["--plugin-host"] and len(sys.argv) == 3:
        from decky_loader.plugin.plugin_host import main as plugin_host_main
        plugin_host_main(sys.argv[2])
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
