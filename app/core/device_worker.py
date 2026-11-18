"""Reuse the application executable for isolated device work in a frozen build."""

import os
import sys


def device_worker_argv(command, *args):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--device-worker", command, *args]
    return [sys.executable, "-B", "-c", command, *args]


def _restore_windows_worker_streams():
    # A windowed PyInstaller executable leaves Python's standard streams unset
    # even when subprocess passes valid Win32 pipe handles to the worker.
    import ctypes
    import msvcrt
    from ctypes.wintypes import DWORD, HANDLE

    get_handle = ctypes.windll.kernel32.GetStdHandle
    get_handle.argtypes = (DWORD,)
    get_handle.restype = HANDLE
    for name, standard_id in (("stdout", -11), ("stderr", -12)):
        if getattr(sys, name) is not None:
            continue
        handle = get_handle(standard_id & 0xffffffff)
        if handle and handle != ctypes.c_void_p(-1).value:
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
            setattr(sys, name, os.fdopen(fd, "w", buffering=1))


def run_device_worker(command, *args):
    if sys.platform == "win32" and (sys.stdout is None or sys.stderr is None):
        _restore_windows_worker_streams()
    # Preserve the same argument layout as Python's -c in source launches.
    sys.argv = ["-c", *args]
    namespace = {"__name__": "__main__"}
    exec(compile(command, "<device worker>", "exec"), namespace)
