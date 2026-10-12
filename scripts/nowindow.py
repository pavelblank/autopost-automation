# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""Import this first in any long-running process (dashboard, engine, guard, dispatcher...).

On Windows a process without a console (pythonw, a scheduled task) opens a visible console window for EVERY
child process it starts (tasklist, powershell, ffmpeg...). With checks running every few seconds that is a
cmd window flashing on screen all day. This makes every child hidden (CREATE_NO_WINDOW) unless the caller
asked for its own console flags, and gives a spawn-free "is this pid alive" check.
"""
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_CONSOLE = 0x00000010

if sys.platform == "win32" and not getattr(subprocess.Popen, "_nowindow", False):
    _orig_init = subprocess.Popen.__init__

    def _init(self, *a, **k):
        fl = k.get("creationflags", 0) or 0
        if not (fl & (DETACHED_PROCESS | CREATE_NEW_CONSOLE | CREATE_NO_WINDOW)):
            k["creationflags"] = fl | CREATE_NO_WINDOW
        _orig_init(self, *a, **k)

    subprocess.Popen.__init__ = _init
    subprocess.Popen._nowindow = True


def pid_alive(pid):
    """True if the process exists. No child process, no window."""
    try:
        pid = int(pid)
    except Exception:
        return False
    if sys.platform != "win32":
        import os
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False
    import ctypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return k.GetLastError() == 5                 # access denied = it exists (elevated)
    code = ctypes.c_ulong()
    ok = k.GetExitCodeProcess(h, ctypes.byref(code))
    k.CloseHandle(h)
    return bool(ok) and code.value == 259            # STILL_ACTIVE
