# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""FACT GROUNDING - the rule that stops the system publishing invented facts.

THE PROBLEM (2026-10-10, first real run): asked "what is the longest lived single-cell organism?",
the writer invented "Methuselah, a marine microbe, 1,000 years"; then NOAH's text generator
invented a DIFFERENT organism ("Thiocapsa roseus, 150 years"). Both false, contradicting each
other, and every agent approved it because research was always marked "unverified" and nobody
checked the facts.

THE RULE: a post may only state a name, number, date, place, statistic or quote that is
  (a) in the topic itself, or
  (b) in the SOURCE TEXT ARJUN fetched for this job (Wikipedia, verbatim).
With no usable source, the post may make NO factual claims at all (reflective / general only).

  research_topic(topic)            -> ARJUN: fetch real sources, verbatim claims
  grounding_check(text, res, topic)-> list of unsupported specifics (empty = grounded)
  facts_prompt(res)                -> the exact instruction block SOPHIE must follow
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA = {"User-Agent": "AutoPostResearch/1.0 (https://blog.your-domain.example; fact-checking a small blog)"}
API = "https://en.wikipedia.org/w/api.php"

_STOP = set("""a an the this that these those it its in on at to of for and but or so if then than as is are was were be
been being do does did done not no yes you your we our they their he she his her him them my me us what when why how
who whom which where while with without into over under about after before during between through just only also even
still here there now today tonight imagine remember consider think know see look let because since though although
however therefore thus yet very more most less least many much some any all each every both either neither one two
three first second third new old good great little big small long short high low true false real best better worse
monday tuesday wednesday thursday friday saturday sunday january february march april may june july august september
october november december""".split())
_MAGNITUDE = r"hundred|thousand|million|billion|trillion|dozen|decades?|centuries|millennia"


def _norm(t):
    return re.sub(r"[^a-z0-9 ]+", " ", (t or "").lower().replace("’", "'")).replace("  ", " ").strip()


def _words(t):
    return {w for w in _norm(t).split() if len(w) >= 4 and w not in _STOP}


_CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "research_cache.json"
_last_call = [0.0]


def _cache():
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _get(params):
    """Polite Wikipedia client: identifying User-Agent, >=1 s between calls, obeys Retry-After on 429,
    and a disk cache so the same query is never fetched twice."""
    key = json.dumps(dict(params), sort_keys=True)
    cache = _cache()
    if key in cache:
        return cache[key]
    url = API + "?" + urllib.parse.urlencode(dict(params, format="json"))
    last = None
    for attempt in range(4):
        wait = 1.0 - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        try:
            _last_call[0] = time.time()
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                d = json.loads(r.read().decode("utf-8"))
            cache[key] = d
            try:
                _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                _CACHE_FILE.write_text(json.dumps(cache), encoding="utf-8")
            except Exception:
                pass
            return d
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                time.sleep(min(30, int(e.headers.get("Retry-After", "5")) + 1 + attempt * 3))
                continue
            raise
    raise last


def _search(q, n=4):
    try:
        d = _get({"action": "query", "list": "search", "srsearch": q, "srlimit": n})
        return [x["title"] for x in d["query"]["search"]]
    except Exception:
        return []


def _extract(title, chars=9000):
    try:
        d = _get({"action": "query", "prop": "extracts", "explaintext": 1, "exchars": chars,
                  "redirects": 1, "titles": title})
        pg = list(d["query"]["pages"].values())[0]
        return pg.get("extract") or ""
    except Exception:
        return ""


def _pick_titles(topic, titles):
    """Choose WHICH real Wikipedia article(s) are about the topic. The model only picks from the titles the
    search returned (it cannot invent a source); the facts still come verbatim from the page text."""
    if not titles:
        return []
    kw = _words(topic)
    try:
        nl = chr(10)
        prompt = ("Topic: " + topic + nl + "Candidate Wikipedia articles:" + nl
                  + nl.join("- " + t for t in titles) + nl + nl
                  + "Which of these articles are DIRECTLY about the subject of the topic (not merely sharing a word)? "
                  + 'Reply as JSON {"titles": [up to 2 exact titles from the list]} or {"titles": []} if none fits.')
        body = json.dumps({"model": "qwen2.5:14b", "stream": False, "format": "json",
                           "options": {"temperature": 0, "num_predict": 120}, "prompt": prompt}).encode()
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            out = json.loads(json.loads(r.read().decode())["response"])
        picked = [t for t in out.get("titles", []) if t in titles]
        if picked or out.get("titles") == []:
            return picked
    except Exception:
        pass
    # fallback (model unavailable): a page whose title is the topic's own 2+ word phrase
    return [t for t in titles if (lambda w: w and w <= kw and len(w) >= 2)(_words(t))][:2]


