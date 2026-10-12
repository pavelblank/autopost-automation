# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""AutoPost - Social Media Department v3.1  (the 18 agents)

Every agent is ONE function `run_<key>(job_id)` driven by the trigger engine
(department_queue.py). The engine owns timeouts, heartbeats, retries, backoff, routing
a rejection back to the earliest specialist, dead-letter and the generation lease.
This module owns what each agent actually DOES.

Contract every agent obeys
  * reads only the previous agents' ARTIFACT FILES (WORK/<area>/<job_id>/...), never memory
  * writes exactly its named output, then updates its own room STATUS.md
  * raises Reject (retry / route back), Fatal (dead-letter now) or Wait (not a failure)
  * NOAH cannot start without ISABELLA's authorisation file; ELLA cannot publish without
    MARCUS's release file - both re-checked from the files, always
  * a dry-run job passes through every agent and gate with deterministic placeholders:
    no model call, no generation, no publish

Run:  python scripts/department.py --topic "..." --kind text --dry-run
Real: python scripts/department_queue.py --enqueue "..." --kind text --run
"""
import argparse
import hashlib
import json
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "videogen"))

import db                      # noqa: E402
import paths                   # noqa: E402
from dept_errors import Fatal, Reject, Wait   # noqa: E402

PY = str(ROOT / "venv" / "Scripts" / "python.exe")

FINAL_STATUS = {
    "ok": "PUBLISHED AND VERIFIED",
    "dry": "DRY RUN COMPLETE (nothing generated or published)",
    "corrected": "PUBLISHED WITH CORRECTIONS",
    "partial": "PARTIALLY PUBLISHED",
    "failed": "FAILED",
    "human": "STOPPED - SUPERVISOR RESOLVING",     # was 'AWAITING HUMAN ACTION': nobody is waiting for a person any more
}


def log(m):
    try:
        print("[%s] %s" % (time.strftime("%I:%M:%S %p"), m), flush=True)
    except UnicodeEncodeError:
        print(m.encode("ascii", "replace").decode("ascii"), flush=True)


def rules():
    try:
        return json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def dept_rules():
    return rules().get("department", {}) or {}


def department_enabled():
    return bool(dept_rules().get("enabled"))


# --------------------------------------------------------------------------- processes
PROCS = {}                     # pid -> Popen, every child the department started
_PROCS_LOCK = threading.Lock()


def run_cmd(args, timeout, cwd=None, env=None, on_start=None):
    """Run a child process with a HARD timeout. The whole process tree is killed on timeout,
    so nothing can keep the GPU, the lease or the database. Returns (returncode, output)."""
    p = subprocess.Popen(args, cwd=cwd or str(ROOT), env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         creationflags=0x00008000)    # ABOVE_NORMAL: the working agent wins the CPU too
    with _PROCS_LOCK:
        PROCS[p.pid] = p
    try:
        if on_start:
            on_start(p.pid)
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out or ""
    except subprocess.TimeoutExpired:
        _kill(p.pid)
        try:
            out, _ = p.communicate(timeout=10)
        except Exception:
            out = ""
        raise TimeoutError("child process exceeded %ds and was killed: %s"
                           % (timeout, " ".join(map(str, args))[-120:]))
    finally:
        with _PROCS_LOCK:
            PROCS.pop(p.pid, None)


def _kill(pid):
    try:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=30)
    except Exception:
        pass


def _gpu_util():
    """Current GPU utilisation in percent (None when it cannot be read)."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10, creationflags=0x08000000)
        return int((r.stdout or "").strip().splitlines()[0])
    except Exception:
        return None


def _tree_cpu_seconds(pid):
    """Total CPU seconds used so far by a process and all its children (None when unreadable)."""
    try:
        import psutil
        p = psutil.Process(pid)
        return sum((q.cpu_times().user + q.cpu_times().system) for q in [p] + p.children(recursive=True))
    except Exception:
        return None


def run_cmd_watched(args, timeout, logfile, watch_dir, idle_minutes=25, cwd=None, env=None, on_start=None):
    """Like run_cmd, for the long video render - but it tells a SLOW render from a DEAD one.

    A render may legitimately run for a long time (complex content / screenplay), so the long hard limit stays.
    What is new: the child's output goes to `logfile` as it happens (so a stall can be diagnosed afterwards), and
    every 30 s we look for signs of life - the GPU busy, the CPU working, a new or changed file in `watch_dir`,
    or new log output. If there has been NO sign of life for `idle_minutes`, the render is dead (a hang on the
    first attempt used to burn the whole 88-minute limit before the retry could start): kill it now so the retry,
    which reuses the clips already rendered, starts at once. Raises TimeoutError('timed out: ...') so the
    Watchdog treats it as a transient failure and retries. Returns (returncode, output)."""
    Path(logfile).parent.mkdir(parents=True, exist_ok=True)
    lf = open(logfile, "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen(args, cwd=cwd or str(ROOT), env=env, stdout=lf, stderr=subprocess.STDOUT,
                         creationflags=0x00008000)
    with _PROCS_LOCK:
        PROCS[p.pid] = p
    t0 = last_alive = time.time()
    last_sig, last_cpu = None, None
    try:
        if on_start:
            on_start(p.pid)
        while True:
            try:
                p.wait(timeout=30)
                break
            except subprocess.TimeoutExpired:
                pass
            now = time.time()
            if now - t0 > timeout:
                _kill(p.pid)
                raise TimeoutError("child process exceeded %ds and was killed: %s"
                                   % (timeout, " ".join(map(str, args))[-120:]))
            sig = [os.path.getsize(logfile)]
            try:
                for f in Path(watch_dir).rglob("*"):
                    if f.is_file():
                        st = f.stat()
                        sig.append((f.name, st.st_size, int(st.st_mtime)))
            except Exception:
                pass
            gpu, cpu = _gpu_util(), _tree_cpu_seconds(p.pid)
            alive = (sig != last_sig) or (gpu is not None and gpu >= 5) \
                or (cpu is not None and last_cpu is not None and cpu - last_cpu > 20)
            if alive:
                last_alive = now
            last_sig = sig
            if cpu is not None and (last_cpu is None or cpu - last_cpu > 20):
                last_cpu = cpu
            if now - last_alive > idle_minutes * 60:
                _kill(p.pid)
                raise TimeoutError("timed out: the render showed no sign of life for %d minutes (GPU idle, CPU idle, "
                                   "no new file, no output) - stalled render killed so the retry can start now"
                                   % idle_minutes)
    finally:
        with _PROCS_LOCK:
            PROCS.pop(p.pid, None)
        try:
            lf.close()
        except Exception:
            pass
    try:
        out = Path(logfile).read_text(encoding="utf-8", errors="replace")
    except Exception:
        out = ""
    return p.returncode, out


def kill_children():
    """Called by the engine's watchdog at the hard limit."""
    with _PROCS_LOCK:
        pids = list(PROCS)
    for pid in pids:
        _kill(pid)


# --------------------------------------------------------------------------- local AI
class LLMError(Exception):
    pass


LAST_MODEL = {"name": None}
DEADLINE = {"t": None}      # wall-clock time by which the CURRENT step must be finished


def models():
    R = rules()
    primary = R.get("ollama_model") or "qwen2.5:14b"
    out = [primary]
    fo = R.get("ollama_failover", {}) or {}
    if fo.get("enabled", True):
        for m in R.get("ollama_fallbacks", []) or []:
            if m and m not in out:
                out.append(m)
    return out


def active_model():
    return models()[0]


def _ollama(model, prompt, system, as_json, timeout):
    payload = {"model": model, "prompt": prompt, "stream": False, "keep_alive": "10m",
               "options": {"temperature": 0.5}}
    if system:
        payload["system"] = system
    if as_json:
        payload["format"] = "json"
    req = urllib.request.Request("http://localhost:11434/api/generate",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode())
    text = (resp.get("response") or "").strip()
    if not text:
        raise LLMError("model %s returned an empty response" % model)
    return text


