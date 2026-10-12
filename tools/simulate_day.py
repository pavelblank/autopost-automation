# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""SIMULATE A WHOLE DAY of the schedule + idle-time stock system - on a scratch database, with a fake clock and
DRY-RUN jobs only. Nothing is generated, nothing is posted, no GPU, no model.

It proves the TRIGGER FLOW: when production starts and stops, the priority order, serving from stock at every
slot, the fallback when stock is empty, no catch-up storm when automation is switched on, and the topic-shortage
trigger (ARJUN first).

  venv\Scripts\python.exe -I tools\simulate_day.py
"""
import datetime as dt
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "videogen"))

import db as DB                       # noqa: E402
import department_queue as Q          # noqa: E402
import paths                          # noqa: E402
import stock as S                     # noqa: E402
import dispatcher as D                # noqa: E402
import research                       # noqa: E402
import ai_health                      # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="simday_"))
paths.OFFICE = _TMP / "OFFICE"
paths.AREAS = {k: _TMP / "WORK" / v.name for k, v in paths.AREAS.items()}
paths.ensure_all = lambda: None
Q.BACKOFF_SCALE = 0
Q.WATCHDOG_GRACE = 10 ** 6
Q._HEAL_FILE = _TMP / "auto_heal.json"
S.STOCK = _TMP / "STOCK"
S.LIST = S.STOCK / "stock.json"
S.MD = S.STOCK / "WAITING-FOR-PUBLISH.md"
D.STATE = _TMP / "dispatcher.json"
D.LOG = _TMP / "dispatcher.log"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("  %-4s %-72s %s" % ("OK" if ok else "FAIL", name, detail))


class FakeDT(dt.datetime):
    _now = dt.datetime(2026, 10, 11, 0, 0)

    @classmethod
    def now(cls, tz=None):
        return cls._now


D.datetime = FakeDT


def fresh(topics=60, stock_on=True):
    p = Path(tempfile.gettempdir()) / "simday.db"
    for suf in ("", "-wal", "-shm"):
        f = Path(str(p) + suf)
        if f.exists():
            try:
                f.unlink()
            except Exception:
                pass
    DB.DB = p
    Q.schema()
    with research.conn() as c:                                   # Windows cannot delete an open db file: clear the tables instead
        for t in ("topics", "department_steps", "department_jobs", "runs", "posts", "topics_used"):
            try:
                c.execute("DELETE FROM " + t)
            except Exception:
                pass
    USED.clear()
    shutil.rmtree(S.STOCK, ignore_errors=True)
    if D.STATE.exists():
        D.STATE.unlink()
    for d in paths.AREAS.values():
        Path(d).mkdir(parents=True, exist_ok=True)
    import itertools
    cons, vow = "bdfgklmnprstvz", "aeiou"
    words = ["".join(w) for w in itertools.product(cons, vow, cons, vow, cons)]      # thousands of distinct pseudo-words
    with research.conn() as c:
        for n in range(topics):
            ws = [words[(n * 4 + k) * 7 % len(words)] for k in range(4)]            # 4 words per topic, none shared between topics
            c.execute("INSERT OR IGNORE INTO topics(title,source,score,hook,found) VALUES(?,?,?,?,?)",
                      ("how %s and %s shape %s with %s" % tuple(ws), "you", 100 - n, "FACT", time.time()))
    cfgd = S.cfg()
    S.cfg = lambda: dict(cfgd, enabled=stock_on)


# ---- patches that make jobs DRY and the world quiet ----
_orig_enqueue = Q.enqueue
GENERATED = []                                         # jobs the dispatcher made itself (no stock available)
PRODUCED = []                                          # (time, kind, minutes-to-next-slot, counts-before)
SERVED = []                                            # (time, slot, kind, style, job)
SCOUTS = []
USED = []


def enq(topic, kind, platforms=None, dry_run=False, notify=False, source="manual"):
    jid = _orig_enqueue(topic, kind, platforms, dry_run=True, notify=False, source=source)
    USED.append(topic)
    if source == "dispatcher":
        GENERATED.append((FakeDT._now, kind, jid))
    return jid


Q.enqueue = enq
Q.vet_topic = lambda t, k="video": (False, "same topic already used") if t in USED else (True, "ok")
D.enabled = lambda: (True, "ok")
ai_health.port_up = lambda port=11434: True
S._probe_ok = lambda media, kind: (True, "")
_orig_register = S.register


def register(job_id):
    it = _orig_register(job_id)
    d = S._load()
    i = S._find(d, job_id)
    if i and i["kind"] != "text":
        f = _TMP / "media" / job_id / "final.mp4"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\0" * 60000)
        i["media"] = str(f)
        S._save(d)
    return it


S.register = register
_orig_produce = S.produce_one


def produce(kind="video", topic_override=None):
    mins, slot = S.next_slot_minutes(FakeDT._now)
    counts = {k: S._count(k) for k in ("video", "image", "text")}
    jid = _orig_produce(kind, topic_override)
    if jid:
        PRODUCED.append((FakeDT._now, kind, mins, counts))
    return jid


S.produce_one = produce
_orig_serve = S.serve


def serve(kind, style, slot=""):
    jid = _orig_serve(kind, style, slot)
    SERVED.append((FakeDT._now, slot, kind, style, jid))
    return jid


S.serve = serve
S.scout_topics = lambda: SCOUTS.append(FakeDT._now)


def step(minutes, until):
    """advance the fake clock; the dispatcher clock runs every 5 min, the supervisor's idle trigger every 10"""
    while FakeDT._now < until:
        FakeDT._now += dt.timedelta(minutes=minutes)
        D.run_once()
        Q.run()
        if FakeDT._now.minute % 10 == 0:
            S.tick(FakeDT._now)
            Q.run()


