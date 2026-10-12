# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""Shared exception types for the department. One module so the engine and the agents
see the SAME classes even when the engine is started as __main__."""


class Fatal(Exception):
    """A deterministic rejection that retrying cannot fix -> dead-letter at once."""


class Reject(Exception):
    """An agent rejects the work. The engine retries within budget, then returns it to the
    earliest responsible specialist (see department_queue.FIRST_RESPONSIBLE) - or to `owner`
    when the rejecting agent names the exact agent that must fix it."""

    def __init__(self, msg="", owner=None):
        super().__init__(msg)
        self.owner = owner


class Wait(Exception):
    """Not a failure: the step must wait for something outside the department (for example a
    human approval). The engine re-arms it after `seconds` WITHOUT spending an attempt."""

    def __init__(self, reason, seconds=60):
        super().__init__(reason)
        self.reason = reason
        self.seconds = seconds
