# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""Tests the extra publishers (Threads, LinkedIn, WordPress, Webhook) against a fake local server.
Nothing leaves your computer.   venv\\Scripts\\python.exe tools\\test_extra_publishers.py
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import extra_publishers as X

seen = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _go(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        seen.append((self.command, self.path, dict(self.headers), body))
        p = self.path.split("?")[0]
        code, out, hdr = 200, {}, {}
        if p.endswith("/threads"):
            out = {"id": "C1"}
        elif p.endswith("/threads_publish"):
            out = {"id": "P1"}
        elif p == "/P1":
            out = {"permalink": "https://threads.example/p/P1"}
        elif p == "/v2/userinfo":
            out = {"sub": "abc123"}
        elif p == "/v2/ugcPosts":
            hdr = {"x-restli-id": "urn:li:share:777"}
            code = 201
        elif p.endswith("/wp-json/wp/v2/media"):
            out = {"id": 55}
        elif p.endswith("/wp-json/wp/v2/posts"):
            out = {"id": 9, "link": "https://site.example/hello"}
        self.send_response(code)
        for k, v in hdr.items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(out).encode())

    do_GET = do_POST = _go


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % srv.server_address[1]
ok = fail = 0


def check(name, cond):
    global ok, fail
    print(("  PASS  " if cond else "  FAIL  ") + name)
    ok, fail = (ok + 1, fail) if cond else (ok, fail + 1)


r = X.send("webhook", {"url": base + "/hook", "secret": "s3"}, "Hello world", None, "text", "Title")
check("webhook returns a proof", r.startswith("webhook:HTTP 200"))
m, path, hdr, body = seen[-1]
check("webhook sends the text, the title and the author", json.loads(body)["text"] == "Hello world" and json.loads(body)["created_by"] == "Mr Blank")
check("webhook sends the secret header", hdr.get("X-Webhook-Secret") == "s3")

r = X.send("threads", {"user_id": "U1", "token": "T", "api_base": base}, "x" * 700, None, "text")
check("threads returns the post link", r == "https://threads.example/p/P1")
check("threads cuts the text to 500 characters", any(b"text=" in s[3] and len(s[3]) < 700 for s in seen))

r = X.send("linkedin", {"token": "T", "api_base": base}, "Hello LinkedIn")
check("linkedin returns the post link", r == "https://www.linkedin.com/feed/update/urn:li:share:777")
check("linkedin post is public and uses the person from the token",
      any(b"urn:li:person:abc123" in s[3] and b"PUBLIC" in s[3] for s in seen))

img = Path(__file__).resolve().parent / "_t.png"
img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 50)
try:
    r = X.send("wordpress", {"url": base, "user": "u", "app_password": "p"}, "Para one\n\nPara two", str(img), "image", "My title")
finally:
    img.unlink()
check("wordpress returns the post link", r == "https://site.example/hello")
post = [s for s in seen if s[1].endswith("/wp-json/wp/v2/posts")][-1]
data = json.loads(post[3])
check("wordpress uses the picture as the featured image", data.get("featured_media") == 55)
check("wordpress turns paragraphs into HTML", "<p>Para one</p>" in data["content"] and data["title"] == "My title")
check("wordpress uses a login", post[2].get("Authorization", "").startswith("Basic "))

try:
    X.send("webhook", {"url": "http://127.0.0.1:1/none"}, "x")
    check("a dead address raises a clear error", False)
except RuntimeError as e:
    check("a dead address raises a clear error", "could not reach" in str(e))

print("\n%d passed, %d failed" % (ok, fail))
sys.exit(1 if fail else 0)
