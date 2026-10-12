# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""STOCK - posts made in idle time, published later at their scheduled time.

the owner's idea (2026-10-10): when nothing is scheduled for the next 2 hours and nothing is running, use the idle
GPU/agents to PRE-MAKE posts (videos first) - fully finished and quality-checked but NOT published. They wait in
STOCK/ with a list (WAITING-FOR-PUBLISH.md). At each scheduled slot the clock asks the stock first: if a ready post
of that kind exists it is released to ELLA and published within seconds; the topic leaves the waiting list so it is
never picked again. If the stock is empty the normal generation runs as before.

How it fits the existing chain (nothing is duplicated):
  * a stock job is an ordinary job (meta stock=True) that runs NADIA .. GRACE and then HOLDS: department_queue.
    trigger_next does not fire ELLA, it calls register() instead.
  * release() flips ELLA to READY with the slot's platform list; ELLA -> VICTORIA -> HELENA run as usual
    (VICTORIA verifies every platform, HELENA sends the final Telegram and records the publication history).
  * the producer only starts when the system is ON, nothing runs, the GPU lease is free and the next scheduled
    slot is at least `min_free_minutes` away (default 120).

Files:  STOCK/stock.json (machine list), STOCK/WAITING-FOR-PUBLISH.md (human list), STOCK/ready/<job_id>/ (copies).
"""
import os as _os, sys as _sys; _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import nowindow  # noqa: F401  (hides every child console window)
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "videogen"))

STOCK = ROOT / "STOCK"
LIST = STOCK / "stock.json"
MD = STOCK / "WAITING-FOR-PUBLISH.md"
DEFAULTS = {"enabled": True, "min_free_minutes": {"video": 120, "image": 30, "text": 20},
            "target": {"video": 5, "image": 5, "text": 5}, "priority": ["video", "image", "text"], "max_age_days": 14,
            "styles": ["real", "cartoon"]}


def _log(m):
    try:
        print("[%s] [stock] %s" % (time.strftime("%I:%M:%S %p"), m), flush=True)
    except Exception:
        pass


def cfg():
    try:
        r = json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8-sig"))
    except Exception:
        r = {}
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    user = r.get("stock") or {}
    for k, v in user.items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def _min_free(kind):
    m = cfg()["min_free_minutes"]
    return int(m.get(kind, 120)) if isinstance(m, dict) else int(m)


class _Lock:
    """Tiny cross-process lock: two engines (poller + a manual command) can never take the same post."""
    def __enter__(self):
        STOCK.mkdir(parents=True, exist_ok=True)
        self.f = STOCK / ".lock"
        t0 = time.time()
        while True:
            try:
                fd = __import__("os").open(str(self.f), __import__("os").O_CREAT | __import__("os").O_EXCL)
                __import__("os").close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.f.stat().st_mtime > 60:     # a crashed holder never blocks us
                        self.f.unlink()
                        continue
                except Exception:
                    pass
                if time.time() - t0 > 20:
                    return self
                time.sleep(0.2)

    def __exit__(self, *a):
        try:
            self.f.unlink()
        except Exception:
            pass


def _load():
    try:
        return json.loads(LIST.read_text(encoding="utf-8"))
    except Exception:
        return {"items": []}


def _save(d):
    STOCK.mkdir(parents=True, exist_ok=True)
    d["updated"] = time.time()
    tmp = LIST.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(LIST)
    write_md(d)


def _find(d, job_id):
    return next((i for i in d["items"] if i["id"] == job_id), None)


def items(status=None, kind=None):
    return [i for i in _load()["items"] if (status is None or i["status"] == status) and (kind is None or i["kind"] == kind)]


def is_stocked(job_id):
    """True while the job is held in stock (made, not yet released) - the supervisor must not call it stuck."""
    i = _find(_load(), job_id)
    return bool(i and i["status"] in ("in_production", "ready"))


def should_hold(job_id):
    try:
        import department_queue as Q
        m = Q.read_job(job_id) or {}
        return bool(m.get("stock")) and not m.get("stock_released")
    except Exception:
        return False


# ------------------------------------------------------------------ the list for humans
def write_md(d=None):
    d = d or _load()
    now = time.time()
    ready = [i for i in d["items"] if i["status"] == "ready"]
    prod = [i for i in d["items"] if i["status"] == "in_production"]
    recent = sorted([i for i in d["items"] if i["status"] in ("taken", "discarded", "failed")],
                    key=lambda i: i.get("taken_at") or i.get("created") or 0, reverse=True)[:10]
    t = lambda ts: time.strftime("%b %d %I:%M %p", time.localtime(ts)) if ts else "-"
    nxt = "?"
    try:
        nxt = "%d min" % next_slot_minutes()[0]
    except Exception:
        pass
    per = ", ".join("%s %d/%d" % (k, len([i for i in ready if i["kind"] == k]), int(cfg()["target"].get(k, 0))) for k in cfg()["priority"])
    L = ["# WAITING FOR PUBLISH (stock of finished posts)", "",
         "Updated %s. Ready: **%d** (%s). In production: %d. Next scheduled slot in: %s." %
         (t(now), len(ready), per, len(prod), nxt), "",
         "Idle-time priority: 1 video (until %d ready) -> 2 image+text -> 3 text -> ARJUN keeps research and ready topics." % int(cfg()["target"].get("video", 5)), "",
         "These posts are finished and quality-checked but NOT published. At each scheduled slot the publisher takes the "
         "oldest matching one, publishes it, and removes it from this list (so the topic is never picked again).", "",
         "## Ready to publish", ""]
    cats = (("VIDEO (photoreal + cartoon)", "video"), ("IMAGE + TEXT", "image"), ("TEXT only", "text"))
    for title, kind in cats:
        mine = sorted([i for i in ready if i["kind"] == kind], key=lambda i: i["created"])
        L += ["### %s - %d of %d ready" % (title, len(mine), int(cfg()["target"].get(kind, 0))), ""]
        if mine:
            L += ["| # | Style | Topic | Hook | Made | Folder |", "|---|---|---|---|---|---|"]
            for n, i in enumerate(mine, 1):
                L.append("| %d | %s | %s | %s | %s | `STOCK/ready/%s` |" % (
                    n, i.get("style") or "-", i["topic"][:70], (i.get("hook") or "")[:90].replace("|", "/"), t(i["created"]), i["id"]))
        else:
            L.append("_nothing ready yet_")
        L.append("")
    L += ["", "## In production", ""]
    L += ["- %s (%s %s) started %s" % (i["topic"][:70], i["kind"], i.get("style") or "", t(i["created"])) for i in prod] or ["_none_"]
    L += ["", "## Recently taken / discarded", ""]
    L += ["- %s - %s - %s%s" % (t(i.get("taken_at") or i.get("created")), i["status"], i["topic"][:70],
                               (" (%s)" % i["note"]) if i.get("note") else "") for i in recent] or ["_none_"]
    STOCK.mkdir(parents=True, exist_ok=True)
    MD.write_text("\n".join(L) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ when may we produce?
def next_slot_minutes(now=None):
    """Minutes until the next scheduled slot that still has to be served. A slot that is due today and not yet
    served counts as 0 (it is about to run), so nothing is produced while a catch-up slot is pending."""
    import datetime as dt
    import dispatcher as D
    now = now or dt.datetime.now()
    best, which = None, None
    for slot_id, hh, mm, kind, style, _p in D.SLOTS:
        at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if at <= now:
            try:
                if not D.done_today(slot_id) and (now - at).total_seconds() <= 8 * 3600:
                    return 0, slot_id
            except Exception:
                pass
            at = at + dt.timedelta(days=1)
        mins = int((at - now).total_seconds() // 60)
        if best is None or mins < best:
            best, which = mins, slot_id
    return best, which


def _count(kind):
    return len([i for i in _load()["items"] if i["status"] in ("ready", "in_production") and i["kind"] == kind])


def can_produce(kind="video", now=None):
    c = cfg()
    if not c.get("enabled"):
        return False, "stock is switched off"
    import dispatcher as D
    ok, why = D.enabled()
    if not ok:
        return False, "automation is off (%s)" % why
    try:
        import ai_health
        if not ai_health.port_up(11434):
            return False, "the AI engine (Ollama) is not running - the supervisor restarts it"
    except Exception:
        pass
    b, bwhy = D.busy()
    if b:
        return False, "busy: %s" % bwhy
    try:
        import db
        if db.generation_busy():
            return False, "the GPU lease is held"
    except Exception:
        pass
    mins, slot = next_slot_minutes(now)
    need = _min_free(kind)
    if mins < need:
        return False, "next slot (%s) is in %d min, less than the %d min a %s needs" % (slot, mins, need, kind)
    have = _count(kind)
    if have >= int(c["target"].get(kind, 0)):
        return False, "%s stock is full (%d)" % (kind, have)
    return True, "free for %d min until %s" % (mins, slot)


def next_kind_needed():
    """STRICT priority (the owner): videos first until their stock is full, then image+text, then text.
    Returns the first kind whose stock is below target - lower priorities wait until it is full."""
    c = cfg()
    for kind in c["priority"]:
        if _count(kind) < int(c["target"].get(kind, 0)):
            return kind
    return None


def _topic_id(title):
    try:
        import db
        r = db.conn().execute("SELECT id FROM topics WHERE title=?", (title,)).fetchone()
        return r[0] if r else None
    except Exception:
        return None


def produce_one(kind="video", topic_override=None):
    """Pick a vetted topic, reserve it, enqueue a STOCK job. Returns the job id or None."""
    import dispatcher as D
    import department_queue as Q
    import research
    d = _load()
    styles = cfg()["styles"]
    count = {s: len([i for i in d["items"] if i["status"] in ("ready", "in_production") and i.get("style") == s and i["kind"] == kind]) for s in styles}
    style = min(styles, key=lambda s: (count[s], styles.index(s))) if kind == "video" else None
    refused = set()
    for _try in range(8):
        topic = topic_override or D.pick_topic(kind, style, None)
        if not topic:
            _log("no unused topic available for stock")
            return None
        if topic in refused and not topic_override:                 # the same topic twice = the topic bank is not excluding it: stop, never loop
            _log("the topic bank keeps offering a refused topic - stopping this pass")
            return None
        ok, why = Q.vet_topic(topic, kind)
        if not ok:
            refused.add(topic)
        tid = _topic_id(topic)
        if not ok:
            _log("topic refused (%s): %s" % (why[:60], topic[:50]))
            if topic_override:
                return None
            if tid:
                research.mark(tid, "skipped")
            else:
                try:
                    import db
                    db.note_topic(topic)
                except Exception:
                    pass
            continue
        jid = Q.enqueue(topic, kind, D.live_targets(kind, ["socialai"]), notify=True, source="stock")
        if not jid:
            return None
        m = Q.read_job(jid) or {}
        m.update(stock=True, style=style, style_prompt_tag=(D.STYLE_TAG.get(style) if style else None), slot="stock")
        Q.write_job(jid, m)
        if tid:
            research.mark(tid, "used")                 # reserved: ARJUN / NADIA never pick it again
        try:
            import db
            db.note_topic(topic)
        except Exception:
            pass
        d = _load()
        d["items"].append({"id": jid, "kind": kind, "style": style, "topic": topic, "status": "in_production",
                           "created": time.time(), "hook": "", "media": ""})
        _save(d)
        _log("producing %s %s for stock: %s (%s)" % (style or "", kind, topic[:60], jid))
        return jid
    return None


def scout_topics():
    """No usable topic left: the whole team starts with ARJUN. It is idle time, so he goes right now."""
    try:
        import desk_work
        st = desk_work._load()
        st["topic_scout"] = 0
        st["topic_editing"] = 0
        desk_work._save(st)
        # run_due() does ONE task per call: first the scout, then the editor that turns headlines into ready subjects
        r1 = desk_work.run_due()
        r2 = desk_work.run_due()
        _log("topics were short - ARJUN scouted: %s | edited: %s" % (
            r1[2][:60] if r1 else "waiting for the AI engine to be idle", r2[2][:80] if r2 else "-"))
    except Exception as e:
        _log("scout failed: %s" % str(e)[:80])


def _unstocked_kinds(now=None, hours=24):
    """Kinds of the upcoming slots (soonest first, next `hours`) that the stock cannot cover. Each upcoming slot
    uses up one ready post of its kind; a slot with nothing left to take would have to be generated live."""
    import datetime as dt
    import dispatcher as D
    now = now or dt.datetime.now()
    left = {k: _count(k) for k in ("video", "image", "text")}
    due = []
    for slot_id, hh, mm, kind, _style, _p in D.SLOTS:
        at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if at <= now:
            at += dt.timedelta(days=1)
        if (at - now).total_seconds() <= hours * 3600:
            due.append((at, kind))
    out = []
    for _at, kind in sorted(due):
        if left.get(kind, 0) > 0:
            left[kind] -= 1
        elif kind not in out:
            out.append(kind)
    return out


def choose_kind(now=None):
    """THE IDLE-TIME DECISION. Returns (kind, reason) or (None, reason). Rules, in order:
      1. nothing starts unless the machine is free (system ON, Ollama up, nothing running, GPU free) - can_produce.
      2. only kinds below their stock target are candidates, and only if the gap before the next slot is long enough
         (video 120 min, image 30, text 20).
      3. VIDEO first whenever a video fits (the owner's rule: the longest job gets the long gaps).
      4. otherwise, in a short gap, make the kind the NEXT UPCOMING SLOT will lack (so no slot is generated live),
         soonest slot first.
      5. otherwise make the kind whose stock is emptiest compared with its target (ties: video, image, text).
    """
    conf = cfg()
    pri, tgt = conf["priority"], conf["target"]
    below = [k for k in pri if _count(k) < int(tgt.get(k, 0))]
    fits = [k for k in below if can_produce(k, now)[0]]
    if not fits:
        why = can_produce(below[0], now)[1] if below else "every stock is full"
        return None, why
    if "video" in fits:
        return "video", ""
    for k in _unstocked_kinds(now):
        if k in fits:
            return k, "gap too short for a video - the next %s slot has no stock, making it now" % k
    k = min(fits, key=lambda x: (_count(x) / max(1, int(tgt.get(x, 1))), pri.index(x)))
    return k, "gap too short for a video - making the emptiest stock (%s)" % k


def tick(now=None):
    """The idle-time trigger (run by the SUPERVISOR's sidecar thread every ~2 minutes; cheap when nothing to do).
    Strict priority: video until 5 are ready -> image+text -> text -> (always) ARJUN's research and ready topics."""
    d = _load()
    changed = False
    # a stock job that died / finished without registering must not block the target forever
    try:
        import department_queue as Q
        for i in d["items"]:
            if i["status"] != "in_production":
                continue
            rows = {r["step_key"]: r["status"] for r in Q._rows(i["id"])}
            if "dead" in rows.values():
                i["status"], i["note"] = "failed", "a step stopped (see the job report)"
                changed = True
    except Exception:
        pass
    for i in d["items"]:
        if i["status"] == "in_production" and time.time() - i["created"] > 12 * 3600:
            i["status"], i["note"] = "failed", "no progress for 12 hours"
            changed = True
        if i["status"] in ("taken", "discarded", "failed") and i.get("folder") and time.time() - (i.get("taken_at") or i["created"]) > 3 * 86400:
            shutil.rmtree(i["folder"], ignore_errors=True)       # keep disk use small: copies of used posts go after 3 days
            i["folder"] = ""
            changed = True
    if changed:
        _save(d)
    kind = next_kind_needed()
    if not kind:
        return None                      # every stock is full: only ARJUN's own research clock runs (priority 4)
    # CIRCUIT BREAKER: 3 stock jobs that failed (not cancelled by the owner) within 3 hours = something is wrong
    # (a model, a source, the disk). Stop making stock for 2 hours instead of burning the machine on a loop.
    recent = [i for i in d["items"] if i["status"] == "failed" and not str(i.get("note", "")).startswith("cancelled")
              and time.time() - i["created"] < 3 * 3600]
    if len(recent) >= 3:
        last = max(i["created"] for i in recent)
        if time.time() - last < 2 * 3600:
            _log("circuit breaker: %d stock jobs failed in 3 h - idle production paused until %s" %
                 (len(recent), time.strftime("%I:%M %p", time.localtime(last + 2 * 3600))))
            return None
    kind, why = choose_kind(now)
    if not kind:
        return None                      # busy / GPU held / Ollama down / automation off / gap too short for anything
    if why:
        _log("idle choice: %s" % why)
    jid = produce_one(kind)
    if not jid:
        scout_topics()                   # no topic -> ARJUN first; the next tick produces
    return jid


# ------------------------------------------------------------------ registering a finished stock job
def register(job_id):
    """Called when GRACE passed on a stock job: copy the deliverables, list it, tell the owner. ELLA is NOT triggered."""
    import department_queue as Q
    m = Q.read_job(job_id) or {}
    folder = STOCK / "ready" / job_id
    folder.mkdir(parents=True, exist_ok=True)
    src = Path(m.get("run_path") or "")
    for name in ("final.mp4", "post.jpg", "meta.json", "caption.txt", "post.json"):
        f = src / name
        if f.exists():
            shutil.copy2(f, folder / name)
    try:
        import paths
        for area, name in (("scripts", "script.json"), ("research", "research_report.json")):
            f = Path(paths.job_dir(job_id, area)) / name
            if f.exists():
                shutil.copy2(f, folder / name)
    except Exception:
        pass
    hook = ""
    try:
        hook = (json.loads((folder / "script.json").read_text(encoding="utf-8")).get("hook") or "")
    except Exception:
        pass
    media = str(m.get("run_media") or "")
    if not media:
        try:
            media = json.loads((src / "post.json").read_text(encoding="utf-8")).get("media") or ""
        except Exception:
            media = ""
    d = _load()
    it = _find(d, job_id)
    already = bool(it and it["status"] == "ready")
    if not it:
        it = {"id": job_id, "kind": m.get("kind"), "style": m.get("style"), "topic": m.get("topic"), "created": time.time()}
        d["items"].append(it)
    it.update(status="ready", hook=hook, media=media, folder=str(folder), ready_at=time.time())
    _save(d)
    n = len([i for i in d["items"] if i["status"] == "ready"])
    _log("READY in stock (%d waiting): %s" % (n, m.get("topic", "")[:60]))
    # No Telegram message here (2026-10-11): pre-made posts are an internal matter (the dashboard shows the stock);
    # HELENA reports once, when the post is actually published.
    return it


# ------------------------------------------------------------------ taking from stock at schedule time
def _probe_ok(media, kind):
    """Cheap quality re-check at TAKE time: the file must still play (video: >= 5 s with video and audio)."""
    if kind != "video":
        return True, ""
    try:
        import imageio_ffmpeg
        import re
        import subprocess
        t = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(media)], capture_output=True,
                           text=True, errors="replace", timeout=60).stderr
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", t)
        dur = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 0
        if dur < 5:
            return False, "video is %.1fs long" % dur
        if " Audio:" not in t or " Video:" not in t:
            return False, "video lacks an audio or video track"
    except Exception:
        pass
    return True, ""


