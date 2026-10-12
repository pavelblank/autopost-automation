# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""ONE PROCESS PER ROLE - a hard stop against running the poller or dashboard twice.

WHY THIS EXISTS. Two copies of department_queue.py were running at the same time.
The dispatcher writes its "this slot already fired today" state to a file, so a second
poller re-reading that file a moment later can decide the slot is still free and
enqueue the same post a second time. The claim file alone cannot prevent that race -
it can only make each individual write atomic. The fix is to make sure there is only
one process racing in the first place.

HOW. A Windows named mutex, owned by the process. The kernel releases it automatically
when the process dies - including a kill, a crash, or a power cut - so unlike a PID
file or a lock file there is never a stale lock left behind to clean up by hand.

If the mutex is already held, this process says so and exits. It does not try to take
over, and it does not kill the other one: the running instance is healthy and the owner
should not lose his dashboard because something started it twice.
"""
import ctypes
import sys

ERROR_ALREADY_EXISTS = 183

# Kernel32 handle for the mutex. Held for the life of the process.
_handles = {}


def acquire(name):
    """Try to become the only instance of `name`.

    Returns True if we are it. Returns False if somebody else already holds it.
    Never raises - a failure to lock must not be mistaken for permission to run a
    duplicate, so anything unexpected is treated as "already running".
    """
    try:
        k32 = ctypes.windll.kernel32
        handle = k32.CreateMutexW(None, False, name)
        if not handle:
            return False
        already = k32.GetLastError() == ERROR_ALREADY_EXISTS
        if already:
            k32.CloseHandle(handle)          # do not hold the handle we did not win
            return False
        _handles[name] = handle              # keep it alive for the process lifetime
        return True
    except Exception:
        return False


def guard(name, what):
    """Acquire the lock or exit with a clear message.

    `what` is the human name of the thing, e.g. "the AutoPost poller".
    """
    if acquire(name):
        return True
    sys.stderr.write(
        "[%s] already running - this copy will exit.\n"
        "            If you meant to restart it, stop the existing one first.\n" % what)
    sys.stderr.flush()
    sys.exit(3)