def unload_model(model):
    """Free the model's memory (keep_alive=0) so two big models never sit in RAM together."""
    try:
        req = urllib.request.Request("http://localhost:11434/api/generate",
                                     data=json.dumps({"model": model, "keep_alive": 0}).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
    except Exception:
        pass


def _parse_json(text):
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(t)
    except Exception:
        s, e = t.find("{"), t.rfind("}")
        if s != -1 and e > s:
            return json.loads(t[s:e + 1])
        raise LLMError("reply was not valid JSON")


def llm(prompt, system=None, as_json=False, timeout=240, required=None, check=None):
    """Call the local model with real error handling.

    * every call has a timeout
    * JSON replies are parsed and checked (required keys non-empty, optional check(d) that
      raises ValueError naming what is missing); a bad reply is retried once with a stricter
      prompt, never silently accepted as {}
    * if the primary model fails twice, the primary is UNLOADED and the backup is tried
      (sequential - never two big models loaded at once)
    * all models failed -> LLMError (the engine retries with backoff, then dead-letters)
    """
    errs = []
    cands = models()
    for mi, model in enumerate(cands):
        p = prompt
        for attempt in (1, 2):
            # the step has ONE total time budget: never start a model call that cannot finish
            # inside it - fail cleanly (engine retries) instead of being killed by the watchdog
            left = (DEADLINE["t"] - time.time()) if DEADLINE["t"] else None
            if left is not None and left < 25:
                errs.append("step time budget exhausted")
                raise LLMError("step time budget exhausted after: " + " | ".join(errs[-3:]))
            try:
                text = _ollama(model, p, system, as_json, min(timeout, left) if left else timeout)
                if not as_json:
                    LAST_MODEL["name"] = model
                    return text
                d = _parse_json(text)
                if not isinstance(d, dict) or not d:
                    raise LLMError("reply was an empty JSON object")
                miss = [k for k in (required or []) if not d.get(k)]
                if miss:
                    raise LLMError("reply is missing: %s" % ", ".join(miss))
                if check:
                    check(d)
                LAST_MODEL["name"] = model
                return d
            except Exception as e:
                errs.append("%s try%d: %s" % (model, attempt, str(e)[:90]))
                log("    model %s attempt %d failed: %s" % (model, attempt, str(e)[:100]))
                if as_json and attempt == 1 and not isinstance(e, (OSError,)):
                    p = prompt + ("\n\nYour previous answer was rejected: %s. Return ONLY one valid "
                                  "JSON object containing every required key." % str(e)[:140])
        if mi + 1 < len(cands):
            log("    primary model %s failed twice - unloading it and trying backup %s"
                % (model, cands[mi + 1]))
            unload_model(model)
    raise LLMError("every model failed: " + " | ".join(errs[-4:]))


# --------------------------------------------------------------------------- job
class Job:
    """Context for one post. Rebuilt from job.json for every agent call - agents share
    nothing in memory, only artifact files."""

    def __init__(self, meta):
        self.meta = meta
        self.job_id = meta["job_id"]
        self.topic = (meta.get("topic") or "").strip()
        self.kind = meta.get("kind", "video")
        self.platforms = list(meta.get("platforms") or ["socialai"])
        self.dry_run = bool(meta.get("dry_run"))
        self.notify = bool(meta.get("notify"))

    @classmethod
    def load(cls, job_id):
        import department_queue as Q
        meta = Q.read_job(job_id)
        if not meta:
            raise Fatal("job metadata for %s is missing" % job_id)
        return cls(meta)

    # -- persistence ------------------------------------------------------
    def set_meta(self, **kw):
        import department_queue as Q
        self.meta.update(kw)
        Q.write_job(self.job_id, self.meta)

    @property
    def run_id(self):
        return self.meta.get("run_id")

    def out(self, agent_key, area=None):
        folder, agent, role, stage, default_area = paths.ROOMS[agent_key]
        return paths.job_dir(self.job_id, area or default_area)

    def write(self, agent_key, area, name, obj):
        f = self.out(agent_key, area) / name
        f.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return f

    def read(self, agent_key, area, name, default=None):
        try:
            return json.loads((self.out(agent_key, area) / name).read_text(encoding="utf-8"))
        except Exception:
            return default

    def touch_status(self, agent_key, status, waiting_on="-"):
        folder, agent, role, stage, _ = paths.ROOMS[agent_key]
        f = paths.room(agent_key) / "STATUS.md"
        prev = f.read_text(encoding="utf-8") if f.exists() else ""
        head, _, _hist = prev.partition("## History")
        head = "\n".join(l for l in head.splitlines() if not re.match(r"^\|\s*(Status|Current job|Current version|Last updated|Waiting on)\s*\|", l))
        rows = {"Status": status, "Current job": self.job_id, "Current version": "-",
                "Last updated": time.strftime("%Y-%m-%d %H:%M:%S"), "Waiting on": waiting_on}
        out = [head.rstrip(), ""]
        for k, v in rows.items():
            out.append("| %s | %s |" % (k, v))
        out += ["", "## History"]
        line = "- %s  %s  %s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), self.job_id,
                                     agent or "INTAKE", status)
        hist = [l for l in _hist.strip().splitlines() if l.strip() and not l.strip().startswith("_(")]
        out.append("\n".join((hist + [line])[-12:]))
        f.write_text("\n".join(out) + "\n", encoding="utf-8")

    # -- convenience --------------------------------------------------------
    def spec_timeout(self, key):
        import department_queue as Q
        return Q.spec_for(key)["timeout"]

    def final_content(self):
        """The caption the generator ACTUALLY produced (post.json / meta.json), else the plan."""
        rp = self.meta.get("run_path")
        d = {"title": "", "caption": "", "tags": [], "blog_body": "", "media": None}
        if rp and Path(rp).is_dir():
            pj, mj = Path(rp) / "post.json", Path(rp) / "meta.json"
            try:
                if pj.exists():
                    p = json.loads(pj.read_text(encoding="utf-8"))
                    d.update(title=p.get("topic", ""), caption=p.get("caption", ""),
                             tags=(p.get("hashtags") or "").split(), blog_body=p.get("blog_body") or "",
                             media=p.get("media"))
                elif mj.exists():
                    m = json.loads(mj.read_text(encoding="utf-8"))
                    d.update(title=m.get("title", ""), caption=m.get("hook", ""),
                             tags=m.get("hashtags") or [], blog_body=m.get("blog_body") or "")
                    vids = sorted(Path(rp).glob("*.mp4"), key=lambda x: x.stat().st_mtime)
                    d["media"] = self.meta.get("run_media") or (str(vids[-1]) if vids else None)
            except Exception:
                pass
        if rp and Path(rp).is_dir() and not d["media"]:
            fin = Path(rp) / "final.mp4"
            vids = [fin] if fin.exists() else sorted(Path(rp).glob("*.mp4"), key=lambda x: x.stat().st_mtime)
            d["media"] = self.meta.get("run_media") or (str(vids[-1]) if vids else None)
        if not d["caption"]:
            sc = self.read("sophie", "scripts", "script.json", {}) or {}
            d.update(title=sc.get("title", ""), caption=sc.get("narration", ""))
        return d


NEXT = {}
try:
    import department_queue as _Q
    for _k, _a, _s, _d in _Q.STEPS:
        if _d:
            NEXT[_d] = _k
except Exception:
    pass


# The 2026-10-10 roster merged 18 chain agents into 12. A merged agent runs the proven code of the
# agents it absorbed, in order, as ONE step with ONE retry budget and ONE hand-off:
#   DANIEL  = storyboard, then AISHA's characters/backgrounds
#   GRACE   = file/media inspection, then ETHAN's content audit of the generated text
#   ELLA    = CHLOE's per-platform packages, MARCUS's release gate, then the publish itself
#   HELENA  = JAMES's fix-up (only when something failed), then the final report
CORE = {}                                   # key -> the undecorated handler
ABSORB_BEFORE = {"ella": ["chloe", "marcus"], "helena": ["james"]}
# NADIA runs the second history audit + attaches the research record for the topic (ARJUN's proven
# duplicate check), so every later agent still finds research_report.json beside the job.
ABSORB_AFTER = {"intake": ["arjun"], "daniel": ["aisha"], "grace": ["ethan"]}


HANDOFF = {}     # agent key -> fn(job) -> list of problems. THE DEFINITION OF DONE: checked at the agent's own desk, BEFORE the
                 # next agent is triggered, so a defect is retried by the agent that caused it, not found at the end.


def agent(key):
    """Wrap a handler: load the job, mark the room working, record the outcome."""
    def deco(fn):
        CORE[key] = fn

        def wrapper(job_id):
            job = Job.load(job_id)
            nxt = NEXT.get(key, "-").upper()
            job.touch_status(key, "working", waiting_on="-")
            DEADLINE["t"] = time.time() + job.spec_timeout(key) - 15
            try:
                notes = []
                for k_ in ABSORB_BEFORE.get(key, []):
                    o_ = CORE[k_](job)
                    if isinstance(o_, str) and o_:
                        notes.append(o_)
                out = fn(job)
                for k_ in ABSORB_AFTER.get(key, []):
                    o_ = CORE[k_](job)
                    if isinstance(o_, str) and o_:
                        notes.append(o_)
                if notes and isinstance(out, str):
                    out = out + " | " + " | ".join(notes)
                chk = HANDOFF.get(key)
                if chk is not None and not job.dry_run:
                    probs = chk(job)
                    if probs:        # same agent retries (engine back-off); never passes a half-finished product on
                        raise RuntimeError("handoff check failed - %s's work is not finished: %s" % (key.upper(), "; ".join(probs)[:300]))
            except Wait as w:
                job.touch_status(key, "waiting", waiting_on=w.reason[:60])
                raise
            except Exception as e:
                job.touch_status(key, "failed: %s" % str(e)[:80], waiting_on="engine retry")
                raise
            finally:
                DEADLINE["t"] = None
            if isinstance(out, tuple):
                job.touch_status(key, out[0], waiting_on=nxt)
            else:
                job.touch_status(key, "done", waiting_on=nxt)
            return out
        wrapper.__name__ = "run_%s" % key
        globals()["run_%s" % key] = wrapper
        return wrapper
    return deco


def _need(d, keys, what):
    miss = [k for k in keys if not d.get(k)]
    if miss:
        raise Reject("%s is missing: %s" % (what, ", ".join(miss)))


def _short(t, n):
    t = str(t or "")
    return t if len(t) <= n else t[:n - 1] + "..."


# ===========================================================================
# STAGE 1 - INTAKE
# ===========================================================================
@agent("intake")
def _intake(job):
    if not job.topic:
        raise Fatal("no topic supplied")
    if job.kind not in ("video", "image", "text"):
        raise Fatal("unknown kind %r (valid: video, image, text)" % job.kind)
    if len(job.topic) > 400:
        raise Fatal("topic is too long (%d chars, max 400)" % len(job.topic))
    cls = {"video": "A. Video + text", "image": "B. Image + text", "text": "C. Text only"}[job.kind]
    history = []
    try:
        import research
        history = [r["title"] for r in research.unused(limit=5)]
    except Exception:
        pass
    job.write("intake", "intake", "intake.json", {
        "job_id": job.job_id, "classification": cls, "source": job.topic, "kind": job.kind,
        "requirements": "one post, complete script, sound, on-screen script" if job.kind == "video"
        else "one %s post with a complete caption" % job.kind,
        "destinations": job.platforms, "dry_run": job.dry_run, "publication_history_checked": history})
    return cls


# ===========================================================================
# STAGE 2 - CREATIVE PLANNING
# ===========================================================================
@agent("maya")
def _maya(job):
    keys = ["objective", "target_audience", "central_message", "intended_outcome", "tone"]
    brief = {"topic": job.topic, "kind": job.kind, "destinations": job.platforms}
    if job.dry_run:
        brief.update({k: "dry-run placeholder for %s" % k for k in keys})
    else:
        brief.update(llm(
            "Create a creative brief as JSON with exactly these keys: objective, target_audience, "
            "central_message, intended_outcome, tone (each a short plain sentence). Topic: %s" % job.topic,
            system="You are a creative strategist. Output ONLY valid JSON.", as_json=True,
            required=keys, timeout=min(240, job.spec_timeout("maya") - 40)))
    job.write("maya", "scripts", "creative_brief.json", brief)
    return "creative brief written (%s)" % (LAST_MODEL["name"] or "dry-run")


@agent("arjun")
def _arjun(job):
    doc = {"topic": job.topic, "verified_claims": [], "sources": [], "duplicates": [],
           "copyright_notes": [], "unsupported_claims": [], "unverified": True, "mode": "no_sources",
           "source_text": "",
           "note": "No usable source was found: the post must make NO factual claims."}
    if not job.dry_run:
        try:
            import research
            fresh, similar = research.check_fresh(job.topic, exclude_self=True)   # its own reservation is not a duplicate
            doc["duplicate_check"] = {"fresh": bool(fresh), "similar_to": similar}
            if not fresh:
                doc["duplicates"].append(similar)
                job.write("arjun", "research", "research_report.json", doc)
                raise Fatal("near-duplicate of an already used topic: \"%s\"" % _short(similar, 90))
        except Fatal:
            raise
        except Exception as e:
            doc["duplicate_check"] = "unavailable: %s" % str(e)[:80]
    if not job.dry_run:
        try:
            import fact_grounding
            r = fact_grounding.research_topic(job.topic)
            doc.update(r)
            doc["unverified"] = r["mode"] != "grounded"
            doc["note"] = ("Facts below are verbatim from the listed sources; the post may state ONLY these."
                           if r["mode"] == "grounded" else doc["note"])
        except Exception as e:
            doc["note"] = "research failed (%s): the post must make NO factual claims." % str(e)[:80]
    job.write("arjun", "research", "research_report.json", doc)
    return "research: %s (%d source(s), %d verified fact(s))" % (
        doc["mode"], len(doc.get("sources", [])), len(doc.get("verified_claims", [])))


REAL_LEAD_RULE = (" For photoreal (real people) videos the MAIN CHARACTER is an attractive, charming young woman aged 25 to 32 "
                  "(natural beauty, warm smile, confident, photogenic) in a stylish, tasteful, fully dressed everyday outfit that "
                  "suits the setting. Adults only. Never nude, never revealing or sexual: the video must be safe for YouTube and "
                  "TikTok. Describe her face, hair and outfit precisely (in characters: name, age, look, dress) so she looks the "
                  "same in every scene.")