def take(kind, style=None):
    """Oldest ready item of this kind (and style). Re-audited first: media present (restored from the stock copy
    when the original is gone), playable, not older than max_age, and not published meanwhile.
    Returns the job id or None."""
    with _Lock():
        d = _load()
        c = cfg()
        cand = sorted([i for i in d["items"] if i["status"] == "ready" and i["kind"] == kind and (not style or i.get("style") == style)],
                      key=lambda i: i["created"])
        for it in cand:
            reason = None
            media = Path(it.get("media") or "")
            if kind == "text":
                pass                                  # a text post has no media file; its words are in post.json / meta
            elif not it.get("media") or not media.exists():
                backup = Path(it.get("folder") or "") / (media.name or "final.mp4")
                if it.get("media") and backup.exists():
                    media.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, media)                  # the working file was cleaned: restore it
                else:
                    reason = "media file is gone"
            if not reason and kind != "text":
                ok, why = _probe_ok(media, kind)
                if not ok:
                    reason = why
            if not reason and time.time() - it["created"] > c["max_age_days"] * 86400:
                reason = "older than %d days" % c["max_age_days"]
            if not reason:
                try:
                    import post_history
                    if post_history.find_repeat(it["topic"]):
                        reason = "the topic was published meanwhile"
                except Exception:
                    pass
            if reason:
                it.update(status="discarded", note=reason, taken_at=time.time())
                _log("discarded stock item (%s): %s" % (reason, it["topic"][:50]))
                continue
            it.update(status="taken", taken_at=time.time())
            _save(d)
            return it["id"]
        _save(d)
        return None


