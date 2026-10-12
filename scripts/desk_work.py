# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""SIDE DESK - light background work for agents who have no chain step right now.

WHY: the chain runs ONE step at a time on purpose (an 8 GB GPU cannot hold a video render and a
14B language model together). While NOAH renders for 45+ minutes everyone else used to sit idle,
and the research refresh lived inside the dashboard scheduler where it stalled the heartbeat.

RULES (so the PC never lags):
  * CPU / network only. NO language-model calls, NO GPU. (An LLM next to the render is what made
    "AI enhance" time out and made the guard think Ollama was wedged.)
  * ONE side task at a time, each with a fixed cadence, each recorded in `desk_tasks` so the
    Agent Office shows who is doing what.
  * Every task is wrapped: a failure is recorded and never reaches the chain or the poller.

Owners (named in the office):  ARJUN scouts topics - LEO studies what performed -
HELENA chairs the strategy meeting (with OLIVER, ISABELLA, MAYA).
"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import nowindow  # noqa: F401  (hides every child console window)
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))
import db  # noqa: E402

STATE = ROOT / "data" / "desk_work.json"
NOTES = ROOT / "videogen" / "AGENT_MEMORY" / "strategy_notes.md"
_LOCK = threading.Lock()


def _schema():
    c = db.conn()
    c.execute("""CREATE TABLE IF NOT EXISTS desk_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT NOT NULL, task TEXT NOT NULL,
        title TEXT, status TEXT NOT NULL, started REAL, finished REAL, reason TEXT)""")
    return c


def _load():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st), encoding="utf-8")
    os.replace(tmp, STATE)


# ------------------------------------------------------------------ the tasks
def topic_scout():
    """ARJUN: find fresh topics (trends / YouTube / trend-pulse), skip near-duplicates.
    Same refresh the scheduler used to do, minus the language-model scoring step."""
    os.environ["AUTOPOST_NO_AI_ENHANCE"] = "1"
    import research
    n = research.refresh()
    return "%s new topic(s) found and ranked" % n


READY_TARGET = 10          # ARJUN keeps at least this many audited, ready topics in the bank


def topic_editing():
    """ARJUN's editorial gate: raw headlines -> clean, general, vetted subjects (quality='ok').
    This is how the FIRST agents do the work up front so that nothing is rejected later: junk is
    dropped by cheap rules, the rest is judged and REWRITTEN by the local model while the AI engine is
    idle (run_due() only calls this when no job step is running). Fails closed: if the model cannot be
    reached nothing is approved. Skipped once enough ready topics exist and nothing is waiting."""
    import topic_editor
    ready, waiting = topic_editor.ready_count(), topic_editor.pending_count()
    if ready >= READY_TARGET and waiting == 0:
        return "%d ready topics, nothing waiting - nothing to do" % ready
    out = topic_editor.analyse_pending(6)
    return "%s | ready topics now: %d" % (out, topic_editor.ready_count())


def performance_study():
    """LEO: study what performed (view counts + content mix). Network/DB only."""
    import analytics
    import learn
    learn.refresh_if_due()
    perf = analytics.get_content_performance(7) or {}
    parts = ["%s: %d posts, %.0f avg views" % (k, v.get("count", 0), v.get("avg_views", 0)) for k, v in perf.items()]
    return "last 7 days - " + ("; ".join(parts) if parts else "no data yet")


def strategy_meeting():
    """HELENA chairs (OLIVER, ISABELLA, MAYA attend): look at the day so far and write down what
    is next. Plain counting from the database - no model - appended to the agents' memory."""
    import dispatcher
    now = time.localtime()
    today0 = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, 0, 0, 0, 0, 0, -1))
    c = db.conn()
    made = c.execute("SELECT COUNT(*) FROM final_reports WHERE ts>?", (today0,)).fetchone()[0]
    ok = c.execute("SELECT COUNT(*) FROM final_reports WHERE ts>? AND status LIKE 'PUBLISHED AND VERIFIED%'",
                   (today0,)).fetchone()[0]
    dead = c.execute("SELECT COUNT(DISTINCT job_id) FROM department_steps WHERE status='dead'").fetchone()[0]
    unused = c.execute("SELECT COUNT(*) FROM topics WHERE used=0").fetchone()[0] if _has(c, "topics") else -1
    later = [s for s in dispatcher.SLOTS if (s[1], s[2]) > (now.tm_hour, now.tm_min)]
    nxt = ("%s at %s (%s)" % (later[0][0], time.strftime("%I:%M %p", (2000, 1, 1, later[0][1], later[0][2], 0, 0, 1, -1)).lstrip("0"),
                              later[0][3])) if later else "nothing more today"
    line = ("[%s] STRATEGY MEETING - chair HELENA; present OLIVER, ISABELLA, MAYA. Today: %d report(s), %d verified. "
            "Jobs stuck (dead) overall: %d. Unused topics in the bank: %s. Next slot: %s."
            % (time.strftime("%Y-%m-%d %I:%M %p"), made, ok, dead, unused if unused >= 0 else "n/a", nxt))
    NOTES.parent.mkdir(parents=True, exist_ok=True)
    with NOTES.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    return "met; next slot: " + nxt


def _has(c, table):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone())


# agent key, task key, title shown in the office, cadence in seconds, function
TASKS = [
    ("arjun", "topic_scout", "scouting fresh topics", 300, topic_scout),
    ("arjun", "topic_editing", "editing topics into ready subjects", 120, topic_editing),
    ("leo", "performance_study", "studying what performed", 7200, performance_study),
    ("helena", "strategy_meeting", "chairing the strategy meeting", 10800, strategy_meeting),
]


def run_due(now=None):
    """Run at most ONE due side task. Safe to call every few seconds; never raises."""
    if not _LOCK.acquire(blocking=False):
        return None
    try:
        now = now or time.time()
        # the owner's rule: side work NEVER hampers the process flow. While any job step (the AI engine)
        # is working, ARJUN and the others stand down; they continue only when everything is idle.
        try:
            import ai_priority
            if ai_priority.chain_busy() or db.generation_busy():
                return None
        except Exception:
            pass
        st = _load()
        for agent, key, title, every, fn in TASKS:
            if now - float(st.get(key, 0)) < every:
                continue
            c = _schema()
            cur = c.execute("INSERT INTO desk_tasks(agent,task,title,status,started) VALUES(?,?,?,?,?)",
                            (agent, key, title, "running", time.time()))
            rid = cur.lastrowid
            try:
                out = str(fn())[:300]
                c.execute("UPDATE desk_tasks SET status='done', finished=?, reason=? WHERE id=?", (time.time(), out, rid))
            except Exception as e:
                out = "failed: %s" % str(e)[:200]
                c.execute("UPDATE desk_tasks SET status='failed', finished=?, reason=? WHERE id=?", (time.time(), out, rid))
            st[key] = time.time()          # a failed task waits a full cadence too - no hammering
            _save(st)
            return (agent, key, out)
        return None
    except Exception:
        return None
    finally:
        _LOCK.release()


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else None
    if which:
        for agent, key, title, every, fn in TASKS:
            if key == which:
                print(agent, key, "->", fn())
    else:
        print(run_due())