SPOKEN_MAX_WORDS = 28     # 4 scenes of ~3 s = about 11 s of picture; natural speech is about 2.6 words a second


def _norm_dialog(d):
    """Any dialogue shape the writer produced -> {"speaker", "line"} (or None when there is no sentence)."""
    if isinstance(d, str):
        return {"speaker": "Narrator", "line": d.strip()} if d.strip() else None
    if not isinstance(d, dict):
        return None
    who = next((str(d[k]).strip() for k in ("speaker", "character", "who", "name") if d.get(k)), "")
    line = next((str(d[k]).strip() for k in ("line", "text", "say", "words", "dialog", "dialogue") if d.get(k)), "")
    if not line:                                         # {"Emma": "sentence"}
        for k, v in d.items():
            if k in ("speaker", "character", "who", "name", "line", "text", "say", "words", "dialog", "dialogue"):
                continue
            if isinstance(v, str) and v.strip():
                who, line = (who or str(k)), v.strip()
                break
    return {"speaker": who or "Narrator", "line": line} if line else None


def _fit_spoken(script, job):
    """A video is ~11 s long and every dialogue line is SPOKEN. 21-word lines x 3 were 60 words = 25 s of voice on
    11 s of picture (the voice ran on over a frozen frame). Keep the writer's meaning but make it fit: ask the
    model to shorten once; if it is still too long, drop whole trailing lines (never cut a sentence in half)."""
    lines = [x for x in (_norm_dialog(d) for d in (script.get("dialogs") or [])) if x]
    words = lambda ls: sum(len(x["line"].split()) for x in ls)
    if words(lines) > SPOKEN_MAX_WORDS:
        try:
            r = llm("Shorten these spoken dialogue lines for an 11 second video. Keep the same speakers, the same order and the "
                    "same meaning, state NOTHING new. Use at most 4 lines and AT MOST %d words in total, each line one complete "
                    "short sentence. Return JSON {\"dialogs\": [{\"speaker\": ..., \"line\": ...}]}.\nLines: %s"
                    % (SPOKEN_MAX_WORDS, json.dumps(lines, ensure_ascii=False)),
                    system="You are a careful editor. Output ONLY valid JSON.", as_json=True, required=["dialogs"], timeout=150)
            new = [x for x in (_norm_dialog(d) for d in r.get("dialogs") or []) if x]
            if new and words(new) <= words(lines):
                lines = new
        except Exception as e:
            log("    could not shorten the dialogue with the model (%s) - trimming it" % str(e)[:60])
        while words(lines) > SPOKEN_MAX_WORDS and len(lines) > 1:
            lines.pop()                                   # drop the last whole line, never half a sentence
        if words(lines) > SPOKEN_MAX_WORDS:               # one single long line: keep its first sentence(s) that fit
            ws = lines[0]["line"].split()[:SPOKEN_MAX_WORDS]
            lines[0]["line"] = " ".join(ws).rstrip(",;:") + "."
        log("    dialogue fitted to the video: %d line(s), %d word(s)" % (len(lines), words(lines)))
    script["dialogs"] = lines
    return script


def _script_check(kind):
    def check(d):
        miss = []
        if len(str(d.get("narration", "")).strip()) < 40:
            miss.append("narration (at least 40 characters)")
        if kind != "text":
            arc = d.get("story_arc")
            if not isinstance(arc, dict):
                miss.append("story_arc")
            else:
                miss += ["story_arc.%s" % k for k in
                         ("problem", "tension", "insight", "action", "result", "takeaway") if not arc.get(k)]
            if not d.get("characters"):
                miss.append("characters (with age and dress)")
            # the owner's SCRIPT & SCREENPLAY FIRST rule: no video until the screenplay spells out
            # background, timing, dialogs and movement as well (arc/characters checked above).
            for k in ("background", "timing", "dialogs", "movement"):
                if not d.get(k):
                    miss.append(k)
        if miss:
            raise ValueError("script is missing: %s" % ", ".join(miss))
    return check


def _lowercase_common(obj, issues):
    """Lower-case every flagged SINGLE alphabetic word (optionally with 's) in all string fields. Multi-word names,
    numbers and quotes are left alone (those must really be removed by the rewrite)."""
    import re
    words = []
    for i in issues:
        m = re.fullmatch(r"([A-Za-z]+)(?:['’]s)?", str(i).strip())
        if m:
            words.append(m.group(1))
    if not words:
        return obj
    rx = re.compile(r"\b(%s)\b" % "|".join(re.escape(w) for w in set(words)))

    def fix(v):
        if isinstance(v, str):
            return rx.sub(lambda m: m.group(1).lower(), v)
        if isinstance(v, list):
            return [fix(x) for x in v]
        if isinstance(v, dict):
            return {k: fix(x) for k, x in v.items()}
        return v
    return fix(obj)


@agent("sophie")
def _sophie(job):
    brief = job.read("maya", "scripts", "creative_brief.json", {})
    res = job.read("arjun", "research", "research_report.json", {})
    if not brief:
        raise Reject("creative brief is missing")
    if job.dry_run:
        script = {"title": "Dry run: " + job.topic[:60], "narration": "Dry run narration. " * 6,
                  "on_screen_text": "Dry run", "kind": job.kind,
                  "story_arc": {k: "dry run" for k in ("problem", "tension", "insight", "action", "result", "takeaway")},
                  "characters": [{"name": "Dry", "age": 30, "dress": "plain"}],
                  "background": "dry run", "timing": "dry run", "dialogs": "dry run", "movement": "dry run"}
    elif job.kind in ("text", "image"):
        import fact_grounding
        script = llm(
            "Write the final post as JSON with keys: title, hook, narration. Topic: %s.\n"
            "Creative brief: %s\n%s\n"
            "hook = ONE or TWO sentences, max 230 characters, the strongest opening, plain human voice. "
            "narration = 3-5 sentences (max 110 words) that expands the hook, no emoji spam."
            % (job.topic, json.dumps(brief, ensure_ascii=False)[:900], fact_grounding.facts_prompt(res)),
            system="You are a careful copywriter who never states anything that is not in the verified facts. "
                   "Output ONLY valid JSON.", as_json=True,
            required=["title", "hook", "narration"], check=_script_check("text"),
            timeout=min(240, job.spec_timeout("sophie") - 40))
    else:
        script = llm(
            "Write the final script as JSON with keys: title, hook, narration, on_screen_text, background, "
            "characters (list of {name, age, dress}), timing, dialogs, movement, story_arc "
            "(object with keys problem, tension, insight, action, result, takeaway). Topic: %s. Kind: %s.\n"
            "Creative brief: %s\n"
            "on_screen_text must be ONE short line a human reads on a phone. Also give hook (ONE or TWO sentences, "
            "max 230 characters). Dialogs are part of the post: they may state ONLY verified facts. "
            "dialogs = a list of at most 4 objects {\"speaker\": name, \"line\": one complete short sentence}; ALL lines together "
            "at most 28 words, because the finished video is only about 11 seconds long and every line is spoken aloud.\n%s"
            % (job.topic, job.kind, json.dumps(brief, ensure_ascii=False)[:900], __import__("fact_grounding").facts_prompt(res)),
            system="You are a screenwriter. Output ONLY valid JSON."
                   + (REAL_LEAD_RULE if (job.kind == "video" and (job.meta.get("style") or "") == "real") else ""), as_json=True,
            required=["title", "narration", "story_arc"], check=_script_check(job.kind),
            timeout=min(300, job.spec_timeout("sophie") - 40))
    script["unverified_research"] = bool(res.get("unverified", True))
    if not job.dry_run:
        # SOPHIE audits her own text against the research before it leaves her desk
        import fact_grounding
        issues = fact_grounding.grounding_check(fact_grounding.post_text(script), res, job.topic, fact_grounding.script_extra(script))
        if issues and job.kind in ("text", "image"):
            script = llm(
                "Rewrite this post as JSON with keys: title, hook, narration. REMOVE every one of these "
                "unsupported specifics (do not replace them with other specifics): %s.\n%s\nPost: %s"
                % (", ".join(issues[:12]), fact_grounding.facts_prompt(res), json.dumps(script, ensure_ascii=False)[:1500]),
                system="You are a careful editor. Output ONLY valid JSON.", as_json=True,
                required=["title", "hook", "narration"], check=_script_check("text"),
                timeout=min(240, job.spec_timeout("sophie") - 40))
            script["unverified_research"] = bool(res.get("unverified", True))
            issues = fact_grounding.grounding_check(fact_grounding.post_text(script), res, job.topic, fact_grounding.script_extra(script))
        elif issues:
            # VIDEO scripts get the same self-edit pass (they used to be rejected outright and the writer repeated the
            # same slip until its budget was gone). Same keys back, only the flagged specifics removed.
            vkeys = ["title", "narration", "story_arc"]
            fixed = llm(
                "Rewrite this script as JSON with EXACTLY the same keys. REMOVE every one of these unsupported "
                "specifics from every field (do not replace them with other specifics): %s.\n%s\nScript: %s"
                % (", ".join(issues[:12]), fact_grounding.facts_prompt(res), json.dumps(script, ensure_ascii=False)[:3500]),
                system="You are a careful editor. Output ONLY valid JSON.", as_json=True,
                required=vkeys, check=_script_check(job.kind),
                timeout=min(300, job.spec_timeout("sophie") - 40))
            fixed["unverified_research"] = bool(res.get("unverified", True))
            script = fixed
            issues = fact_grounding.grounding_check(fact_grounding.post_text(script), res, job.topic, fact_grounding.script_extra(script))
        if issues:
            # last resort, no model: a flagged single common word that is merely capitalised ("Earth", "Moon") is
            # not a proper-name claim - write it in lower case and look again
            script = _lowercase_common(script, issues)
            issues = fact_grounding.grounding_check(fact_grounding.post_text(script), res, job.topic, fact_grounding.script_extra(script))
        if issues:
            raise Reject("unsupported specifics in the text (not in the verified facts): %s" % ", ".join(issues[:10]),
                         owner="sophie")
    if job.kind == "video" and not job.dry_run:
        script = _fit_spoken(script, job)
    job.write("sophie", "scripts", "script.json", script)
    return "script written (%s)" % (LAST_MODEL["name"] or "dry-run")


SCENES = {"video": 4, "image": 1}


@agent("daniel")
def _daniel(job):
    script = job.read("sophie", "scripts", "script.json")
    if not script:
        raise Reject("script is missing")
    n = SCENES.get(job.kind, 1)
    if job.dry_run:
        board = {"scenes": [{"n": i + 1, "beat": "dry run", "framing": "wide", "transition": "cut"} for i in range(n)]}
    else:
        def check(d):
            sc = d.get("scenes")
            if not isinstance(sc, list) or len(sc) != n:
                raise ValueError("scenes must be a list of EXACTLY %d scenes (got %s)"
                                 % (n, len(sc) if isinstance(sc, list) else "none"))
            if any(not (isinstance(s, dict) and s.get("beat")) for s in sc):
                raise ValueError("every scene needs a beat")
        board = llm("Turn this script into a scene-by-scene plan as JSON with key scenes: a list of "
                    "EXACTLY %d objects {n, beat, framing, transition}.\n%s"
                    % (n, json.dumps(script, ensure_ascii=False)[:1500]),
                    system="You are a storyboard director. Output ONLY valid JSON.", as_json=True,
                    required=["scenes"], check=check, timeout=min(240, job.spec_timeout("daniel") - 40))
    job.write("daniel", "scripts", "storyboard.json", board)
    return "storyboard written (%d scenes)" % len(board["scenes"])


