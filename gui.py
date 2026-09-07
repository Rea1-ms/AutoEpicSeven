"""Compatibility launcher for the Alasio backend."""

from alasio.backport.patch import patch_startup

patch_startup()


if __name__ == "__main__":
    import os

    from alasio.backend.backend import BackendWithSupervisor

    supervisor = BackendWithSupervisor().multiprocessing_freeze_support()
    supervisor.run_gui(root=os.path.dirname(os.path.abspath(__file__)))
