# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""Author credit for AutoPost Automation.

The project is free to use, change and share. The author credit is not optional:
it must stay in the files, in the dashboard and in the logs (see LICENSE).

What this module does, in plain words:
  * gives every dashboard page a footer with the author credit (added by the server,
    so removing it from an HTML file changes nothing);
  * stamps a one line credit at the top of every source file, and puts it back if
    someone removes it;
  * keeps AUTHORS.md, NOTICE.md and the LICENSE credit line, and restores them if they
    are changed or deleted;
  * writes the credit into the start-up log.

Nothing here can stop the program or change how it posts. Every function swallows
its own errors. A determined person can still edit the source code, but doing so
breaks the license, and the restore step makes any removal easy to see.
"""
import base64
import hashlib
import json
import os
import time
from pathlib import Path

# The credit is stored twice: plain (easy to read) and encoded with a fingerprint.
# If someone edits the plain values, the encoded copy is used instead.
_PLAIN = {"name": "Mr Blank", "project": "AutoPost Automation",
          "tagline": "Social Media Automation (Local Machine)", "version": "3.0",
          "home": "github.com/pavelblank/autopost-automation"}
_ENC = (
        "eyJuYW1lIjogIk1yIEJsYW5rIiwgInByb2plY3QiOiAiQXV0b1Bvc3QgQXV0b21hdGlvbiIsICJ0"
        "YWdsaW5lIjogIlNvY2lhbCBNZWRpYSBBdXRvbWF0aW9uIChMb2NhbCBNYWNoaW5lKSIsICJ2ZXJz"
        "aW9uIjogIjMuMCIsICJob21lIjogImdpdGh1Yi5jb20vcGF2ZWxibGFuay9hdXRvcG9zdC1hdXRv"
        "bWF0aW9uIn0=")
PUBKEY = "tLdUvSnK74tPYFSPQkmt49tdvTiccTGBkP9V7CaRPOo="      # only the owner's private key can sign a valid credit
STATE = {"tampered": False, "why": ""}
_FP = "6663246f68b49693e65f65d2d945df2d321283f0ada5d41202df42aa8fe726ef"


def _credit():
    try:
        enc = json.loads(base64.b64decode(_ENC).decode("utf-8"))
    except Exception:
        enc = dict(_PLAIN)
    return enc          # the encoded copy always wins


C = _credit()
NAME, PROJECT, VERSION, HOME = C["name"], C["project"], C["version"], C["home"]
TAGLINE = C["tagline"]
LINE = "%s %s - created by %s (%s). Keep this notice." % (PROJECT, VERSION, NAME, HOME)
FOOTER_TEXT = "%s %s &middot; %s &middot; created by <b>%s</b> &middot; %s" % (PROJECT, VERSION, TAGLINE, NAME, HOME)
MARK = 'id="ap-author"'


def footer_html():
    if STATE.get("tampered"):
        return ('<div %s data-author="%s" style="max-width:1180px;margin:22px auto 14px;padding:12px 24px;text-align:center;'
                'font:13px system-ui,sans-serif;color:#ffb4ae;border:1px solid #f85149;border-radius:8px">'
                'Unofficial modified copy. The original %s %s is by <b>%s</b> &middot; %s</div>'
                % (MARK, NAME, PROJECT, VERSION, NAME, HOME))
    return ('<div %s data-author="%s" style="max-width:1180px;margin:22px auto 14px;padding:10px 24px;'
            'text-align:center;font:12px system-ui,sans-serif;color:#8b98a9">%s</div>' % (MARK, NAME, FOOTER_TEXT))


def inject(html):
    """Add the footer to an HTML page that does not already have it."""
    try:
        if MARK in html:
            return html
        i = html.lower().rfind("</body>")
        return html[:i] + footer_html() + html[i:] if i >= 0 else html + footer_html()
    except Exception:
        return html


def banner():
    return "=== %s %s | %s | created by %s | %s ===" % (PROJECT, VERSION, TAGLINE, NAME, HOME)


# ---------------------------------------------------------------- files
_AUTHORS = """# Authors