def hhmm(t):
    return t.strftime("%H:%M")


print("== A. an ordinary day, automation already ON at midnight, empty stock ==")
fresh()
FakeDT._now = dt.datetime(2026, 10, 11, 0, 0)
step(5, dt.datetime(2026, 10, 11, 23, 59))
need = {"video": 120, "image": 30, "text": 20}
check("every production started with enough free time before the next slot", all(m >= need[k] for _, k, m, _c in PRODUCED), "%d productions" % len(PRODUCED))
order = [k for _, k, _m, _c in PRODUCED][:15]
check("overnight order is STRICT: 5 video, then 5 image, then 5 text", order == ["video"] * 5 + ["image"] * 5 + ["text"] * 5, ",".join(o[0] for o in order))
viol = [(hhmm(t), k, c) for t, k, _m, c in PRODUCED if (k == "image" and c["video"] < 5) or (k == "text" and (c["video"] < 5 or c["image"] < 5))]
check("a lower kind NEVER started while a higher kind was below target", not viol, str(viol[:3]))
served = {s[1]: s for s in SERVED if s[4]}
check("all 8 scheduled slots were served FROM STOCK (no generation needed)", len(served) == 8 and not GENERATED, "%d from stock, %d generated" % (len(served), len(GENERATED)))
check("each slot got a post of the right kind", all(any(x[1] == sid and x[2] == kind for x in SERVED if x[4]) for sid, _h, _m, kind, _s, _p in D.SLOTS))
check("the cartoon slot got a cartoon video, the real slot a real one",
      served["video-real"][3] == "real" and served["video-cartoon"][3] == "cartoon")
check("no topic was ever used twice", len(USED) == len(set(USED)), "%d topics" % len(USED))
counts_end = {k: S._count(k) for k in ("video", "image", "text")}
check("stock never exceeds its target and refills during the day", all(v <= 5 for v in counts_end.values()), str(counts_end))
t_small = [(hhmm(t), k) for t, k, _m, _c in PRODUCED if t.hour == 8 and t.minute > 0]
print("     productions per hour:", {h: len([1 for t, *_ in PRODUCED if t.hour == h]) for h in range(24) if any(t.hour == h for t, *_ in PRODUCED)})
sx = S.MD.read_text(encoding="utf-8")
check("the waiting-for-publish list is written and shows the per-kind counts", "video" in sx and "Idle-time priority" in sx)

print("== B. automation switched ON late in the evening: no catch-up storm ==")
fresh()
FakeDT._now = dt.datetime(2026, 10, 11, 23, 30)
GENERATED.clear(); SERVED.clear(); PRODUCED.clear()
skipped = D.arm_today()
check("switching on at 23:30 marks all 8 slots that already passed as done", len(skipped) == 8, str(skipped)[:70])
step(5, dt.datetime(2026, 10, 11, 23, 59))
check("nothing is generated or served in the rest of the evening", not GENERATED and not any(s[4] for s in SERVED))
check("the idle producer starts (next slot is 9 hours away)", len(PRODUCED) >= 1 and PRODUCED[0][1] == "video")

print("== C. short windows: a slot is close -> no video; AND lower kinds must wait ==")
fresh()
FakeDT._now = dt.datetime(2026, 10, 11, 8, 50)
PRODUCED.clear()
D.arm_today()
step(5, dt.datetime(2026, 10, 11, 10, 10))
check("08:50 -> 10:10: the next slot is always < 120 min away, so NO video starts", not [p for p in PRODUCED if p[1] == "video"])
check("and no image/text jumps the queue while videos are still needed", not PRODUCED, str([(hhmm(t), k) for t, k, *_ in PRODUCED][:4]))

print("== D. no topic ready: the WHOLE TEAM starts with ARJUN ==")
fresh(topics=0)
FakeDT._now = dt.datetime(2026, 10, 11, 1, 0)
SCOUTS.clear(); PRODUCED.clear()
S.tick(FakeDT._now)
check("empty topic bank -> ARJUN is triggered to scout, no job is made", len(SCOUTS) == 1 and not PRODUCED, "scouts=%d produced=%d" % (len(SCOUTS), len(PRODUCED)))
with research.conn() as c:
    for n in range(5):
        c.execute("INSERT OR IGNORE INTO topics(title,source,score,hook,found) VALUES(?,?,?,?,?)", ("why %s and %s explain %s near %s" % ("qazuxop%d" % n, "wekubyl%d" % n, "hivojuc%d" % n, "ruvydel%d" % n), "you", 50, "FACT", time.time()))
S.tick(FakeDT._now)
check("after ARJUN has topics the next check produces", len(PRODUCED) == 1)

print("== E. stock empty / switched off at slot time: normal generation ==")
fresh(stock_on=False)
GENERATED.clear(); SERVED.clear()
FakeDT._now = dt.datetime(2026, 10, 11, 8, 40)
step(5, dt.datetime(2026, 10, 11, 8, 50))
check("with no stock the 08:45 slot generates a post itself (nothing is lost)", len(GENERATED) == 1 and GENERATED[0][1] == "image", str([(hhmm(t), k) for t, k, _ in GENERATED]))

print("== F. the monitor cadence ==")
check("the supervisor / idle trigger interval is 10 minutes by default", Q._monitor_every() == 600, str(Q._monitor_every()))

bad = [r for r in RESULTS if not r[1]]
print("\n%d/%d passed" % (len(RESULTS) - len(bad), len(RESULTS)))
sys.exit(1 if bad else 0)
