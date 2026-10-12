# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""More places to post: Threads, LinkedIn, WordPress and a Webhook that reaches almost anything else.

Each sender takes the settings block saved by connect.py and returns the link (or a short proof text) of
the new post. They raise an error with a clear message when something is wrong. No network calls happen
unless a post is really being published.

    threads    text posts on Threads (Meta). Pictures and video need public web addresses, so they go as text.
    linkedin   text posts on your LinkedIn profile.
    wordpress  full posts on your own WordPress site, with the picture as the featured image.
    webhook    sends the post to any web address you give (Zapier, Make, n8n, IFTTT ...).
               From there you can reach Instagram, Pinterest, Reddit, Tumblr, a spreadsheet, an email and more.
"""
import base64
import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

UA = "AutoPostAutomation/3.0 (created by Mr Blank)"
PLATFORMS = ("threads", "linkedin", "wordpress", "webhook")


def _req(url, data=None, headers=None, method=None, timeout=60):
    h = {"User-Agent": UA}
    h.update(headers or {})
    r = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, dict(resp.headers), body
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise RuntimeError("HTTP %s from %s: %s" % (e.code, urllib.parse.urlparse(url).netloc, detail))
    except Exception as e:
        raise RuntimeError("could not reach %s: %s" % (urllib.parse.urlparse(url).netloc, str(e)[:100]))


def _json(b):
    try:
        return json.loads(b.decode("utf-8"))
    except Exception:
        return {}


# ------------------------------------------------------------------ Threads
def send_threads(c, text, media_path=None, kind="text", title=""):
    base = (c.get("api_base") or "https://graph.threads.net/v1.0").rstrip("/")
    uid, tok = c["user_id"], c["token"]
    form = urllib.parse.urlencode({"media_type": "TEXT", "text": text[:500], "access_token": tok}).encode()
    _, _, b = _req("%s/%s/threads" % (base, uid), form, {"Content-Type": "application/x-www-form-urlencoded"}, "POST")
    cid = _json(b).get("id")
    if not cid:
        raise RuntimeError("Threads did not return a container id")
    form = urllib.parse.urlencode({"creation_id": cid, "access_token": tok}).encode()
    _, _, b = _req("%s/%s/threads_publish" % (base, uid), form, {"Content-Type": "application/x-www-form-urlencoded"}, "POST")
    pid = _json(b).get("id")
    if not pid:
        raise RuntimeError("Threads did not publish the post")
    try:
        _, _, b = _req("%s/%s?fields=permalink&access_token=%s" % (base, pid, urllib.parse.quote(tok)))
        return _json(b).get("permalink") or "threads:" + str(pid)
    except Exception:
        return "threads:" + str(pid)


# ------------------------------------------------------------------ LinkedIn
def send_linkedin(c, text, media_path=None, kind="text", title=""):
    base = (c.get("api_base") or "https://api.linkedin.com").rstrip("/")
    tok = c["token"]
    auth = {"Authorization": "Bearer " + tok}
    author = c.get("author")
    if not author:
        _, _, b = _req(base + "/v2/userinfo", headers=auth)
        sub = _json(b).get("sub")
        if not sub:
            raise RuntimeError("LinkedIn did not tell who this token belongs to")
        author = "urn:li:person:" + sub
    body = {"author": author, "lifecycleState": "PUBLISHED",
            "specificContent": {"com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text[:2900]}, "shareMediaCategory": "NONE"}},
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"}}
    h = dict(auth)
    h.update({"Content-Type": "application/json", "X-Restli-Protocol-Version": "2.0.0"})
    _, hdr, b = _req(base + "/v2/ugcPosts", json.dumps(body).encode(), h, "POST")
    urn = {k.lower(): v for k, v in hdr.items()}.get("x-restli-id") or _json(b).get("id") or ""
    return ("https://www.linkedin.com/feed/update/" + urn) if urn else "linkedin:accepted"


# ------------------------------------------------------------------ WordPress
def _multipart(fields, file_field=None, file_path=None):
    bd = uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (bd, k, v)).encode())
    if file_field and file_path:
        p = Path(file_path)
        ct = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        out.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\nContent-Type: %s\r\n\r\n"
                    % (bd, file_field, p.name, ct)).encode())
        out.append(p.read_bytes())
        out.append(b"\r\n")
    out.append(("--%s--\r\n" % bd).encode())
    return b"".join(out), "multipart/form-data; boundary=" + bd


def send_wordpress(c, text, media_path=None, kind="text", title=""):
    site = c["url"].rstrip("/")
    auth = {"Authorization": "Basic " + base64.b64encode(("%s:%s" % (c["user"], c["app_password"])).encode()).decode()}
    media_id = None
    if media_path and kind == "image" and Path(media_path).exists():
        p = Path(media_path)
        h = dict(auth)
        h["Content-Disposition"] = 'attachment; filename="%s"' % p.name
        h["Content-Type"] = mimetypes.guess_type(p.name)[0] or "image/jpeg"
        try:
            _, _, b = _req(site + "/wp-json/wp/v2/media", p.read_bytes(), h, "POST", timeout=120)
            media_id = _json(b).get("id")
        except Exception:
            media_id = None
    html = "".join("<p>%s</p>" % para.strip().replace("\n", "<br>") for para in text.split("\n\n") if para.strip())
    body = {"title": (title or text.split("\n")[0])[:120], "content": html, "status": c.get("status") or "publish"}
    if media_id:
        body["featured_media"] = media_id
    h = dict(auth)
    h["Content-Type"] = "application/json"
    _, _, b = _req(site + "/wp-json/wp/v2/posts", json.dumps(body).encode(), h, "POST")
    d = _json(b)
    return d.get("link") or ("wordpress:post " + str(d.get("id", "")))


# ------------------------------------------------------------------ Webhook
def send_webhook(c, text, media_path=None, kind="text", title=""):
    url = c["url"]
    payload = {"app": "AutoPost Automation", "created_by": "Mr Blank", "kind": kind, "title": title, "text": text}
    headers = {}
    if c.get("secret"):
        headers["X-Webhook-Secret"] = c["secret"]
    if c.get("send_file") and media_path and Path(media_path).exists() and Path(media_path).stat().st_size < 25_000_000:
        data, ct = _multipart({"payload": json.dumps(payload)}, "file", media_path)
        headers["Content-Type"] = ct
    else:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    st, _, _ = _req(url, data, headers, "POST")
    return "webhook:HTTP %d accepted" % st


SENDERS = {"threads": send_threads, "linkedin": send_linkedin, "wordpress": send_wordpress, "webhook": send_webhook}


def send(platform, conn_block, text, media_path=None, kind="text", title=""):
    return SENDERS[platform](conn_block, text, media_path, kind, title)