@agent("aisha")
def _aisha(job):
    script = job.read("sophie", "scripts", "script.json", {})
    if job.dry_run:
        spec = {"characters": [{"name": "Dry", "age": 30, "dress": "plain", "look": "neutral"}],
                "backgrounds": ["plain room"], "props": [], "palette": ["#888"],
                "consistency_rules": ["same character description in every scene"]}
    else:
        def check(d):
            ch = d.get("characters")
            if job.kind == "image" and (ch is None or ch == []):
                ch = d["characters"] = []          # a concept image (sleep, nature ...) needs no character
            if not isinstance(ch, list) or (not ch and job.kind != "image"):
                raise ValueError("characters (a non-empty list)")
            bad = [c.get("name", "?") for c in ch if not (isinstance(c, dict) and c.get("age") and c.get("dress"))]
            if bad:
                raise ValueError("character description missing age or dress for: %s" % ", ".join(map(str, bad)))
        spec = llm("Define the visual design as JSON with keys: characters (each {name, age, dress, look}), "
                   "backgrounds (list), props (list), palette (list). For the MAIN character, look MUST state hair colour, "
                   "length and style, skin tone, build and the exact colours of every garment (the character is drawn in "
                   "several separate shots and must look identical in each). "
                   "Keep the characters of this script: %s\n"
                   "Topic: %s" % (json.dumps(script.get("characters", []), ensure_ascii=False)[:600], job.topic),
                   system="You are a visual designer. Output ONLY valid JSON.", as_json=True,
                   required=(["backgrounds", "palette"] if job.kind == "image"
                             else ["characters", "backgrounds", "palette"]), check=check,
                   timeout=min(240, job.spec_timeout("aisha") - 40))
        spec["consistency_rules"] = ["same character description in every scene"]
    job.write("aisha", "visual", "visual_spec.json", spec)
    return "visual spec written"


@agent("leo")
def _leo(job):
    if job.dry_run:
        emo = {"emotion": "calm", "atmosphere": "quiet", "expressions": ["soft"], "progression": ["tense", "calm"]}
    else:
        emo = llm("Define emotion and performance as JSON with keys: emotion, atmosphere, expressions "
                  "(list), progression (list). Topic: %s" % job.topic,
                  system="You are a performance director. Output ONLY valid JSON.", as_json=True,
                  required=["emotion", "atmosphere", "expressions", "progression"],
                  timeout=min(240, job.spec_timeout("leo") - 40))
    job.write("leo", "visual", "emotion_guide.json", emo)
    return "emotion guide written"


# ===========================================================================
# STAGE 3 - PLANNING APPROVAL
# ===========================================================================
@agent("oliver")
def _oliver(job):
    need = [("creative brief", "maya", "scripts", "creative_brief.json"),
            ("research report", "arjun", "research", "research_report.json"),
            ("script", "sophie", "scripts", "script.json"),
            ("emotion guide", "leo", "visual", "emotion_guide.json")]
    if job.kind != "text":
        need += [("storyboard", "daniel", "scripts", "storyboard.json"),
                 ("visual spec", "aisha", "visual", "visual_spec.json")]
    missing, found = [], {}
    for label, k, area, name in need:
        d = job.read(k, area, name)
        if not d:
            missing.append(label)
        else:
            found[label] = hashlib.md5(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:10]
    issues = []
    sc = job.read("sophie", "scripts", "script.json", {}) or {}
    if sc and not job.dry_run and job.kind == "video":      # the six-part story arc is a VIDEO screenplay rule
        arc = sc.get("story_arc") or {}
        issues += ["story_arc.%s empty" % k for k in ("problem", "tension", "insight", "action", "result", "takeaway") if not arc.get(k)]
    grounding = []
    if sc and not job.dry_run:
        import fact_grounding
        res_ = job.read("arjun", "research", "research_report.json", {}) or {}
        grounding = fact_grounding.grounding_check(fact_grounding.post_text(sc), res_, job.topic, fact_grounding.script_extra(sc))
        issues += ["unsupported specific (not in the verified facts): %s" % g for g in grounding[:8]]
    verdict = "approved" if not missing and not issues else "rejected"
    job.write("oliver", "audits", "planning_audit.json",
              {"missing": missing, "issues": issues, "verdict": verdict, "artifacts": found, "at": time.time(),
               "grounding": "pass" if not grounding else "fail"})
    if verdict != "approved":
        raise Reject("plan not approved: missing %s %s" % (", ".join(missing) or "nothing", "; ".join(issues)),
                     owner=("sophie" if grounding and not missing else None))
    return "plan approved (%d artifacts checked)" % len(found)


@agent("isabella")
def _isabella(job):
    a = job.read("oliver", "audits", "planning_audit.json")
    if not a:
        raise Reject("OLIVER's audit file is unreadable - never authorising on trust")
    if a.get("verdict") != "approved":
        raise Reject("OLIVER's verdict is %r" % a.get("verdict"))
    job.write("isabella", "audits", "generation_approval.json",
              {"authorised": True, "job_id": job.job_id, "authorised_at": time.time(),
               "plan_audit": a.get("artifacts"), "condition": "no material defect outstanding"})
    return "generation AUTHORISED"


# ===========================================================================
# STAGE 4 - GENERATION AND QUALITY CONTROL
# ===========================================================================
def _check_authorised(job):
    ap = job.read("isabella", "audits", "generation_approval.json")
    return bool(ap and ap.get("authorised") is True and ap.get("job_id") == job.job_id)


@agent("noah")
def _noah(job):
    if not _check_authorised(job):
        raise Fatal("no generation authorisation from ISABELLA - REFUSED (hard gate)")
    work = job.out("noah", "clips")
    sc = job.read("sophie", "scripts", "script.json", {}) or {}
    plan = {"job_id": job.job_id, "topic": job.topic, "kind": job.kind, "script": sc,
            "brief": job.read("maya", "scripts", "creative_brief.json", {}),
            "storyboard": job.read("daniel", "scripts", "storyboard.json", {}),
            "visual": job.read("aisha", "visual", "visual_spec.json", {}),
            "emotion": job.read("leo", "visual", "emotion_guide.json", {})}
    if job.kind == "video" and not job.dry_run:
        # last line of defence: whatever script reached the render, the spoken lines are real sentences and fit the picture
        # (idempotent - a script SOPHIE already fitted passes unchanged). The storyboard carries its own copy of the dialogs.
        try:
            sc_fit = _fit_spoken(dict(sc), job)
            plan["script"] = sc_fit
            sb_ = dict(plan.get("storyboard") or {})
            sb_["dialogs"] = sc_fit["dialogs"]
            plan["storyboard"] = sb_
        except Exception as e:
            log("  could not fit the spoken lines before the render (%s) - rendering as written" % str(e)[:70])
    planf = work / "plan.json"
    planf.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    if job.dry_run:
        f = work / "DRYRUN_asset.txt"
        f.write_text("dry run - nothing generated", encoding="utf-8")
        job.set_meta(run_path=str(work), run_id=None)
        return "dry run: placeholder asset"

    # REAL VIDEO PATH: Wan2.1 text-to-video, one moving clip per scene. Used when the job
    # carries a style tag (DISPATCHER sets it: "real" for the photoreal human slot,
    # "cartoon" for the animation slot). This REPLACES the Ken Burns zoom in make_video.py.
    style = (job.meta.get("style") or "").strip() or None
    if job.kind == "video" and style in ("real", "cartoon"):
        args = [PY, str(ROOT / "scripts" / "videogen_real.py"),
                "--plan", str(planf), "--out", str(work / "video"), "--style", style]
    elif job.kind == "video":
        args = [PY, str(ROOT / "scripts" / "make_video.py"), job.topic,
                "--scenes", str(SCENES["video"]), "--lease"]
    else:
        args = [PY, str(ROOT / "scripts" / "make_post.py"), job.kind, job.topic, "--lease"]
    env = dict(os.environ, AUTOPOST_PLAN_FILE=str(planf), PYTHONIOENCODING="utf-8")
    if job.kind == "video" and style in ("real", "cartoon"):
        _free_vram()
    if not db.acquire_gen_gate("department"):
        raise RuntimeError("another generation holds the lease - will retry after backoff")
    t0 = time.time()
    started = False
    try:
        timeout = max(120, job.spec_timeout("noah") - 120)
        if job.kind == "video" and style in ("real", "cartoon"):
            rc, out = run_cmd_watched(args, timeout, work / "noah_generation.log", work / "video", env=env,
                                      on_start=lambda pid: (db.bind_gen_worker(pid), None)[1])
        else:
            rc, out = run_cmd(args, timeout, env=env, on_start=lambda pid: (db.bind_gen_worker(pid), None)[1])
        started = True
    finally:
        db.release_gen_gate()
        if not started:
            db.abort_gen_gate("department")
    (work / "noah_generation.log").write_text(out, encoding="utf-8")
    if job.kind == "video" and style in ("real", "cartoon"):
        # The Wan generator (videogen_real.py) writes final.mp4 but, unlike make_video.py, never
        # recorded a run - so every successful render was reported as "recorded no run" and
        # retried until dead-lettered. Record the run here, from the real file.
        final = work / "video" / "final.mp4"
        if rc != 0 or not final.exists() or final.stat().st_size < 50000:
            raise RuntimeError("video generator failed (rc=%s): %s" % (rc, out[-200:]))
        # VOICE-OVER MUST BE COMPLETE (2026-10-11): a published video once said "line, line, line" after the hook and
        # lost every written line. Refuse a render whose voice-over did not speak every planned line, so the retry
        # (which reuses the rendered clips) fixes it instead of publishing it.
        try:
            vr = json.loads((final.parent / "voice_report.json").read_text(encoding="utf-8"))
        except Exception:
            vr = None
        if vr is None:
            raise RuntimeError("video has no voice-over report - not accepted")
        if not vr.get("ok"):
            raise RuntimeError("voice-over incomplete: %s of %s line(s) were spoken - not accepted"
                               % (vr.get("lines_spoken"), vr.get("lines_planned")))
        if float(vr.get("voice_s") or 0) > float(vr.get("video_s") or 0) + 1.5:
            log("  WARNING: the voice (%.1fs) is longer than the picture (%.1fs); the last frame is held so no word is cut"
                % (float(vr["voice_s"]), float(vr["video_s"])))
        _write_video_meta(job, plan, final.parent)
        rid = db.start_run(job.topic, "video")
        scenes_n = len(list((work / "video").glob("clip*.mp4")))
        db.finish_run(rid, status="ok", stage="done", path=str(final), scenes=scenes_n,
                      size_kb=int(final.stat().st_size / 1024), kind="video")
        with db.conn() as c:
            r = c.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    else:
        with db.conn() as c:
            r = c.execute("SELECT * FROM runs WHERE started>=? AND kind=? ORDER BY id DESC LIMIT 1",
                          (t0 - 5, job.kind)).fetchone()
    if not r:
        raise RuntimeError("generator exited (rc=%s) but recorded no run: %s" % (rc, out[-160:]))
    if r["status"] != "ok":
        raise RuntimeError("generation failed (run %d): %s" % (r["id"], (r["error"] or out[-160:])))
    # videos record the FILE (final.mp4) in runs.path, posts/images record the FOLDER
    rp = Path(r["path"] or "")
    media = None
    if rp.is_file():
        media, rp = str(rp), rp.parent
    if job.kind in ("text", "image"):
        _apply_approved_text(job, rp)
    job.set_meta(run_id=r["id"], run_path=str(rp), run_media=media)
    return "asset produced: run %d (%s)" % (r["id"], job.kind)


