# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
r"""SESSION LOGIN for the dashboard.

REPLACES HTTP Basic Auth. Basic auth showed a browser dialog asking for BOTH fields,
there was no way to log out, and it could not pre-fill the username. This gives:
  * /login      a real page: username already filled in and read-only, so only the
                password has to be typed
  * /logout     clears the cookie and returns to /login
  * a signed, httpOnly cookie, so the token is not readable from JavaScript

The token is HMAC-SHA256 over (username + expiry) using a secret stored in
data/dashboard_session.key, generated on first use. Nothing about the password is stored
in the cookie or sent back to the browser.
"""
import base64, hashlib, hmac, json, os, secrets, time
from pathlib import Path

ROOT = (Path(__file__).resolve().parents[1])
KEYFILE = ROOT / "data" / "dashboard_session.key"
COOKIE = "ap_session"
TTL_SEC = 12 * 3600          # a login lasts 12 hours, then it asks again


def secret():
    try:
        KEYFILE.parent.mkdir(parents=True, exist_ok=True)
        if not KEYFILE.exists():
            KEYFILE.write_bytes(secrets.token_bytes(32))
            try:
                os.chmod(str(KEYFILE), 0o600)
            except Exception:
                pass
        return KEYFILE.read_bytes()
    except Exception:
        # never let a missing key file lock the owner out of his own dashboard
        return b"autopost-fallback-session-key"


def make(user):
    exp = int(time.time()) + TTL_SEC
    payload = "%s|%d" % (user, exp)
    sig = hmac.new(secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(("%s.%s" % (payload, sig)).encode("utf-8")).decode()


def read(token):
    """Return the username if the token is valid and not expired, else None."""
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode("utf-8")
        payload, _, sig = raw.rpartition(".")
        user, _, exp = payload.partition("|")
        want = hmac.new(secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, want):
            return None
        if int(exp) < int(time.time()):
            return None
        return user
    except Exception:
        return None


def check(user, password):
    """Constant-time credential check against rules.json."""
    from dashboard import load_rules
    auth = (load_rules().get("dashboard_auth") or {})
    want_u = auth.get("username") or ""
    want_p = auth.get("password") or ""
    if not want_p:
        return False
    return hmac.compare_digest((user or "").strip(), want_u) and \
           hmac.compare_digest(password or "", want_p)


def enabled():
    from dashboard import load_rules
    return bool((load_rules().get("dashboard_auth") or {}).get("enabled"))


def cookie_header(token):
    return ("Set-Cookie: %s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d"
            % (COOKIE, token, TTL_SEC))


def clear_header():
    return ("Set-Cookie: %s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % COOKIE)


def parse_cookies(header_val):
    out = {}
    for part in (header_val or "").split(";"):
        if "=" in part:
            k, _, v = part.partition("=")
            out[k.strip()] = v.strip()
    return out


def token_from_request(headers):
    return parse_cookies(headers.get("Cookie", "")).get(COOKIE)


# ------------------------------------------------------------------ the page
LOGIN_PAGE = """<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in · AutoPost</title><link rel="stylesheet" href="/assets/ui/app.css">
<style>body{display:flex;align-items:center;justify-content:center;min-height:100vh}
 .box{width:100%%;max-width:380px}
 .card{margin:0}
 .cb{padding:26px 24px}
 h1{font-size:20px;font-weight:650;letter-spacing:-.02em;margin-bottom:3px}
 .sub{color:var(--dim2);font-size:13px;margin-bottom:20px}
 input[type=text],input[type=password]{width:100%%}
 .field{margin-bottom:15px}
 .btn.primary{width:100%%;padding:10px}
 .err{background:rgba(248,81,73,.10);border:1px solid rgba(248,81,73,.4);color:#ffb4ae;
      border-radius:8px;padding:10px 13px;font-size:13px;margin-bottom:16px}
 .hint{color:var(--dim2);font-size:12px;margin-top:16px;line-height:1.5}
</style></head><body>
<div class="box"><div class="card">
  <div class="ch"><h1>AutoPost</h1><span class="sub">control panel</span></div>
  <div class="cb">
    %s
    <input type="hidden" id="u" value="%s" autocomplete="username">
    <div class="field">
      <label class="fl">Password</label>
      <input type="password" id="p" autocomplete="current-password" autofocus>
    </div>
    <button class="btn primary" id="go">Sign in</button>
    <div class="hint">Only the password needs typing. The dashboard can change your posting
      rules and send posts, so it asks for the password once every 12 hours.</div>
  </div>
</div></div>
<script>
const p = document.getElementById('p');
p.focus();
async function go(){
  const r = await fetch('/login', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({user: document.getElementById('u').value, pass: p.value})});
  if (r.ok){ const nx = new URLSearchParams(location.search).get('next');
    location.href = (nx && nx[0] === '/' && nx[1] !== '/') ? nx : '/'; }
  else p.value = '', p.focus();
}
document.getElementById('go').onclick = go;
p.onkeydown = e => { if (e.key === 'Enter') go(); };
</script></body></html>"""


def login_page(error=None):
    from dashboard import load_rules
    user = (load_rules().get("dashboard_auth") or {}).get("username") or "pavel"
    err = ('<div class="err">Wrong password. Try again.</div>' if error else "")
    return LOGIN_PAGE % (err, user)