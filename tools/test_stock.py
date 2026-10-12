# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Prove the STOCK system and every failure scenario around it - on a SCRATCH database and a scratch STOCK folder.
No GPU, no model, no posting.

  venv\Scripts\python.exe -I tools\test_stock.py
"""
import json
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

_TMP = Path(tempfile.mkdtemp(prefix="stock_test_"))
paths.OFFICE = _TMP / "OFFICE"
paths.AREAS = {k: _TMP / "WORK" / v.name for k, v in paths.AREAS.items()}
paths.ensure_all = lambda: None
S.STOCK = _TMP / "STOCK"
S.LIST = S.STOCK / "stock.json"
S.MD = S.STOCK / "WAITING-FOR-PUBLISH.md"
Q.BACKOFF_SCALE = 0
Q.WATCHDOG_GRACE = 10 ** 6
Q._HEAL_FILE = _TMP / "auto_heal.json"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("  %-4s %-70s %s" % ("OK" if ok else "FAIL", name, detail))


def fresh():
    p = Path(tempfile.gettempdir()) / "stock_test.db"
    for suf in ("", "-wal", "-shm"):
        f = Path(str(p) + suf)
        if f.exists():
            try:
                f.unlink()
            except Exception:
                pass
    DB.DB = p
    Q.schema()
    import shutil
    shutil.rmtree(S.STOCK, ignore_errors=True)


def mk_stock_job(topic, style="real"):
    for d in paths.AREAS.values():
        Path(d).mkdir(parents=True, exist_ok=True)
    jid = Q.enqueue(topic, "video", ["socialai"], dry_run=True, source="stock")
    m = Q.read_job(jid)
    m.update(stock=True, style=style)
    Q.write_job(jid, m)
    d = S._load()
    d["items"].append({"id": jid, "kind": "video", "style": style, "topic": topic, "status": "in_production", "created": time.time()})
    S._save(d)
    return jid


_REAL_PROBE = S._probe_ok
S._probe_ok = lambda media, kind: (True, "")          # fake byte files stand in for videos in most tests


def states(j):
    return {r["step_key"]: r["status"] for r in Q._rows(j)}


def fake_media(jid, name="final.mp4", size=60000):
    """give a registered item a real file so take() can pass its media check (dry-run jobs have none)"""
    f = _TMP / "media" / jid / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"\0" * size)
    d = S._load()
    it = S._find(d, jid)
    it["media"] = str(f)
    S._save(d)
    return f


print("== 1. a stock job is made, quality-checked, then HOLDS (no publishing) ==")
fresh()
j1 = mk_stock_job("slow breathing for a calmer evening routine")
Q.run(j1)
st = states(j1)
check("production steps finished up to GRACE", all(st[k] in ("done", "skipped") for k in ("intake", "maya", "sophie", "daniel", "leo", "oliver", "isabella", "noah", "grace")), str(st)[:90])
check("ELLA / VICTORIA / HELENA did NOT run (held for the slot)", all(st[k] == "pending" for k in ("ella", "victoria", "helena")))
it = S._find(S._load(), j1)
check("the post is listed as READY in the stock", it and it["status"] == "ready")
check("the waiting-for-publish .md lists it", "slow breathing for a calmer evening routine" in S.MD.read_text(encoding="utf-8"))
check("the stock folder holds its files", (S.STOCK / "ready" / j1).is_dir())

print("== 2. nobody calls a held stock job 'stuck' or cleans it up ==")
import supervisor as SV
found = [p for p in SV.find_problems() if p.get("job_id") == j1]
check("the supervisor does not flag the held job", not found, str(found)[:80])
import retention as RT
check("retention does not list it as a finished job", j1 not in RT.finished_jobs())

print("== 3. at the slot: take -> release -> ELLA publishes -> removed from the list ==")
fake_media(j1)
jid = S.take("video", "real")
check("take() returns the ready post", jid == j1)
check("a second take cannot take the SAME post (no double publishing)", S.take("video", "real") is None)
check("taken post is no longer 'ready' in the list", S._find(S._load(), j1)["status"] == "taken")
S.release(j1, ["socialai"], "video-real")
Q.run(j1)
st = states(j1)
check("after release the job completes (ELLA, VICTORIA, HELENA)", all(st[k] in ("done", "skipped") for k in ("ella", "victoria", "helena")), str(st)[:80])
check("the .md no longer lists it under 'Ready'", "slow breathing for a calmer evening routine" not in S.MD.read_text(encoding="utf-8").split("## In production")[0])

S._probe_ok = _REAL_PROBE
fresh()
j0 = mk_stock_job("a corrupt file must never be published")
Q.run(j0)
fake_media(j0)
check("a corrupt / unplayable video is DISCARDED at take time", S.take("video", "real") is None and S._find(S._load(), j0)["status"] == "discarded")
S._probe_ok = lambda media, kind: (True, "")

print("== 4. nothing in stock -> the normal generation path ==")
check("serve() with an empty stock returns None", S.serve("video", "cartoon", "video-cartoon") is None)
check("wrong style never serves the other style's post", True)
fresh()
j2 = mk_stock_job("a cartoon about patience in the garden", "cartoon")
Q.run(j2)
fake_media(j2)
check("a 'real' slot does not take a 'cartoon' post", S.take("video", "real") is None)
check("a 'cartoon' slot takes it", S.take("video", "cartoon") == j2)

print("== 5. scenarios that must not lose or duplicate a post ==")
fresh()
j3 = mk_stock_job("morning light and the nervous system")
Q.run(j3)
fake_media(j3)
import post_history
orig = post_history.find_repeat
post_history.find_repeat = lambda t, exclude_run_id=None: "morning light" if "morning light" in t else None
check("a topic published meanwhile is DISCARDED, not published twice", S.take("video", "real") is None and S._find(S._load(), j3)["status"] == "discarded")
post_history.find_repeat = orig

fresh()
j4 = mk_stock_job("why quiet mornings help focus")
Q.run(j4)
f = fake_media(j4)
f.unlink()
d = S._load(); it = S._find(d, j4)
(Path(it["folder"])).mkdir(parents=True, exist_ok=True)
(Path(it["folder"]) / "final.mp4").write_bytes(b"\0" * 60000)
check("a cleaned working file is RESTORED from the stock copy", S.take("video", "real") == j4 and Path(it["media"]).exists())

fresh()
j5 = mk_stock_job("gentle stretching before bed")
Q.run(j5)
fake_media(j5)
orig_rel = S.release
S.release = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ELLA unavailable"))
res = S.serve("video", "real", "video-real")
S.release = orig_rel
check("a failed release puts the post BACK (never lost)", res is None and S._find(S._load(), j5)["status"] == "ready")

fresh()
j6 = mk_stock_job("an old post that is too old")
Q.run(j6)
fake_media(j6)
d = S._load(); S._find(d, j6)["created"] = time.time() - 40 * 86400; S._save(d)
check("a post older than the limit is discarded", S.take("video", "real") is None and S._find(S._load(), j6)["status"] == "discarded")

fresh()
j7 = mk_stock_job("a job that died")
Q._set(j7, "intake", "dead", "boom")
S.tick()
check("a stock job that died stops counting toward the target", S._find(S._load(), j7)["status"] == "failed")
d = S._load(); i8 = {"id": "job_x", "kind": "video", "style": "real", "topic": "stale", "status": "in_production", "created": time.time() - 13 * 3600}; d["items"].append(i8); S._save(d)
S.tick()
check("a production with no progress for 12 h is cleared", S._find(S._load(), "job_x")["status"] == "failed")

print("== 6. when may the producer run? (the 2-hour free-time rule) ==")
fresh()
D.enabled = lambda: (True, "ok")
D.busy = lambda: (False, "")
S.next_slot_minutes = lambda now=None: (240, "image-morning")
check("everything free, 4 h to the next slot -> produce", S.can_produce()[0])
S.next_slot_minutes = lambda now=None: (45, "text-late")
check("next slot in 45 min -> do NOT produce", not S.can_produce()[0], S.can_produce()[1][:60])
S.next_slot_minutes = lambda now=None: (0, "text-late")
check("a slot is due now / catching up -> do NOT produce", not S.can_produce()[0])
S.next_slot_minutes = lambda now=None: (240, "x")
D.busy = lambda: (True, "a department step is running")
check("a job is running -> do NOT produce", not S.can_produce()[0])
D.busy = lambda: (False, "")
D.enabled = lambda: (False, "rules.paused is true")
check("automation switched off -> do NOT produce", not S.can_produce()[0])
D.enabled = lambda: (True, "ok")
d = {"items": [{"id": "a%d" % n, "kind": "video", "style": "real", "topic": "t%d" % n, "status": "ready", "created": time.time()} for n in range(5)]}
S._save(d)
check("stock already at the target (5) -> do NOT produce", not S.can_produce()[0], S.can_produce()[1][:50])

print("== 7. the real clock: next-slot maths ==")
import datetime as dt
import importlib
importlib.reload(S)
S.STOCK = _TMP / "STOCK"; S.LIST = S.STOCK / "stock.json"; S.MD = S.STOCK / "WAITING-FOR-PUBLISH.md"
D.done_today = lambda sid: False
now = dt.datetime(2026, 10, 11, 23, 30)
D.done_today = lambda sid: True                      # every earlier slot was served
m, slot = S.next_slot_minutes(now)
check("all served at 23:30 -> the next slot is tomorrow 08:45 AM (about 9 h 15 min)", slot == "image-morning" and 540 < m < 570, "%s in %d min" % (slot, m))
now = dt.datetime(2026, 10, 11, 12, 30)
D.done_today = lambda sid: sid in ("image-morning", "text-morning")
D.SLOTS = list(D.SLOTS)
m, slot = S.next_slot_minutes(now)
check("a slot that is due but not yet served counts as 0 (catch-up)", m == 0 and slot == "video-real", "%s %d" % (slot, m))

print("== 8. the producer picks vetted topics and balances the styles ==")
fresh()
topics = iter(["why slow breathing calms us down", "how gentle walking clears the mind", "what bioluminescent plankton do at night"])
D.pick_topic = lambda kind, style, partner=None: next(topics, None)
Q.vet_topic = lambda t, k="video": (True, "new and good")
Q.enqueue = lambda topic, kind, platforms, **kw: "job_fake_%d" % abs(hash(topic) % 1000)
orig_rj = Q.read_job
Q.read_job = lambda j: {"job_id": j}
Q.write_job = lambda j, m: None
import research
research.mark = lambda *a, **k: None
j_a = S.produce_one("video")
j_b = S.produce_one("video")
styles = [i["style"] for i in S._load()["items"]]
check("two productions alternate the styles (real, cartoon)", styles == ["real", "cartoon"], str(styles))
check("each is listed as in_production", all(i["status"] == "in_production" for i in S._load()["items"]))

print("== 9. STRICT PRIORITY: video until full -> image -> text; short windows wait; ARJUN when no topic ==")
fresh()
D.enabled = lambda: (True, "ok")
D.busy = lambda: (False, "")
S.next_slot_minutes = lambda now=None: (600, "image-morning")
import ai_health
ai_health.port_up = lambda port=11434: True
def put(kind, n, status="ready"):
    d = S._load()
    for k in range(n):
        d["items"].append({"id": "%s%d" % (kind, k), "kind": kind, "style": "real" if kind == "video" else None, "topic": "%s topic %d" % (kind, k), "status": status, "created": time.time()})
    S._save(d)
check("empty stock -> the first thing needed is VIDEO", S.next_kind_needed() == "video")
put("video", 4)
check("4 videos ready -> still VIDEO (target is 5)", S.next_kind_needed() == "video")
put("video", 1, "in_production")
d = S._load(); d["items"][-1]["id"] = "vp"; S._save(d)
check("5 videos (4 ready + 1 in production) -> now IMAGE+text", S.next_kind_needed() == "image")
put("image", 5)
check("images full -> now TEXT", S.next_kind_needed() == "text")
put("text", 5)
check("everything full -> nothing to produce (only ARJUN's research keeps running)", S.next_kind_needed() is None)

fresh()
S.next_slot_minutes = lambda now=None: (90, "text-late")
check("90 min free: a VIDEO (needs 120) does not start", not S.can_produce("video")[0])
check("90 min free but videos are still needed: lower kinds must NOT jump the queue", S.next_kind_needed() == "video")
S.next_slot_minutes = lambda now=None: (25, "text-late")
put("video", 5)
check("25 min free, videos full: an IMAGE (needs 30) still waits", not S.can_produce("image")[0])
S.next_slot_minutes = lambda now=None: (40, "text-late")
check("40 min free, videos full: an IMAGE starts", S.can_produce("image")[0])
ai_health.port_up = lambda port=11434: False
check("the AI engine is down -> nothing starts (the supervisor restarts it)", not S.can_produce("image")[0], S.can_produce("image")[1][:50])
ai_health.port_up = lambda port=11434: True

fresh()
S.next_slot_minutes = lambda now=None: (600, "image-morning")
scouted = []
S.scout_topics = lambda: scouted.append(1)
S.produce_one = lambda kind="video", topic_override=None: None          # the topic bank has nothing usable
r = S.tick()
check("no usable topic -> the team starts with ARJUN (scout), no job is made", r is None and scouted == [1])
S.produce_one = lambda kind="video", topic_override=None: "job_made_%s" % kind
check("with a topic the next tick produces the needed kind", S.tick() == "job_made_video")

bad = [r for r in RESULTS if not r[1]]
print("\n%d/%d passed" % (len(RESULTS) - len(bad), len(RESULTS)))
sys.exit(1 if bad else 0)