**Creator and author:** %(n)s
**Project:** %(p)s %(v)s - %(t)s
**Home:** https://%(h)s

This credit is part of the license. You may use, change and share this project,
but you must keep the author name in the source files, this file, NOTICE.md, the
LICENSE and the dashboard footer.
"""
_NOTICE = """%(p)s %(v)s
Created by %(n)s - https://%(h)s

Free to use, change and share. The author credit must stay (see LICENSE).

Third party parts keep their own licenses:
- Pixel office sprites: MIT, from the pixel-agents project.
- Python packages listed in requirements.txt keep their own licenses.
"""


def _fill(t):
    return t % {"n": NAME, "p": PROJECT, "v": VERSION, "t": TAGLINE, "h": HOME}


def _ensure_file(path, text, must):
    try:
        cur = path.read_text(encoding="utf-8") if path.exists() else ""
        if must not in cur:
            path.write_text(text, encoding="utf-8")
            return True
    except Exception:
        pass
    return False


_EXT = {".py": "#", ".ps1": "#", ".sh": "#", ".bat": "REM", ".cmd": "REM", ".html": "html", ".css": "css", ".js": "css"}
_SKIP = {"venv", "engine", "backups", "data", "out", "logs", "__pycache__", ".git", "node_modules",
         "STOCK", "WORK", "final", "Download", "_archive_DANGER_purge_scripts"}


def _stamp_one(p):
    try:
        ext = p.suffix.lower()
        kind = _EXT.get(ext)
        if not kind or p.stat().st_size > 3_000_000:
            return False
        if kind == "REM" and os.environ.get("AP_STAMP_BAT") != "1":
            return False                 # never edit a batch file that may be running
        raw = p.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        txt = raw.decode("utf-8-sig")
        if NAME in txt[:600] and "created by" in txt[:600]:
            return False
        nl = "\r\n" if "\r\n" in txt[:2000] else "\n"
        lines = txt.split(nl)
        if kind == "#":
            at = 0
            if lines and lines[0].startswith("#!"):
                at = 1
            if len(lines) > at and "coding" in lines[at][:40] and lines[at].startswith("#"):
                at += 1
            lines.insert(at, "# " + LINE)
        elif kind == "REM":
            at = 1 if lines and lines[0].strip().lower().startswith("@echo off") else 0
            lines.insert(at, "REM " + LINE)
        elif kind == "css":
            lines.insert(0, "/* " + LINE + " */")
        else:
            j = next((k for k, l in enumerate(lines) if "<head" in l.lower()), None)
            if j is None:
                return False
            lines[j] = lines[j] + "<!-- " + LINE + " -->"
        out = nl.join(lines).encode("utf-8")
        if bom:
            out = b"\xef\xbb\xbf" + out
        p.write_bytes(out)
        return True
    except Exception:
        return False


def restamp(root):
    """Put the one line credit back at the top of every source file that lost it."""
    n = 0
    try:
        root = Path(root)
        for p in root.rglob("*"):
            if any(part in _SKIP for part in p.relative_to(root).parts) or not p.is_file():
                continue
            if _stamp_one(p):
                n += 1
    except Exception:
        pass
    return n



# ---------------------------------------------------------------- three copies that restore each other
# The owner signs this file with a private key (Ed25519); only that key makes a valid signature.
# Copy 1: this file. Copy 2: scripts/_ap_core.py (holds an encoded copy of this file).
# Copy 3: the database table ap_meta. If one copy is changed or deleted, the other two win
# and the damaged one is rebuilt. To change this file on purpose, run:  python scripts/authorship.py --seal
import re
import sqlite3
import zlib

_CORE_T = ('''# {line}
"""Part of the author credit system. Do not edit: it is rebuilt from authorship.py and checked against the owner's signature."""
import base64, zlib
from pathlib import Path
BLOB = "{blob}"
SIG = "{sig}"
def restore():
    try:
        me = Path(__file__).resolve().parent / "authorship.py"
        me.write_bytes(zlib.decompress(base64.b64decode(BLOB)))
        return True
    except Exception:
        return False