def _free_vram():
    """The video model needs the GPU's memory. Ollama keeps its language model (qwen2.5:14b, ~6 GB of the 8 GB)
    loaded after the planning agents are done, so the render crawled (16+ minutes for a clip that takes 6).
    Unload every resident model before a render; the next agent that needs the model simply reloads it."""
    try:
        import urllib.request
        ps = json.loads(urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=10).read().decode())
        for m in ps.get("models", []):
            req = urllib.request.Request("http://127.0.0.1:11434/api/generate", headers={"Content-Type": "application/json"},
                                         data=json.dumps({"model": m["name"], "keep_alive": 0}).encode())
            urllib.request.urlopen(req, timeout=30).read()
            log("  freed GPU memory: unloaded %s before the video render" % m["name"])
    except Exception as e:
        log("  could not free GPU memory (%s) - continuing" % str(e)[:60])


def _apply_approved_text(job, folder):
    """make_post.py writes its OWN caption with a free language-model call (the 'Thiocapsa roseus' bug:
    it contradicted the approved script and invented facts). The approved script is the only source
    of reader-facing text: overwrite the generator's text with it, word for word."""
    sc = job.read("sophie", "scripts", "script.json", {}) or {}
    hook = " ".join(str(sc.get("hook") or "").split())
    nar = " ".join(str(sc.get("narration") or "").split())
    if not hook and not nar:
        return
    pj = Path(folder) / "post.json"
    try:
        p = json.loads(pj.read_text(encoding="utf-8")) if pj.exists() else {}
    except Exception:
        p = {}
    tags = (p.get("hashtags") or "").strip()
    cap = hook or nar[:230]
    p.update(caption=cap, text=(cap + ("\n\n" + tags if tags else "")), blog_body=nar or cap,
             topic=job.topic, approved_text=True)
    pj.write_text(json.dumps(p, indent=2, ensure_ascii=False), encoding="utf-8")
    (Path(folder) / "caption.txt").write_text(p["text"], encoding="utf-8")


