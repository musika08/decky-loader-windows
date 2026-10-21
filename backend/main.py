# This file is needed to make the relative imports in decky_loader/ work properly.
if __name__ == "__main__":
    import multiprocessing, sys, time
    multiprocessing.freeze_support()

    if sys.platform != "win32" or sys.argv[1:2] == ["--loader"]:
        from decky_loader.main import main
        main()
    elif sys.argv[1:2] == ["--finish-update"] and len(sys.argv) == 4:
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
    else:
        print(f"Unknown arguments: {sys.argv[1:]}", file=sys.stderr)
        sys.exit(2)
