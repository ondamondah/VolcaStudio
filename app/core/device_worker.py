"""Reuse the application executable for isolated device work in a frozen build."""

import sys


def device_worker_argv(command, *args):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--device-worker", command, *args]
    return [sys.executable, "-B", "-c", command, *args]


def run_device_worker(command, *args):
    # Preserve the same argument layout as Python's -c in source launches.
    sys.argv = ["-c", *args]
    namespace = {"__name__": "__main__"}
    exec(compile(command, "<device worker>", "exec"), namespace)