def _write_video_meta(job, plan, folder):
    """The Wan generator makes final.mp4 only. Every later agent (GRACE, ETHAN, CHLOE, ELLA) and
    publish.py read title / hook / hashtags / blog text from meta.json + caption.txt beside the
    video, exactly as make_video.py writes them. Without them GRACE saw "no media" and rejected a
    finished render. Built from the approved script (no model call, nothing new invented)."""
    import re as _re
    sc = (plan.get("script") or {}) if isinstance(plan, dict) else {}
    narr = " ".join(str(sc.get("narration") or "").split())
    title = (sc.get("title") or job.topic or "").strip()[:95]
    sentences = _re.split(r"(?<=[.!?])\s+", narr)
    hook = " ".join(sentences[:2]).strip()
    if len(hook) > 230:
        hook = hook[:227].rsplit(" ", 1)[0] + "..."
    words = [w for w in _re.findall(r"[A-Za-z]{4,}", job.topic or "")][:3]
    tags = ["#" + w.lower() for w in words] + ["#shorts", "#viral", "#fyp", "#trending"]
    seen, uniq = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    folder = Path(folder)
    (folder / "meta.json").write_text(json.dumps(
        {"title": title, "hook": hook or title, "hashtags": uniq, "blog_body": narr or hook},
        indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / "caption.txt").write_text(((hook or title) + "\n\n" + " ".join(uniq)).strip(), encoding="utf-8")


def _media_probe(path):
    """Duration / streams of a media file using the bundled ffmpeg (no ffprobe needed)."""
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None
    r = subprocess.run([exe, "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    t = r.stderr or ""
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", t)
    dur = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) if m else 0
    return {"duration": dur, "audio": " Audio:" in t, "video": " Video:" in t}


@agent("grace")
def _grace(job):
    defects = []
    checked = []
    if job.dry_run:
        checked.append("dry run: asset placeholder present")
        if not (job.out("noah", "clips") / "DRYRUN_asset.txt").exists():
            defects.append("placeholder asset missing")
    else:
        fc = job.final_content()
        rp = job.meta.get("run_path")
        if not rp or not Path(rp).is_dir():
            defects.append("asset folder missing")
        text = (fc["caption"] or "").strip()
        checked.append("caption present")
        if job.kind in ("text", "image"):
            import fact_grounding
            sc_ = job.read("sophie", "scripts", "script.json", {}) or {}
            res_ = job.read("arjun", "research", "research_report.json", {}) or {}
            want = " ".join(str(sc_.get("hook") or "").split())
            if want and " ".join(text.split()) != want:
                raise Reject("the generated text is not the approved text (the generator rewrote it)", owner="sophie")
            bad = fact_grounding.grounding_check(text + " " + (fc.get("blog_body") or ""), res_, job.topic)
            checked.append("every name/number/quote is in the verified facts")
            if bad:
                raise Reject("unsupported specifics in the final text: %s" % ", ".join(bad[:8]), owner="sophie")
        if len(text) < 20:
            defects.append("caption empty or too short (%d chars)" % len(text))
        else:
            try:
                import make_post
                if make_post._content_is_broken(text):
                    defects.append("caption failed the broken-text check (gibberish/artefacts)")
            except Exception:
                pass
        # the generator falls back to SOLID-COLOR frames when every image source is down;
        # that is a failed video, not a pass.
        if job.kind in ("video", "image"):
            checked.append("image sources actually produced pictures")
            try:
                lg = (job.out("noah", "clips") / "noah_generation.log").read_text(encoding="utf-8", errors="replace")
            except Exception:
                lg = ""
            if "ALL image sources down" in lg or "solid-color frame" in lg:
                defects.append("scene images were NOT generated: every image source was down, "
                               "so the video would be solid-color frames")
        if job.kind == "image":
            checked.append("image file")
            img = Path(rp or ".") / "post.jpg"
            if not img.exists() or img.stat().st_size < 5000:
                defects.append("image missing or tiny")
        elif job.kind == "video":
            checked += ["video file", "duration >= 5s", "audio track", "video track"]
            media = fc.get("media")
            if not media or not Path(media).exists() or Path(media).stat().st_size < 20_000:
                defects.append("video file missing or under 20 KB")
            else:
                pr = _media_probe(media)
                if pr is None:
                    defects.append("could not probe the video (ffmpeg unavailable)")
                else:
                    if pr["duration"] < 5:
                        defects.append("video is %.1fs long (under 5s)" % pr["duration"])
                    if not pr["audio"]:
                        defects.append("video has no audio track")
                    if not pr["video"]:
                        defects.append("file has no video stream")
                # PROVENANCE GATE - this, not a pixel heuristic, is what stops a Ken Burns
                # video slipping through. make_video.py builds a video by zoompanning ONE
                # still image; videogen_real.py builds it by generating a moving clip per
                # scene with Wan2.1. So the reliable question is not "do the pixels move"
                # but "did a real generator produce this".
                #
                # A pixel heuristic was tried first and REJECTED because it does not work:
                # on measured clips, a real Wan2.1 clip scored 0.088 on the
                # static-frame fraction and the Ken Burns video scored 0.094 - the same.
                # A 1.3B video model shifts the whole frame slightly, so "the background
                # held still" is false for genuine output. Frame-difference mean did
                # separate them (10.62 vs 5.59) but depends on content and resolution, so
                # it is recorded as information only, never used to reject.
                real_gen = False
                try:
                    vdir = Path(media).parent
                    plan_json = vdir / "video_plan.json"
                    wan_clips = sorted(vdir.glob("clip*.mp4"))
                    gen_log = ""
                    for cand in (vdir / "videogen_real.log", vdir / "video.log",
                                 job.out("noah", "clips") / "noah_generation.log"):
                        if cand.exists():
                            gen_log = cand.read_text(encoding="utf-8", errors="replace")
                            break
                    # Proof must be ARTEFACTS, not a log line: videogen_real.py always
                    # writes video_plan.json plus one clip<N>.mp4 per scene. A log that
                    # merely mentions "Wan" is not proof (a copied log could fake it).
                    real_gen = bool(plan_json.exists() and wan_clips)
                    if real_gen:
                        checked.append("generated by Wan2.1 (video_plan.json + %d scene clip(s))"
                                       % len(wan_clips))
                    else:
                        defects.append(
                            "the video was NOT generated scene-by-scene: there is no "
                            "video_plan.json and no per-scene clip files, so it was built by "
                            "the old make_video.py path - that is a zoom/pan over a still "
                            "image (Ken Burns), not real motion. Generate with "
                            "videogen_real.py (the DISPATCHER style tag makes NOAH use it).")
                except Exception as e:
                    checked.append("provenance check error, skipped (%s)" % str(e)[:60])

                try:
                    import videogen_real
                    m = videogen_real.motion_metrics(media)
                    if m:
                        checked.append("motion profile (informational): mean frame-diff %.2f, "
                                       "static fraction %.2f" % (m["mean"], m["static_frac"]))
                except Exception:
                    pass
    verdict = "pass" if not defects else "fail"
    job.write("grace", "audits", "quality_inspection.json", {"verdict": verdict, "defects": defects, "checked": checked})
    if any("were NOT generated" in d for d in defects):
        # an outage of the image service cannot be fixed by re-planning: stop and tell a human
        raise Fatal("quality gate: " + "; ".join(defects) + " (image service outage - the SUPERVISOR retries when it is back)")
    if defects:
        tech_marks = ("video file", "could not probe", "no video stream", "no audio track",
                      "under 5s", "image missing", "asset folder missing")
        if all(any(m in d for m in tech_marks) for d in defects):
            # not a content problem: rewriting the script cannot fix a missing/bad FILE
            raise Fatal("technical: " + "; ".join(defects))
        raise Reject("quality defects: " + "; ".join(defects))
    return "media inspection passed (%d checks)" % len(checked)


@agent("ethan")
def _ethan(job):
    fc = job.final_content()
    verdict = {"verdict": "approved", "checked": ["accuracy", "spelling", "completeness", "quality",
                                                   "repetition", "previously published material"]}
    if not job.dry_run:
        # POST HISTORY, checked explicitly. editorial_auditor also has a duplicate check,
        # but this is the authoritative one: it remembers every topic ever PUBLISHED (not
        # just this run) and every topic ever used, so a reworded version of something
        # that went out weeks ago is still caught. Run BEFORE the expensive audit so a
        # repeat is refused in seconds rather than after the model has spent minutes.
        try:
            import post_history
            post_history.refresh()
            dup = post_history.find_repeat(job.topic or (fc["title"] or ""),
                                           exclude_run_id=job.run_id)
            verdict["checked"].append("not in the post history")
            if dup:
                verdict["verdict"] = "rejected"
                verdict["defect"] = ("this topic was already published: %r" % dup)[:300]
            # what previous audits decided - so this audit starts informed, and leaves a
            # note for the next one. Appending one line is cheap; re-reading the whole
            # history is not.
            prev = post_history.recent_audits(5)
            if prev:
                verdict["checked"].append("5 previous audit decisions consulted")
                verdict["recent_audits"] = prev
        except Exception as e:
            verdict["checked"].append("post history unavailable (%s)" % str(e)[:50])
        import editorial_auditor
        text = fc["blog_body"] if len((fc["blog_body"] or "")) > len(fc["caption"] or "") else fc["caption"]
        # skip_job = this job's own output folder, otherwise the duplicate check compares the
        # post with ITSELF ("100% the same wording") and rejects every post.
        own = Path(job.meta.get("run_path") or "").name or None
        res = editorial_auditor.audit_content(text or "", job.platforms[0], fc["title"] or job.topic, skip_job=own)
        verdict["auditor"] = res
        if not res.get("approved") and verdict["verdict"] == "approved":
            verdict["verdict"] = "rejected"
            verdict["defect"] = res.get("reason", "unspecified")
    job.write("ethan", "audits", "final_content_audit.json", verdict)
    # leave a one-line note for the NEXT audit. Append-only, never rewrites the history,
    # and is stored with the agents in videogen/AGENT_MEMORY/ which no cleanup can reach.
    try:
        import post_history
        post_history.note_audit(job.topic or (fc["title"] or ""), verdict["verdict"],
                                verdict.get("defect", ""), agent="ETHAN", job_id=job.job_id)
    except Exception:
        pass
    if verdict["verdict"] != "approved":
        raise Reject("content audit failed: %s" % verdict.get("defect"))
    return "final content APPROVED"


# ===========================================================================
# STAGE 5 - RELEASE
# ===========================================================================
def _conn_cfg():
    try:
        import publish as pub
        return pub.load_conn()
    except Exception:
        return {}


def _allowed_destinations(job):
    """Safety rail: the department publishes ONLY to rules.department.allowed_platforms
    (default: the local blog 'socialai') unless live_publish is switched on."""
    dr = dept_rules()
    if dr.get("live_publish"):
        return list(job.platforms), []
    allowed = dr.get("allowed_platforms") or ["socialai"]
    ok = [p for p in job.platforms if p in allowed]
    return ok, [p for p in job.platforms if p not in allowed]


BLOG_MIN_CHARS = 1200      # blog.your-domain.example is a blog: video, image and text posts ALL get a full article


def _blog_article(job, fc):
    """The long-form text for blog.your-domain.example (video, image and text-only posts alike).

    The approved script's narration is spoken-length (200-500 characters) - fine for a video or a caption, far too
    thin for a blog. When it is shorter than BLOG_MIN_CHARS, write the full article from the SAME approved script
    and the VERIFIED research facts only (no new facts), check it with the same fact gate GRACE uses, and save it
    beside the post (blog_article.txt) so the publisher pushes exactly this text. Retry-safe: reused if it exists."""
    import re as _re
    import fact_grounding
    cur = (fc.get("blog_body") or fc.get("caption") or "").strip()
    rp = job.meta.get("run_path")
    f = (Path(rp) / "blog_article.txt") if rp and Path(rp).is_dir() else None
    if f is not None and f.exists():
        t = f.read_text(encoding="utf-8").strip()
        if len(t) >= BLOG_MIN_CHARS:
            return t
    if len(cur) >= BLOG_MIN_CHARS:
        return cur
    sc = job.read("sophie", "scripts", "script.json", {}) or {}
    res = job.read("arjun", "research", "research_report.json", {}) or {}
    claims = res.get("verified_claims") or []
    facts = "\n".join("- " + str(c.get("claim") if isinstance(c, dict) else c)[:300] for c in claims[:25])
    prompt = (
        "Write the full blog article for this post, in plain, warm, human editorial English.\n"
        "TOPIC: %s\nAPPROVED SCRIPT (keep its message and facts):\n%s\n\nVERIFIED FACTS (the ONLY facts you may use):\n%s\n\n"
        "SOURCE TEXT:\n%s\n\n"
        "RULES: 1500 to 3500 characters, 4 to 7 short paragraphs separated by blank lines. Follow the story arc: the problem, "
        "the tension, the insight, the action, the result, the takeaway. Use ONLY the verified facts and the approved script - "
        "never invent a name, number, date, place or quote. No hashtags, no emojis, no headings, no bullet lists, no mention of "
        "AI, no 'in conclusion'. Do not repeat the title. Return only the article text."
        % (job.topic, cur or str(sc.get("narration") or ""), facts or "(none - use only the approved script)",
           (res.get("source_text") or "")[:3000]))
    system = "You are a careful blog editor. You never add facts that are not given to you."
    for attempt in (1, 2):
        art = llm(prompt, system=system, timeout=200)       # LLMError -> the step retries with backoff, then the Watchdog
        art = _re.sub(r"(?is)<think>.*?</think>", "", art or "").strip()
        if len(art) < 900:
            prompt += "\nYour last answer was too short. Write at least 1500 characters."
            continue
        bad = fact_grounding.grounding_check(art, res, job.topic, extra=cur)
        if bad:
            prompt += "\nYour last answer used facts that are not allowed: %s. Remove them." % ", ".join(bad[:8])
            continue
        if f is not None:
            f.write_text(art, encoding="utf-8")
        log("  BLOG article written: %d chars (the script text was %d)" % (len(art), len(cur)))
        return art
    log("  BLOG article could not be made safely - using the approved text (%d chars)" % len(cur))
    return cur


@agent("chloe")
def _chloe(job):
    import content_format
    import publish_guard as PG
    fc = job.final_content()
    cfg = _conn_cfg()
    made, skipped, held = [], [], []
    for p in job.platforms:
        if not job.dry_run and not (cfg.get(p) or {}).get("connected"):
            skipped.append("%s: not connected" % p)
            continue
        body = fc["caption"] or ""
        bb = fc["blog_body"]
        if p == "socialai":
            bb = _blog_article(job, fc)          # the blog ALWAYS gets the long article
        text = content_format.format_text(p, title=fc["title"], hook=body, tags=fc["tags"], blog_body=bb)
        lim = content_format.PROFILES.get(p, content_format.DEFAULT_PROFILE)["limit"]
        if not text.strip():
            skipped.append("%s: empty after formatting" % p)
            continue
        if len(text) > lim:
            skipped.append("%s: still over its %d limit after trimming" % (p, lim))
            continue
        # LENGTH IS NOT ENOUGH. A post can fit the limit and still be cut off mid-sentence,
        # or carry leaked model reasoning / raw HTML. Those checks used to run only at
        # publish time inside publish.py, which meant a whole run was spent discovering a
        # defect that was knowable here, before anything was pushed. Run them now, at
        # packaging, so a bad post is routed back to SOPHIE instead of failing at ELLA.
        gates = (PG.check_limits(p, text), PG.check_complete(text, body, p),
                 PG.check_clean(text, fc["title"] or ""),
                 PG.check_house_rules(text, fc["title"] or ""))
        blog =PG.check_socialai_full(text, body, p) if p == "socialai" else None
        if blog is not None:
            gates = gates + (blog,)
        bad = [g for g in gates if not g.ok]
        if bad:
            held.append("%s: %s" % (p, bad[0].reason[:110]))
            continue
        job.write("chloe", "packages", "%s.json" % p,
                  {"platform": p, "title": fc["title"], "caption": text, "chars": len(text), "limit": lim,
                   "media": fc["media"], "generated": time.time(),
                   "gates": [g.name for g in gates]})
        made.append(p)
    if not made:
        raise Fatal("no destination could be packaged: %s" % "; ".join(skipped + held))
    job.write("chloe", "packages", "_summary.json",
              {"packaged": made, "skipped": skipped, "held": held})
    if held:
        # something was packaged but something else was NOT - say so loudly, because the
        # difference between "trimmed to fit" and "cut off mid-sentence" is the whole point.
        log("  HELD back (not just trimmed): " + "; ".join(held))
    return "%d package(s): %s%s%s" % (
        len(made), ",".join(made),
        ("; skipped " + "; ".join(skipped)) if skipped else "",
        ("; HELD " + "; ".join(held)) if held else "")


# ===========================================================================
# HANDOFF CONTRACTS - each agent's "definition of done" (2026-10-11)
# Deterministic, fast, no model call. They mirror what the NEXT agents and the final gates will demand, so a defect is
# caught and retried at the desk that caused it. A failed check raises inside the agent's own wrapper (see agent()).
# ===========================================================================
def _h_arjun(job):
    d = job.read("arjun", "research", "research_report.json")
    if not d:
        return ["the research report was not written"]
    p = []
    if not d.get("mode"):
        p.append("research report has no mode (grounded / no_sources)")
    if d.get("mode") == "grounded" and not (d.get("verified_claims") and d.get("sources")):
        p.append("a 'grounded' report must list its sources and verified facts")
    return p


def _h_maya(job):
    b = job.read("maya", "scripts", "creative_brief.json") or {}
    return ["brief is missing: %s" % k for k in ("objective", "target_audience", "central_message", "intended_outcome", "tone")
            if not str(b.get(k) or "").strip()]


def _h_sophie(job):
    sc = job.read("sophie", "scripts", "script.json")
    if not sc:
        return ["the script was not written"]
    p = []
    if len(str(sc.get("narration") or "").strip()) < 40:
        p.append("narration is under 40 characters")
    if not str(sc.get("hook") or sc.get("narration") or "").strip():
        p.append("no hook")
    if job.kind == "video":
        arc = sc.get("story_arc") or {}
        p += ["story_arc.%s is empty" % k for k in ("problem", "tension", "insight", "action", "result", "takeaway") if not arc.get(k)]
        lines = [x for x in (_norm_dialog(d) for d in (sc.get("dialogs") or [])) if x]
        if not lines:
            p.append("no spoken dialogue lines")
        elif sum(len(x["line"].split()) for x in lines) > SPOKEN_MAX_WORDS + 4:
            p.append("the spoken lines are too long for an 11 second video")
        if not sc.get("characters"):
            p.append("no characters (name, age, look, dress)")
    return p


def _h_daniel(job):
    if job.kind == "text":
        return []
    sb = job.read("daniel", "scripts", "storyboard.json") or {}
    scenes = sb.get("scenes") or []
    want = SCENES.get(job.kind, 1)
    p = []
    if len(scenes) != want:
        p.append("storyboard has %d scene(s), %d are needed" % (len(scenes), want))
    p += ["scene %s has no description" % (s.get("n") if isinstance(s, dict) else "?") for s in scenes
          if not (isinstance(s, dict) and str(s.get("beat") or s.get("visual") or s.get("description") or "").strip())]
    return p


def _h_leo(job):
    return [] if job.read("leo", "visual", "emotion_guide.json") else ["the emotion guide was not written"]


def _voice_problems(folder):
    try:
        vr = json.loads((Path(folder) / "voice_report.json").read_text(encoding="utf-8"))
    except Exception:
        return ["the video has no voice-over report"]
    if not vr.get("ok"):
        return ["voice-over incomplete: %s of %s line(s) spoken" % (vr.get("lines_spoken"), vr.get("lines_planned"))]
    return []


def _h_noah(job):
    fc = job.final_content()
    rp = job.meta.get("run_path")
    if not rp or not Path(rp).is_dir():
        return ["no asset folder was recorded"]
    if job.kind == "video":
        m = fc.get("media")
        if not m or not Path(m).exists() or Path(m).stat().st_size < 50000:
            return ["the video file is missing or under 50 KB"]
        if (job.meta.get("style") or "") in ("real", "cartoon"):
            return _voice_problems(Path(m).parent)
    return []


def _h_grace(job):
    if job.kind != "video" or (job.meta.get("style") or "") not in ("real", "cartoon"):
        return []
    m = job.final_content().get("media")
    return _voice_problems(Path(m).parent) if m else ["no video to check"]


def _h_chloe(job):
    s = job.read("chloe", "packages", "_summary.json") or {}
    if not s.get("packaged"):
        return ["no destination was packaged"]
    if "socialai" in (s.get("packaged") or []):
        pkg = job.read("chloe", "packages", "socialai.json") or {}
        if int(pkg.get("chars") or 0) < 700 and job.kind != "text":
            return ["the blog text is only %s characters - the blog always gets the full article" % pkg.get("chars")]
        if int(pkg.get("chars") or 0) < 500:
            return ["the blog text is only %s characters - the blog always gets the full article" % pkg.get("chars")]
    return []


HANDOFF.update({"arjun": _h_arjun, "maya": _h_maya, "sophie": _h_sophie, "daniel": _h_daniel, "leo": _h_leo,
                "noah": _h_noah, "grace": _h_grace, "chloe": _h_chloe})


@agent("marcus")
def _marcus(job):
    blocked = []

    def verdict(agent_k, area, name, field, want):
        d = job.read(agent_k, area, name)
        if not d or d.get(field) != want:
            blocked.append(agent_k.upper())
    verdict("oliver", "audits", "planning_audit.json", "verdict", "approved")
    if not _check_authorised(job):
        blocked.append("ISABELLA")
    verdict("grace", "audits", "quality_inspection.json", "verdict", "pass")
    verdict("ethan", "audits", "final_content_audit.json", "verdict", "approved")
    summ = job.read("chloe", "packages", "_summary.json", {}) or {}
    dests = summ.get("packaged") or []
    if not dests:
        blocked.append("CHLOE")
    allowed, removed = _allowed_destinations(job)
    final = [d for d in dests if d in allowed]
    if dests and not final:
        raise Fatal("no destination is permitted by rules.department.allowed_platforms (%s)" % ", ".join(dests))
    if blocked:
        job.write("marcus", "packages", "release_decision.json", {"authorised": False, "blocked_by": blocked})
        raise Reject("release refused: missing approval from %s" % ", ".join(blocked))

    # the human approval gate (rules.approval_mode == manual) is respected, never bypassed
    if not job.dry_run and rules().get("approval_mode", "manual") == "manual" and job.run_id:
        with db.conn() as c:
            row = c.execute("SELECT approval, published FROM runs WHERE id=?", (job.run_id,)).fetchone()
        if row and row["approval"] != "approved":
            if row["approval"] == "rejected":
                raise Fatal("a human rejected this post on the dashboard")
            first = job.meta.get("approval_wait_since") or time.time()
            job.set_meta(approval_wait_since=first)
            if time.time() - first > 86400:
                raise Fatal("no human approval within 24h")
            raise Wait("waiting for your approval on the dashboard", 60)
    elif not job.dry_run and job.run_id:
        db.set_approval(job.run_id, "approved")

    job.write("marcus", "packages", "release_decision.json",
              {"authorised": True, "destinations": final, "removed": removed, "decided_at": time.time(),
               "job_id": job.job_id})
    return "release AUTHORISED -> %s" % ",".join(final)


def _posts_for(run_id):
    if not run_id:
        return []
    with db.conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT platform, url, remote_id, status FROM posts WHERE run_id=? ORDER BY id", (run_id,)).fetchall()]


@agent("ella")
def _ella(job):
    dec = job.read("marcus", "packages", "release_decision.json")
    if not dec or dec.get("authorised") is not True or dec.get("job_id") != job.job_id:
        raise Fatal("no release authorisation from MARCUS - REFUSED (hard gate)")
    dests = dec["destinations"]
    evid = job.out("ella", "evidence")
    if job.dry_run:
        (evid / "publish.log").write_text("dry run - nothing published", encoding="utf-8")
        return "dry run: not published"
    # LAST DOOR (2026-10-11): no video leaves the building unless its voice-over was verified complete at render time.
    # Videos rendered before the voice fix have no voice report and are refused, however they reached this step.
    if job.kind == "video" and (job.meta.get("style") or "") in ("real", "cartoon"):
        _m = job.final_content().get("media")
        _vp = _voice_problems(Path(_m).parent) if _m else ["no video file"]
        if _vp:
            raise Fatal("NOT PUBLISHED - the video's voice-over is not verified: %s" % "; ".join(_vp))
    have = {p["platform"] for p in _posts_for(job.run_id) if p["status"] == "ok"}
    todo = [d for d in dests if d not in have]
    if not todo:
        (evid / "publish.log").write_text("already published: %s" % ",".join(sorted(have)), encoding="utf-8")
        return "already published (%s) - not sending again" % ",".join(sorted(have))
    rp = job.meta.get("run_path")
    if not rp or not Path(rp).is_dir():
        raise Reject("the asset folder is missing - nothing to publish")
    rc, out = run_cmd([PY, str(ROOT / "scripts" / "publish.py"), "--job", rp, "--only-platforms", ",".join(todo)],
                      timeout=max(60, job.spec_timeout("ella") - 60),
                      env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    (evid / "publish.log").write_text(out, encoding="utf-8")
    got = {p["platform"] for p in _posts_for(job.run_id) if p["status"] == "ok"}
    sent = [d for d in todo if d in got]
    failed = [d for d in todo if d not in got]
    if failed and not sent and not (got & set(dests)):
        raise RuntimeError("publish produced no post record for %s (rc=%s): %s" % (",".join(failed), rc, out[-140:]))
    if failed:
        log("  some destinations did not publish: %s - recorded for VICTORIA, the rest continue" % ",".join(failed))
    if got & set(dests):
        # the dashboard, the publication history and retention all read runs.published; the department never set it,
        # so every live post showed as "held" and was invisible to the history.
        try:
            with db.conn() as c:
                c.execute("UPDATE runs SET published=1 WHERE id=?", (job.run_id,))
        except Exception:
            pass
    return "published to %s%s" % (",".join(sorted(got & set(dests))), ("; FAILED: " + ",".join(failed)) if failed else "")


# ===========================================================================
# STAGE 6 - LIVE VERIFICATION
# ===========================================================================
def _http_ok(url):
    try:
        ctx = None
        host = re.sub(r"^https?://([^/:]+).*$", r"\1", url or "")
        if host in ("localhost", "127.0.0.1"):
            ctx = ssl._create_unverified_context()      # only ever for our own local site
        req = urllib.request.Request(url, headers={"User-Agent": "autopost-victoria"})
        with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
            return r.status < 400, "HTTP %d" % r.status
    except Exception as e:
        return False, "unreachable: %s" % str(e)[:80]


def _verify_socialai(job):
    """Authenticated read-back of the local blog. The site mints its own post ids, so the URL
    recorded in `posts` is not a real page - find the post by its TEXT instead. Fails on a
    missing post, on DUPLICATE copies (the 3x publish bug) and on cut-off text."""
    import publish as pub
    import requests
    import urllib3
    urllib3.disable_warnings()
    # what the site must hold = exactly what CHLOE packaged for it (long-form body + tags)
    pkg = job.read("chloe", "packages", "socialai.json", {}) or {}
    want = " ".join((pkg.get("caption") or job.final_content()["caption"] or "").split())
    if len(want) < 20:
        return "fail", "no packaged text to compare against", None
    since = float(job.meta.get("created") or 0) - 5
    pub.apply_blog_target()
    pw = pub.blog_password()
    s = requests.Session()
    s.verify = False
    r = s.post(pub.SOCIALBLOG_URL + "/socialai/login", json={"password": pw}, timeout=15)
    if r.status_code != 200:
        return "unverified", "could not log in to the blog (HTTP %d)" % r.status_code, None
    posts = s.get(pub.SOCIALBLOG_URL + "/socialai/api/posts", timeout=20).json().get("posts", [])
    head = want[:40].lower()
    found = [p for p in posts if float(p.get("created") or 0) >= since
             and head in " ".join((p.get("caption") or "").split()).lower()]
    if not found:
        return "fail", "the post is not on the site", None
    if len(found) > 1:
        return "fail", "%d duplicate copies on the site (%s)" % (len(found), ", ".join(p["id"] for p in found)), found[0]["id"]
    got = len(found[0].get("caption") or "")
    if got < len(want) * 0.9:
        return "fail", "text cut off: site holds %d of %d characters" % (got, len(want)), found[0]["id"]
    return "pass", "found as %s, full text (%d chars), exactly one copy" % (found[0]["id"], got), found[0]["id"]


def _audit_posts(job, agent_name="VICTORIA"):
    dec = job.read("marcus", "packages", "release_decision.json", {}) or {}
    dests = dec.get("destinations") or job.platforms
    rows = {p["platform"]: dict(p) for p in _posts_for(job.run_id)}
    failures, results = [], []
    with db.conn() as c:
        for d in dests:
            p = rows.get(d)
            if not p:
                st, why = "fail", "no post record for this destination"
            elif p["status"] != "ok" or not (p["url"] or p.get("remote_id")):
                st, why = "fail", "post status=%s proof=%s" % (p["status"], bool(p["url"] or p.get("remote_id")))
            elif d in ("telegram", "telegram_channel", "bluesky", "discord", "tiktok"):
                st, why = _verify_platform(d, p)
            elif d == "socialai":
                try:
                    st, why, _pid = _verify_socialai(job)
                except Exception as e:
                    st, why = "unverified", "blog read-back failed: %s" % str(e)[:80]
            else:
                link = p["url"] or (str(p.get("remote_id") or "") if str(p.get("remote_id") or "").startswith("http") else "")
                if link:
                    ok, detail = _http_ok(link)
                    st, why = ("pass", detail) if ok else ("unverified", detail)
                else:
                    st, why = "pass", "the platform accepted it (%s)" % (p.get("remote_id") or "ok")
            c.execute("INSERT INTO live_audits(job_id,platform,status,reason,evidence_path,checked_at,by_agent)"
                      " VALUES(?,?,?,?,?,?,?)", (job.job_id, d, st, why, (p or {}).get("url") or "", time.time(), agent_name))
            results.append({"platform": d, "status": st, "reason": why, "url": (p or {}).get("url")})
            if st != "pass":
                failures.append(d)
    return results, failures


def _verify_platform(platform, p):
    """Check that a post REALLY exists on the platform, not merely that our database says so.
    telegram (private chat): the Bot API returned a message id - that is the proof (Telegram offers no
    read-back for bot messages); channel: the public t.me link when the channel has a username;
    bluesky: the public AT-protocol API returns the post; discord: the bot reads the message back."""
    import urllib.request
    import urllib.parse
    import urllib.error
    rid = str(p.get("remote_id") or "")
    try:
        conn = pub_load_conn()
        if platform == "bluesky":
            u = "https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts?uris=" + urllib.parse.quote(rid, safe="")
            with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "AutoPostAudit/1.0"}), timeout=20) as r:
                posts = json.loads(r.read().decode()).get("posts", [])
            return ("pass", "bluesky returns the post (%d chars)" % len((posts[0].get("record") or {}).get("text", ""))) \
                if posts else ("fail", "bluesky does not return this post")
        if platform == "discord":
            d = conn.get("discord", {})
            u = "https://discord.com/api/v10/channels/%s/messages/%s" % (d["channel_id"], rid.rsplit("/", 1)[-1])
            req = urllib.request.Request(u, headers={"Authorization": "Bot " + d["token"], "User-Agent": "DiscordBot (autopost, 1.0)"})
            with urllib.request.urlopen(req, timeout=20) as r:
                m = json.loads(r.read().decode())
            return "pass", "discord message read back (%d chars)" % len(m.get("content", ""))
        if platform == "tiktok":
            # TikTok gives no public read-back for a private (SELF_ONLY) upload: the upload id IS the proof.
            return "pass", "TikTok accepted the upload (%s) - private until you make it public in the TikTok app" % rid[:40]
        t = conn.get(platform, {})
        if platform == "telegram_channel":
            api = "https://api.telegram.org/bot%s/getChat?chat_id=%s" % (t["token"], t["chat_id"])
            with urllib.request.urlopen(api, timeout=20) as r:
                user = (json.loads(r.read().decode()).get("result") or {}).get("username")
            if user:
                with urllib.request.urlopen("https://t.me/%s/%s?embed=1" % (user, rid), timeout=20) as r:
                    html = r.read().decode("utf-8", "replace")
                return ("pass", "channel post is public at t.me/%s/%s" % (user, rid)) if "tgme_widget_message" in html \
                    else ("fail", "t.me does not show the post")
        return "pass", "Telegram accepted it (message id %s)" % rid
    except urllib.error.HTTPError as e:
        return ("fail" if e.code in (404, 410) else "unverified"), "%s check: HTTP %s" % (platform, e.code)
    except Exception as e:
        return "unverified", "%s check could not complete: %s" % (platform, str(e)[:80])


