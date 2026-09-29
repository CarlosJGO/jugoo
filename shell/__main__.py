import ctypes
import sys


def set_process_name(name: str) -> None:
    libc = ctypes.CDLL(None)
    libc.prctl(15, name.encode(), 0, 0, 0)


set_process_name("jugoo")


if "--task-watcher" in sys.argv[1:]:
    from .servicios.tareas.vigilancia import run_task_watcher

    raise SystemExit(run_task_watcher())

from .app import main

#Jugoo
if __name__ == "__main__":
    main()