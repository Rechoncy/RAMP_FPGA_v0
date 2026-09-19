"""Windows executable entry point; also dispatches the isolated Vivado worker."""
import multiprocessing
import sys


def run():
    multiprocessing.freeze_support()
    if "--build-worker" in sys.argv or "--flash-worker" in sys.argv:
        flash_worker="--flash-worker" in sys.argv
        sys.argv.remove("--flash-worker" if flash_worker else "--build-worker")
        index = sys.argv.index("--worker-log")
        log = sys.argv[index + 1]
        del sys.argv[index:index + 2]
        with open(log, "w", encoding="utf-8", buffering=1) as stream:
            sys.stdout = sys.stderr = stream
            try:
                if flash_worker:
                    from host.flash_worker import main
                else:
                    from host.build_worker import main
                return main()
            except Exception:
                import traceback
                traceback.print_exc()
                return 1
    if "--self-test" in sys.argv:
        from host.portable_check import main
        return main(sys.argv[sys.argv.index("--self-test") + 1])
    from host.app import main
    main()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except Exception:
        import traceback
        from host.runtime import data_dir
        message = traceback.format_exc()
        try:
            log = data_dir() / "startup-error.log"
            log.write_text(message, encoding="utf-8")
            message = f"Ramp Studio 启动失败。诊断日志：{log}\n\n{message[-1500:]}"
        except Exception:
            pass
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Ramp Studio", 0x10)
        elif sys.stderr:
            print(message, file=sys.stderr)
        sys.exit(1)