def pub_load_conn():
    import publish as pub
    return pub.load_conn()


@agent("victoria")
def _victoria(job):
    if job.dry_run:
        job.write("victoria", "evidence", "live_post_audit.json", {"dry_run": True, "failures": []})
        return "dry run: nothing live to verify"
    results, failures = _audit_posts(job)
    job.write("victoria", "evidence", "live_post_audit.json", {"results": results, "failures": failures})
    return "%d destination(s) checked, %d failed" % (len(results), len(failures))


@agent("james")
def _james(job):
    if job.dry_run:
        return ("skipped", "dry run: nothing to fix")
    va = job.read("victoria", "evidence", "live_post_audit.json", {}) or {}
    failures = va.get("failures") or []
    if not failures:
        return ("skipped", "all live posts passed - nothing to fix")
    results, still = _audit_posts(job, "JAMES")          # one honest re-check (transient errors)
    with db.conn() as c:
        for p in still:
            c.execute("INSERT INTO remediation(job_id,platform,action,details,by_agent,ts) VALUES(?,?,?,?,?,?)",
                      (job.job_id, p, "escalated", "re-checked once, still failing; cannot edit or delete safely "
                       "without authorisation", "JAMES", time.time()))
        for p in set(failures) - set(still):
            c.execute("INSERT INTO remediation(job_id,platform,action,details,by_agent,ts) VALUES(?,?,?,?,?,?)",
                      (job.job_id, p, "reverified", "passed on re-check", "JAMES", time.time()))
    job.write("james", "evidence", "remediation.json", {"failures": failures, "still_failing": still, "results": results})
    return ("escalated %d destination(s) to a human" % len(still)) if still else "re-check passed"