def untake(job_id, why=""):
    """Release failed after take(): put the post back so it is not lost."""
    with _Lock():
        d = _load()
        it = _find(d, job_id)
        if it and it["status"] == "taken":
            it.update(status="ready", note=("release failed: " + why)[:120])
            _save(d)


def release(job_id, platforms, slot=""):
    """Hand a taken stock job to ELLA right now."""
    import department_queue as Q
    m = Q.read_job(job_id) or {}
    m.update(platforms=list(platforms), stock_released=True, slot=slot or m.get("slot"))
    Q.write_job(job_id, m)
    Q._set(job_id, "ella", "ready", "released from stock for slot %s" % (slot or "?"))
    Q.schema().execute("UPDATE department_steps SET version=0, not_before=NULL WHERE job_id=? AND step_key='ella'", (job_id,))
    Q.audit(job_id, "ella", "ready", "taken from the STOCK and released for publishing (%s)" % (slot or "scheduled slot"))
    _log("released %s to ELLA for slot %s on %s" % (job_id, slot, ", ".join(platforms)))


def serve(kind, style, slot=""):
    """The scheduler's first question at every slot: is there a ready post? Returns the job id or None."""
    jid = take(kind, style)
    if not jid:
        return None
    import dispatcher as D
    try:
        release(jid, D.live_targets(kind, ["socialai"]), slot)
    except Exception as e:
        untake(jid, str(e))
        _log("release FAILED (%s) - the post is back in stock, normal generation will serve the slot" % str(e)[:80])
        return None
    return jid


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")     # titles contain emoji; cp1252 consoles crash
    except Exception:
        pass
    a = sys.argv[1:]
    if not a or a[0] == "--list":
        write_md()
        print(MD.read_text(encoding="utf-8"))
    elif a[0] == "--can":
        print(can_produce())
    elif a[0] == "--produce":
        print(produce_one("video"))
    elif a[0] == "--next":
        print(next_slot_minutes())
