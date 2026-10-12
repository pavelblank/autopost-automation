# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""The anti-stuck suite now lives in tools/test_department.py (48+ checks on a scratch database:
retry/backoff, finite correction loops, dead-letter + HELENA report, hard timeout, dead
heartbeat, pid-reuse-safe reaper, orphan recovery, gates, lease, model failover, wait, ...).
This file is kept so the old command still works. The previous 16-test version (12/16: its
4 failures were scenarios sharing one database) is archived in
backups/pre-dept-rewrite-20261010/test_antistuck_OLD.py."""
import runpy
import sys
from pathlib import Path

sys.exit(runpy.run_path(str(Path(__file__).with_name("test_department.py")), run_name="__main__") or 0)