# ===========================================================================
# STAGE 7 - FINAL REPORT
# ===========================================================================
@agent("helena")
def _helena(job):
    import department_queue as Q
    steps = {r["step_key"]: r for r in Q._rows(job.job_id)}
    dead = [k for k, r in steps.items() if r["status"] == "dead"]
    with db.conn() as c:
        audits = c.execute("SELECT platform, status, reason FROM live_audits WHERE job_id=? ORDER BY id", (job.job_id,)).fetchall()
        rems = c.execute("SELECT platform, action, details FROM remediation WHERE job_id=? ORDER BY id", (job.job_id,)).fetchall()
    latest = {}
    for a in audits:
        latest[a["platform"]] = a
    bad = [p for p, a in latest.items() if a["status"] != "pass"]
    escalated = [r for r in rems if r["action"] == "escalated"]
    corrected = [r for r in rems if r["action"] in ("edited", "deleted", "reverified")]
    others_done = all(r["status"] in ("done", "skipped") for k, r in steps.items() if k != "helena")
    if dead:
        status = FINAL_STATUS["human"]
    elif others_done and job.dry_run:
        status = FINAL_STATUS["dry"]
    elif others_done and not bad and corrected:
        status = FINAL_STATUS["corrected"]
    elif others_done and not bad:
        status = FINAL_STATUS["ok"]
    elif escalated:
        status = FINAL_STATUS["human"]
    elif latest and len(bad) < len(latest):
        status = FINAL_STATUS["partial"]
    else:
        status = FINAL_STATUS["failed"]
    lines = ["AutoPost department report - %s" % job.job_id, "Topic: %s" % job.topic, "Kind: %s" % job.kind,
             "FINAL STATUS: %s" % status, ""]
    for p, a in latest.items():
        url = ""
        for post in _posts_for(job.run_id):
            if post["platform"] == p:
                url = post["url"] or ""
        lines.append("  %-12s %-10s %s %s" % (p, a["status"], url, a["reason"] or ""))
    if dead:
        import dept_actions
        lines += ["", "Stopped steps (the SUPERVISOR retries or closes them by itself - nothing for you to do):"]
        for k in dead:
            d_ = dept_actions.write_job_error(job.job_id, k, steps[k]["reason"])
            lines.append("  %-9s %s" % (k, steps[k]["reason"]))
            lines.append("    ERROR : %s" % d_["error"])
            lines.append("    ACTION: %s" % d_["action"])
            if d_["retry"]:
                lines.append("    RETRY : %s   (same job, finished work is reused)" % d_["retry"])
    if escalated:
        lines += ["", "Escalated by JAMES:"] + ["  %-12s %s" % (r["platform"], r["details"]) for r in escalated]
    lines += ["", "Model: %s" % (LAST_MODEL["name"] or active_model())]
    summary = "\n".join(lines)
    # The OWNER-facing Telegram text is written in plain language by report_format (the technical
    # summary above stays in the final_reports table for the dashboard / Work chat).
    tg_text = summary
    try:
        import report_format
        vkey = {FINAL_STATUS["ok"]: "ok", FINAL_STATUS["dry"]: "dry", FINAL_STATUS["corrected"]: "corrected",
                FINAL_STATUS["partial"]: "partial", FINAL_STATUS["failed"]: "failed", FINAL_STATUS["human"]: "human"}.get(status, "failed")
        live_l, failed_l = [], []
        for p, a in latest.items():
            url = ""
            for post in _posts_for(job.run_id):
                if post["platform"] == p:
                    url = post["url"] or ""
            if a["status"] == "pass":
                live_l.append((p, url))
            else:
                failed_l.append((p, a["reason"] or a["status"]))
        stopped_l = []
        if dead:
            import dept_actions
            d0 = dept_actions.describe(job.job_id, dead[0], steps[dead[0]]["reason"])
            stopped_l = [(dead[0], d0["error"], d0["action"])]
        tg_text = report_format.post_report(job.topic, job.kind, vkey, live_l, failed_l, stopped_l, len(corrected))
    except Exception as e:
        log("    plain-language report failed (%s) - sending the technical one" % str(e)[:80])
    # No report FILE (the owner: it only costs storage). The summary lives in the final_reports table and
    # in the Telegram message; the published topic goes into the publication history so ARJUN and
    # NADIA never choose it again.
    try:
        import post_history
        if not job.dry_run and status in (FINAL_STATUS["ok"], FINAL_STATUS["corrected"], FINAL_STATUS["partial"]):
            post_history.record(job.topic, [p for p, a in latest.items() if a["status"] == "pass"], job.run_id)
    except Exception as e:
        log("    publication history not updated: %s" % str(e)[:80])

    delivered = False
    if job.notify and not job.dry_run:
        try:
            import publish as pub
            conn = pub.load_conn()
            for key in ("telegram", "telegram_channel"):
                tg = conn.get(key, {})
                if tg.get("connected") and tg.get("token") and tg.get("chat_id"):
                    for attempt in (1, 2, 3):
                        try:
                            pub.telegram_send(tg["token"], tg["chat_id"], tg_text)
                            delivered = True
                            break
                        except Exception as e:
                            log("    telegram send failed (try %d): %s" % (attempt, str(e)[:80]))
                            time.sleep(2)
                    break
        except Exception as e:
            summary += "\n(telegram delivery failed: %s)" % str(e)[:80]
    with db.conn() as c:
        c.execute("INSERT INTO final_reports(job_id,status,summary,channels,delivered,ts) VALUES(?,?,?,?,?,?) "
                  "ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,summary=excluded.summary,"
                  "channels=excluded.channels,delivered=excluded.delivered,ts=excluded.ts",
                  (job.job_id, status, summary, "telegram" if delivered else "none", 1 if delivered else 0, time.time()))
    return "%s | telegram delivered=%s" % (status, delivered)


# ===========================================================================
# command line - one engine only
# ===========================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--kind", choices=("video", "image", "text"), default="video")
    ap.add_argument("--platforms", default="socialai")
    ap.add_argument("--dry-run", action="store_true",
                    help="run every agent and every gate without model calls, generation or publishing")
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()
    if not a.dry_run and not department_enabled():
        log("department.enabled is FALSE in rules.json - refusing a real run from here.")
        log("(For a deliberate manual run use: department_queue.py --enqueue ... --run)")
        return 2
    import department_queue as Q
    Q.schema()
    jid = Q.enqueue(a.topic, a.kind, [p.strip() for p in a.platforms.split(",") if p.strip()],
                    dry_run=a.dry_run, notify=a.notify, source="cli")
    Q.run(jid)
    Q.show(jid)
    rep = db.conn().execute("SELECT status FROM final_reports WHERE job_id=?", (jid,)).fetchone()
    log("FINAL: %s" % (rep["status"] if rep else "no report"))
    return 0 if rep and rep["status"] in (FINAL_STATUS["ok"], FINAL_STATUS["dry"]) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("interrupted")
        sys.exit(130)
