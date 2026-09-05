import sys

if "--task-watcher" in sys.argv[1:]:
    from .servicios.tareas.vigilancia import run_task_watcher

    raise SystemExit(run_task_watcher())

from .app import main

if __name__ == "__main__":
    main()