def _subject_terms(topic):
    """2-3 SHORT encyclopedia article names for the topic's subject ('How geomagnetic storms affect our
    sleep...' -> 'Geomagnetic storm', 'Sleep'). A whole headline fed to Wikipedia search returns junk
    ('List of Equinox episodes'), which left most jobs with no sources and the fact check rejecting every
    script. The model only proposes SEARCH TERMS; the facts still come verbatim from the fetched page."""
    try:
        nl = chr(10)
        prompt = ("Topic: " + topic + nl + "Give 2 or 3 short Wikipedia article titles for the SUBJECT of this topic "
                  "(the science / thing it is about, not the headline wording). "
                  'Reply only JSON: {"terms": ["Geomagnetic storm", "Sleep"]}')
        body = json.dumps({"model": "qwen2.5:14b", "stream": False, "format": "json",
                           "options": {"temperature": 0, "num_predict": 80}, "prompt": prompt}).encode()
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            out = json.loads(json.loads(r.read().decode())["response"])
        return [str(t).strip() for t in (out.get("terms") or []) if 2 <= len(str(t).strip()) <= 60][:3]
    except Exception:
        return []


def research_topic(topic, max_sources=3):
    """Fetch real, relevant source text for the topic. Never invents anything: claims are verbatim
    sentences from the sources. mode = 'grounded' (usable sources) or 'no_sources'."""
    kw = _words(topic)
    queries = _subject_terms(topic)                     # subject first: it finds the real articles
    queries += [topic, " ".join(sorted(kw))] if kw else [topic]
    if len(kw) > 2:
        longest = sorted(kw, key=len, reverse=True)
        queries += [" ".join(longest[:3]), " ".join(longest[:2])]
    titles = []
    for q in queries:
        for t in _search(q):
            if t not in titles:
                titles.append(t)
    sources, texts = [], []
    chosen = _pick_titles(topic, titles[:8]) or []
    for t in chosen[:max_sources]:
        ex = _extract(t)
        if ex and len(ex) > 400:
            sources.append({"title": t, "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(t.replace(" ", "_"))})
            texts.append(ex)
    text = "\n\n".join(texts)[:24000]
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if 40 <= len(s.strip()) <= 320]
    scored = sorted(((len(kw & _words(s)), i, s) for i, s in enumerate(sents)), key=lambda x: (-x[0], x[1]))
    top = [x for x in scored if x[0] > 0][:8]
    top.sort(key=lambda x: x[1])
    claims = [{"claim": s, "source": sources[0]["url"] if sources else ""} for _, _, s in top]
    mode = "grounded" if sources and claims else "no_sources"
    return {"mode": mode, "sources": sources, "verified_claims": claims, "source_text": text if mode == "grounded" else ""}


def _markers(text):
    """The specifics a reader would take as facts: numbers, magnitudes, names, quotes."""
    out = []
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?%?", text):
        out.append(("number", m.group(0).rstrip(".,")))
    for m in re.finditer(r"\b(%s)\b" % _MAGNITUDE, text, re.I):
        out.append(("number", m.group(1)))
    for m in re.finditer(r"[\"“]([^\"”]{4,120})[\"”]", text):
        out.append(("quote", m.group(1)))
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
        toks = list(re.finditer(r"[A-Za-z][A-Za-z\-']*", sent))
        caps = sum(1 for t in toks if t.group(0)[0].isupper())
        if len(toks) >= 3 and caps >= 0.6 * len(toks):
            continue                       # a Title Case headline: its capitals are style, not names
        i = 0
        while i < len(toks):
            w = toks[i].group(0)
            if w[0].isupper() and w.lower() not in _STOP and len(w) > 1:
                j = i
                phrase = [w]
                while j + 1 < len(toks) and toks[j + 1].group(0)[0].isupper() and toks[j + 1].group(0).lower() not in _STOP:
                    j += 1
                    phrase.append(toks[j].group(0))
                # a lone capitalised word that merely starts the sentence is just grammar
                if not (i == 0 and len(phrase) == 1):
                    out.append(("name", " ".join(phrase)))
                i = j + 1
            else:
                i += 1
    return out


def script_extra(script):
    """Names the script itself invents as FICTIONAL characters (story devices, not factual claims)."""
    out = []
    for c in (script.get("characters") if isinstance(script, dict) else None) or []:
        if isinstance(c, dict):
            out.append(str(c.get("name", "")))
    return " ".join(out)


def grounding_check(text, res, topic="", extra=""):
    """Return a list of unsupported specifics. Empty list = every name/number/quote is in the
    topic or the fetched source text (or, with no sources, there are none at all)."""
    allowed = _norm((topic or "") + " " + (extra or "") + " " + (res.get("source_text") or "") + " " +
                    " ".join(c.get("claim", "") for c in (res.get("verified_claims") or [])))
    allowed_words = set(allowed.split())
    bad = []
    for kind, m in _markers(text or ""):
        n = _norm(m)
        if not n:
            continue
        if kind == "name":
            ok = n in allowed or all(w in allowed_words for w in n.split())
        else:
            ok = n in allowed
        if not ok and m not in bad:
            bad.append(m)
    return ["%s" % b for b in bad]


def facts_prompt(res):
    """The instruction block every writer must obey."""
    if res.get("mode") == "grounded" and res.get("verified_claims"):
        facts = "\n".join("- " + c["claim"] for c in res["verified_claims"][:8])
        return ("VERIFIED FACTS (from real sources: %s). You may state ONLY facts from this list. Do NOT add any other "
                "name, number, date, place, statistic, study or quote. If you are unsure, leave it out.\n%s"
                % (", ".join(s["title"] for s in res.get("sources", [])), facts))
    return ("NO VERIFIED SOURCES were found for this topic, so this post must contain NO factual claims: no names, "
            "numbers, dates, years, places, statistics, studies or quotes. Write only a reflective, general, human "
            "post about the theme. Anything that sounds like a fact will be rejected.")


def post_text(script):
    """All the reader-facing text of a script, for checking."""
    if not isinstance(script, dict):
        return str(script or "")
    parts = [script.get("title"), script.get("hook"), script.get("narration"), script.get("on_screen_text")]
    d = script.get("dialogs")
    if isinstance(d, list):
        for x in d:
            parts.append(x.get("dialog") if isinstance(x, dict) else str(x))
    elif isinstance(d, str):
        parts.append(d)
    return chr(10).join(str(p) for p in parts if p)      # one field per line: a headline stays a headline


def person_names(text):
    """Capitalised multi-word names in a topic (e.g. 'Manuela P Mulondo'). A Title Case headline is ignored."""
    return [m for k, m in _markers(text or "") if k == "name" and " " in m]


_FIRST_PERSON = re.compile(r"(\bI\b|\bI'm\b|\bI've\b|\bI'd\b|\bI'll\b|\b[Mm]y\b|\b[Mm]e\b|\b[Mm]yself\b|\b[Mm]ine\b)")


_CLIP_TITLE = re.compile(
    r"\bbest of\b|\bcompilation\b|\bhighlights?\b|\bfull (episode|movie|match|game|show)\b|\btrailer\b|\breact(s|ion|ing)\b|"
    r"\bofficial (video|trailer|audio|music)\b|#\w+|\bsubscribe\b|\bepisode \d+\b|\bep\.? ?\d+\b|\bunboxing\b|"
    r"\bcrash.?outs?\b|\bviral (clips?|moments?|videos?)\b|\blive:|\bwatch (live|now)\b|\bshorts?\b$", re.I)


def is_subject(title):
    """A topic must be a SUBJECT a post can be about. A Reddit personal story ('Something is interacting with
    my 17 year old daughter ...', 'I hate myself for ...') is not - there is nothing to ground a post on."""
    t = (title or "").strip()
    if _FIRST_PERSON.search(t):
        return False, "a personal first-person story, not a subject"
    if len(t.split()) < 3:
        return False, "too short to be a subject"
    if _CLIP_TITLE.search(t) or " | " in t or re.search(r"\s[l|]\s", t) or re.search(r"\b(trending now|subs)\b", t, re.I):
        return False, "a video / clip / channel title (compilation, highlights, trailer, reaction...), not a subject"
    return True, "ok"


def person_in_topic(topic):
    """Does the topic name a specific REAL individual? Asked of the local model (Title Case headlines defeat a
    capital-letter heuristic: 'How Manuela P Mulondo Changed Her Money Story'). Returns (True/False, name)."""
    try:
        nl = chr(10)
        prompt = ("Topic: " + topic + nl + "Does this topic name or clearly refer to a specific REAL individual person "
                  "(living or historical) - not a fictional character, an animal, a species or a generic role? "
                  'Reply only JSON: {"real_person": true or false, "name": "the name or empty"}')
        body = json.dumps({"model": "qwen2.5:14b", "stream": False, "format": "json",
                           "options": {"temperature": 0, "num_predict": 60}, "prompt": prompt}).encode()
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            out = json.loads(json.loads(r.read().decode())["response"])
        return bool(out.get("real_person")), str(out.get("name") or "")[:60]
    except Exception:
        names = person_names(topic)
        return bool(names), ", ".join(names)[:60]