FOOTER = "{foot}"
''')


def _sha(b):
    return hashlib.sha256(b).hexdigest() if b else None


def _enc_bytes(b):
    return base64.b64encode(zlib.compress(b, 9)).decode("ascii")


def _dec(blob):
    try:
        return zlib.decompress(base64.b64decode(blob))
    except Exception:
        return None


def _read(p):
    try:
        return Path(p).read_bytes()
    except Exception:
        return None


def _manifest(core_sha):
    return {"author": NAME, "project": PROJECT, "home": HOME, "core_sha": core_sha}


def _canon(m):
    return json.dumps(m, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _verify(sig_json):
    """-> manifest dict if the owner's signature is valid, else None."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        d = json.loads(sig_json)
        Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBKEY)).verify(base64.b64decode(d["sig"]), _canon(d["manifest"]))
        return d["manifest"]
    except ImportError:
        return {"_unchecked": True}              # no crypto library installed: cannot judge, do not accuse
    except Exception:
        return None


def _db_conn(root):
    db = Path(root) / "data" / "brain.db"
    if not db.exists():
        return None
    c = sqlite3.connect(str(db), timeout=3)
    c.execute("CREATE TABLE IF NOT EXISTS ap_meta(k TEXT PRIMARY KEY, v TEXT)")
    return c


def _db_get(root, key):
    try:
        c = _db_conn(root)
        if not c:
            return None
        try:
            r = c.execute("SELECT v FROM ap_meta WHERE k=?", (key,)).fetchone()
            return r[0] if r else None
        finally:
            c.close()
    except Exception:
        return None


def _db_put(root, key, val):
    try:
        c = _db_conn(root)
        if not c:
            return
        try:
            c.execute("INSERT OR REPLACE INTO ap_meta(k,v) VALUES(?,?)", (key, val))
            c.commit()
        finally:
            c.close()
    except Exception:
        pass


def _core_text(b, sig_json):
    return _CORE_T.format(line=LINE, blob=_enc_bytes(b), sig=base64.b64encode((sig_json or "").encode()).decode(),
                          foot=FOOTER_TEXT.replace("&middot;", "|").replace("<b>", "").replace("</b>", ""))


def seal(root, key_path):
    """Owner only: sign the current authorship.py with the private key and rebuild every copy."""
    from cryptography.hazmat.primitives import serialization
    root = Path(root)
    me = _read(root / "scripts" / "authorship.py")
    if not me:
        return False
    key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    m = _manifest(_sha(me))
    sig = json.dumps({"manifest": m, "sig": base64.b64encode(key.sign(_canon(m))).decode()}, indent=1)
    if _verify(sig) is None:
        raise SystemExit("this key does not match the public key inside authorship.py")
    (root / "AUTHORSHIP.sig").write_bytes(sig.encode("utf-8"))
    (root / "scripts" / "_ap_core.py").write_bytes(_core_text(me, sig).encode("utf-8"))
    _db_put(root, "author_core", _enc_bytes(me))
    _db_put(root, "author_sig", sig)
    return True


def _vote(root):
    """Check the owner's signature and rebuild any copy that was changed or deleted."""
    fixed = []
    try:
        root = Path(root)
        pa, pc, ps = root / "scripts" / "authorship.py", root / "scripts" / "_ap_core.py", root / "AUTHORSHIP.sig"
        a = _read(pa)
        ctext = _read(pc) or b""
        m = re.search(rb'BLOB = "([^"]+)"', ctext)
        c = _dec(m.group(1).decode()) if m else None
        ms = re.search(rb'SIG = "([^"]*)"', ctext)
        sig_c = base64.b64decode(ms.group(1)).decode() if ms and ms.group(1) else None
        d = _dec(_db_get(root, "author_core") or "")
        sigs = [x for x in ((_read(ps) or b"").decode("utf-8", "ignore") or None, sig_c, _db_get(root, "author_sig")) if x]
        good_sig, manifest = None, None
        for sg in sigs:
            mf = _verify(sg)
            if mf:
                good_sig, manifest = sg, mf
                break
        if manifest and manifest.get("_unchecked"):
            return fixed
        if not manifest:
            STATE.update(tampered=True, why="no valid owner signature")
            return fixed
        truth = next((x for x in (a, c, d) if x and _sha(x) == manifest.get("core_sha")), None)
        if truth is None:
            STATE.update(tampered=True, why="authorship code does not match the owner's signature")
            return fixed
        STATE.update(tampered=False, why="")
        if _sha(a) != _sha(truth):
            pa.write_bytes(truth)
            fixed.append("authorship.py")
        want = _core_text(truth, good_sig).encode("utf-8")
        if ctext != want:
            pc.write_bytes(want)
            fixed.append("_ap_core.py")
        if (_read(ps) or b"").decode("utf-8", "ignore") != good_sig:
            ps.write_bytes(good_sig.encode("utf-8"))
            fixed.append("AUTHORSHIP.sig")
        if d is None or _sha(d) != _sha(truth):
            _db_put(root, "author_core", _enc_bytes(truth))
        if _db_get(root, "author_sig") != good_sig:
            _db_put(root, "author_sig", good_sig)
    except Exception:
        pass
    return fixed


def guard(root, log=None):
    """Check and restore the credit files. Safe: never raises, never blocks."""
    out = {"restored": [], "stamped": 0}
    try:
        root = Path(root)
        out["restored"] += _vote(root)
        if _ensure_file(root / "AUTHORS.md", _fill(_AUTHORS), NAME):
            out["restored"].append("AUTHORS.md")
        if _ensure_file(root / "NOTICE.md", _fill(_NOTICE), NAME):
            out["restored"].append("NOTICE.md")
        lic = root / "LICENSE"
        try:
            ltxt = lic.read_text(encoding="utf-8") if lic.exists() else ""
        except Exception:
            ltxt = ""
        if NAME not in ltxt:
            note = "Copyright (c) %s %s\n\n" % (time.strftime("%Y"), NAME)
            try:
                lic.write_text(note + ltxt, encoding="utf-8")
                out["restored"].append("LICENSE")
            except Exception:
                pass
        out["stamped"] = restamp(root)
        try:
            (root / "data").mkdir(exist_ok=True)
            (root / "data" / "authorship.json").write_text(json.dumps(
                {"author": NAME, "project": PROJECT, "version": VERSION, "home": HOME,
                 "fingerprint": hashlib.sha256(("%s|%s|%s" % (NAME, PROJECT, HOME)).encode()).hexdigest(),
                 "checked": time.strftime("%Y-%m-%d %H:%M:%S"), "restored": out["restored"]}, indent=2),
                encoding="utf-8")
        except Exception:
            pass
        if log and (out["restored"] or out["stamped"]):
            log("author credit restored: %s, %d file header(s) re-stamped" % (",".join(out["restored"]) or "none", out["stamped"]))
    except Exception:
        pass
    return out


if __name__ == "__main__":
    import sys
    if "--seal" in sys.argv:
        kp = sys.argv[sys.argv.index("--key") + 1] if "--key" in sys.argv else None
        if not kp:
            raise SystemExit("owner only: python scripts/authorship.py --seal --key path/to/private_key.pem")
        print("sealed" if seal(Path(__file__).resolve().parents[1], kp) else "nothing to seal")
        raise SystemExit(0)
    r = guard(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1], log=print)
    print(banner())
    print(json.dumps(r))
