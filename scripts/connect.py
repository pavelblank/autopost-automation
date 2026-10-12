# AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
"""Connect your social accounts with simple questions. Nothing is posted by this tool.

Run from the project folder:

    venv\\Scripts\\python.exe scripts\\connect.py status
    venv\\Scripts\\python.exe scripts\\connect.py telegram
    venv\\Scripts\\python.exe scripts\\connect.py telegram-channel
    venv\\Scripts\\python.exe scripts\\connect.py bluesky
    venv\\Scripts\\python.exe scripts\\connect.py discord
    venv\\Scripts\\python.exe scripts\\connect.py mastodon
    venv\\Scripts\\python.exe scripts\\connect.py x
    venv\\Scripts\\python.exe scripts\\connect.py youtube
    venv\\Scripts\\python.exe scripts\\connect.py tiktok
    venv\\Scripts\\python.exe scripts\\connect.py blog
    venv\\Scripts\\python.exe scripts\\connect.py facebook | threads | linkedin | wordpress | webhook
    venv\\Scripts\\python.exe scripts\\connect.py on bluesky telegram     (switch posting on for these)
    venv\\Scripts\\python.exe scripts\\connect.py off bluesky              (switch posting off)

Secrets you type are saved only in config\\connections.json on this computer.
"""
import getpass
import json
import shutil
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
CONN = ROOT / "config" / "connections.json"
RULES = ROOT / "config" / "rules.json"
NAMES = ["telegram", "telegram_channel", "bluesky", "discord", "mastodon", "facebook", "threads", "linkedin",
         "wordpress", "webhook", "youtube", "tiktok", "x", "socialai"]
NICE = {"telegram": "Telegram (private chat)", "telegram_channel": "Telegram channel", "bluesky": "Bluesky",
        "discord": "Discord", "mastodon": "Mastodon", "x": "X", "youtube": "YouTube", "tiktok": "TikTok",
        "socialai": "Your own blog", "facebook": "Facebook Page", "threads": "Threads", "linkedin": "LinkedIn",
        "wordpress": "WordPress site", "webhook": "Webhook (Zapier, Make, n8n)"}


def load():
    if not CONN.exists():
        ex = ROOT / "config" / "connections.example.json"
        if ex.exists():
            shutil.copy2(ex, CONN)
    return json.loads(CONN.read_text(encoding="utf-8-sig")) if CONN.exists() else {}


def save(c):
    CONN.write_text(json.dumps(c, indent=2), encoding="utf-8")


def rules():
    return json.loads(RULES.read_text(encoding="utf-8-sig"))


def save_rules(r):
    RULES.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")


def ask(q, secret=False, default=""):
    try:
        v = (getpass.getpass(q + ": ") if secret else input(q + (" [%s]" % default if default else "") + ": ")).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nStopped.")
        sys.exit(1)
    return v or default


def yes(q, default=True):
    v = ask(q + (" (Y/n)" if default else " (y/N)")).lower()
    return default if not v else v.startswith("y")


def switch(names, on=True):
    r = rules()
    d = r.setdefault("department", {})
    cur = list(d.get("allowed_platforms") or [])
    for n in names:
        if on and n not in cur:
            cur.append(n)
        if not on and n in cur:
            cur.remove(n)
    d["allowed_platforms"] = cur
    save_rules(r)
    print("   Platforms switched on for posting: " + (", ".join(cur) or "none"))
    print("   (Nothing is posted until you turn automation on in the dashboard: Settings, Live status.)")


def offer_switch(name):
    if yes("Switch %s on for posting now?" % NICE.get(name, name)):
        switch([name], True)


def status():
    c = load()
    allowed = (rules().get("department") or {}).get("allowed_platforms") or []
    print("")
    print("%-22s %-12s %s" % ("Platform", "Connected", "Switched on for posting"))
    for n in NAMES:
        v = c.get(n) or {}
        print("%-22s %-12s %s" % (NICE[n], "yes" if v.get("connected") else "no", "yes" if n in allowed else "no"))
    print("")
    print("Connect one with:  venv\\Scripts\\python.exe scripts\\connect.py <name>")


# --------------------------------------------------------------------------- platforms
def tg_api(token, method, **params):
    url = "https://api.telegram.org/bot%s/%s" % (token, method)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return json.loads(urllib.request.urlopen(url, timeout=20).read())


def do_telegram():
    print("""
TELEGRAM (private chat)
 1. Open Telegram and talk to @BotFather.
 2. Send  /newbot  and follow the questions. BotFather gives you a token.
 3. Open your new bot, press START and send it any message (for example: hello).
""")
    tok = ask("Paste the bot token", secret=True)
    try:
        me = tg_api(tok, "getMe")["result"]
    except Exception:
        print("That token was not accepted. Check it and try again.")
        return
    print("Bot found: @" + me.get("username", "?"))
    chat = None
    for _ in range(3):
        try:
            ups = tg_api(tok, "getUpdates").get("result", [])
        except Exception:
            ups = []
        for u in reversed(ups):
            m = u.get("message") or u.get("channel_post") or {}
            if m.get("chat"):
                chat = m["chat"]
                break
        if chat:
            break
        input("I could not see a message yet. Send your bot a message in Telegram, then press Enter...")
    if not chat:
        print("No message found. Run this again after you message the bot.")
        return
    c = load()
    title = chat.get("title") or chat.get("first_name") or str(chat["id"])
    c["telegram"] = {"connected": True, "token": tok, "chat_id": chat["id"], "account": "%s (%s)" % (title, chat.get("type", "chat"))}
    save(c)
    try:
        tg_api(tok, "sendMessage", chat_id=chat["id"], text="AutoPost Automation is connected.")
        print("Connected. A test message was sent to " + title + ".")
    except Exception:
        print("Connected, but the test message could not be sent.")
    offer_switch("telegram")


def do_telegram_channel():
    print("""
TELEGRAM CHANNEL
 1. Create a channel in Telegram.
 2. In the channel settings, add your bot as an administrator with permission to post.
""")
    c = load()
    tok = ask("Bot token (press Enter to reuse the one from the private chat step)", secret=True) or (c.get("telegram") or {}).get("token", "")
    ch = ask("Channel name, like @mychannel")
    if not tok or not ch:
        print("Both are needed.")
        return
    try:
        info = tg_api(tok, "getChat", chat_id=ch)["result"]
    except Exception:
        print("Could not reach that channel. Is the bot an administrator there?")
        return
    c["telegram_channel"] = {"connected": True, "token": tok, "chat_id": info["id"], "account": info.get("title", ch)}
    save(c)
    print("Connected to the channel: " + info.get("title", ch))
    offer_switch("telegram_channel")


def do_bluesky():
    print("""
BLUESKY
 1. Open Bluesky, Settings, Privacy and Security, App Passwords.
 2. Press Add App Password, give it any name, copy the password it shows.
""")
    import bluesky
    h = ask("Your handle, like name.bsky.social")
    p = ask("The app password", secret=True)
    msg = bluesky.connect(h, p)
    print(msg)
    if msg.startswith("Connected"):
        offer_switch("bluesky")


def do_discord():
    print("""
DISCORD
 1. Go to https://discord.com/developers/applications and create an application.
 2. Open Bot, press Reset Token, copy the token.
 3. Invite the bot to your server (OAuth2, URL Generator, scope bot, permission Send Messages and Attach Files).
 4. In Discord turn on Developer Mode, right click your channel, Copy Channel ID.
""")
    import discord_bot
    t = ask("Bot token", secret=True)
    ch = ask("Channel ID")
    msg = discord_bot.connect(t, ch)
    print(msg)
    if "onnected" in str(msg) and not str(msg).startswith("Error"):
        offer_switch("discord")


def do_mastodon():
    print("""
MASTODON
 1. On your Mastodon server open Preferences, Development, New application.
 2. Tick write:statuses and write:media, save, copy 'Your access token'.
""")
    import mastodon
    inst = ask("Your server, like mastodon.social")
    tok = ask("Access token", secret=True)
    msg = mastodon.connect(inst, tok)
    print(msg)
    if "onnected" in str(msg) and not str(msg).startswith("Error"):
        offer_switch("mastodon")


def do_x():
    print("""
X
 Posting on X needs a paid API plan. In the X developer portal create an app with
 read and write permission, then copy the keys and tokens.
""")
    c = load()
    x = c.get("x") or {}
    for k, label in (("api_key", "API key"), ("api_key_secret", "API key secret"), ("access_token", "Access token"),
                     ("access_token_secret", "Access token secret"), ("bearer", "Bearer token")):
        x[k] = ask(label, secret=True)
    x["connected"] = all(x.get(k) for k in ("api_key", "api_key_secret", "access_token", "access_token_secret"))
    x["account"] = "keys saved"
    c["x"] = x
    save(c)
    print("Saved. X is not tested here because posting needs the paid plan.")
    if x["connected"]:
        offer_switch("x")


def do_youtube():
    cl = ROOT / "config" / "youtube_client.json"
    print("""
YOUTUBE
 1. Go to https://console.cloud.google.com and create a project.
 2. Enable 'YouTube Data API v3'.
 3. APIs and Services, Credentials, Create credentials, OAuth client ID, type Desktop app.
 4. Press Download JSON.
""")
    if not cl.exists():
        p = ask("Path of the downloaded JSON file (drag the file into this window)").strip('"')
        if not p or not Path(p).exists():
            print("File not found.")
            return
        shutil.copy2(p, cl)
        print("Saved as config\\youtube_client.json")
    print("A browser window opens now. Choose your Google account and allow access.")
    import youtube
    msg = youtube.authorise()
    print(msg)
    if str(msg).startswith("Connected"):
        offer_switch("youtube")


def do_tiktok():
    cf = ROOT / "config" / "tiktok_client.json"
    print("""
TIKTOK
 1. Go to https://developers.tiktok.com and create an app. Add the product 'Content Posting API'.
 2. TikTok needs a real web address for the callback. Put the file assets\\legal\\callback.html on any
    free web host (for example GitHub Pages) and use its address as the Redirect URI in your TikTok app.
 3. Copy the Client key and Client secret.
 New apps upload as private until TikTok approves the app. That is normal.
""")
    ck = ask("Client key")
    cs = ask("Client secret", secret=True)
    ru = ask("Redirect URI (the web address of your callback page)")
    if not (ck and cs and ru):
        print("All three are needed.")
        return
    cf.write_text(json.dumps({"client_key": ck, "client_secret": cs}), encoding="utf-8")
    (ROOT / "config" / "tiktok_redirect.txt").write_text(ru, encoding="utf-8")
    import tiktok
    tiktok.authorise_start()
    code = ask("Paste the code shown on your callback page")
    print(tiktok.authorise_finish(code))
    offer_switch("tiktok")


def do_blog():
    print("""
YOUR OWN BLOG (optional)
 Only if you run a compatible blog of your own. Enter its web address and its admin password.
""")
    c = load()
    url = ask("Blog address, like https://blog.example.com")
    pw = ask("Blog admin password", secret=True)
    if not url:
        return
    s = c.get("socialai") or {}
    s.update({"connected": True, "url": url, "public_url": url, "password": pw, "public_password": pw,
              "target": "public", "account": url})
    c["socialai"] = s
    save(c)
    print("Saved.")
    offer_switch("socialai")


def do_facebook():
    print("""
FACEBOOK PAGE
 1. Go to https://developers.facebook.com and create an app (type Business).
 2. In the Graph API Explorer pick your Page, add the permissions pages_manage_posts and
    pages_read_engagement, and generate a Page access token.
 3. Copy your Page ID from the Page's About section.
""")
    import meta
    tok = ask("Page access token", secret=True)
    pid = ask("Page ID")
    msg = meta.connect(tok, pid)
    print(msg)
    if str(msg).startswith("Connected"):
        offer_switch("facebook")


def do_threads():
    print("""
THREADS
 1. Go to https://developers.facebook.com, create an app and add the 'Threads API' use case.
 2. Add your Threads account as a tester and accept the invite in the Threads app.
 3. Generate a user access token with threads_basic and threads_content_publish.
 4. Copy your Threads user ID (shown next to the token).
 Threads posts go out as text. Pictures and video need public web addresses, so they are sent as text.
""")
    c = load()
    uid = ask("Threads user ID")
    tok = ask("Access token", secret=True)
    if not uid or not tok:
        print("Both are needed.")
        return
    try:
        me = json.loads(urllib.request.urlopen("https://graph.threads.net/v1.0/%s?fields=username&access_token=%s"
                                               % (uid, urllib.parse.quote(tok)), timeout=20).read())
    except Exception as e:
        print("Threads did not accept those details: " + str(e)[:80])
        return
    c["threads"] = {"connected": True, "user_id": uid, "token": tok, "account": "@" + str(me.get("username", uid))}
    save(c)
    print("Connected as @" + str(me.get("username", uid)))
    offer_switch("threads")


def do_linkedin():
    print("""
LINKEDIN
 1. Go to https://www.linkedin.com/developers/apps and create an app.
 2. Under Products add 'Share on LinkedIn' and 'Sign In with LinkedIn using OpenID Connect'.
 3. Under Auth, generate an access token with the scopes w_member_social, openid and profile.
 LinkedIn tokens expire after about 60 days. Run this again to renew it.
""")
    c = load()
    tok = ask("Access token", secret=True)
    if not tok:
        return
    try:
        rq = urllib.request.Request("https://api.linkedin.com/v2/userinfo", headers={"Authorization": "Bearer " + tok})
        me = json.loads(urllib.request.urlopen(rq, timeout=20).read())
    except Exception as e:
        print("LinkedIn did not accept that token: " + str(e)[:80])
        return
    c["linkedin"] = {"connected": True, "token": tok, "author": "urn:li:person:" + me.get("sub", ""),
                     "account": me.get("name", "LinkedIn profile")}
    save(c)
    print("Connected as " + me.get("name", "your profile"))
    offer_switch("linkedin")


def do_wordpress():
    print("""
WORDPRESS (your own site)
 1. In your WordPress dashboard open Users, Profile, scroll to 'Application Passwords'.
 2. Type any name, press Add, and copy the password it shows (spaces are fine).
 Works with self hosted WordPress and any site that has the standard REST API.
""")
    import base64
    c = load()
    url = ask("Site address, like https://myblog.com").rstrip("/")
    user = ask("Your WordPress user name")
    pw = ask("The application password", secret=True)
    if not (url and user and pw):
        print("All three are needed.")
        return
    try:
        tok = base64.b64encode(("%s:%s" % (user, pw)).encode()).decode()
        rq = urllib.request.Request(url + "/wp-json/wp/v2/users/me", headers={"Authorization": "Basic " + tok})
        me = json.loads(urllib.request.urlopen(rq, timeout=20).read())
    except Exception as e:
        print("The site did not accept those details: " + str(e)[:80])
        return
    c["wordpress"] = {"connected": True, "url": url, "user": user, "app_password": pw, "status": "publish",
                      "account": "%s on %s" % (me.get("name", user), url)}
    save(c)
    print("Connected as " + str(me.get("name", user)))
    offer_switch("wordpress")


def do_webhook():
    print("""
WEBHOOK (reach almost any other app)
 Every post is sent to a web address you choose. Tools like Zapier, Make, n8n or IFTTT can then
 pass it on to Instagram, Pinterest, Reddit, Tumblr, a spreadsheet, an email and many more.
 1. In your tool create a flow that starts with 'Webhook' and copy its address.
 2. The post arrives as JSON with these fields: app, created_by, kind, title, text.
""")
    c = load()
    url = ask("Webhook address (starts with https://)")
    if not url.startswith("http"):
        print("That does not look like a web address.")
        return
    sec = ask("Optional secret to send in the header X-Webhook-Secret (Enter to skip)", secret=True)
    sf = yes("Also upload the picture or video file with each post?", default=False)
    c["webhook"] = {"connected": True, "url": url, "secret": sec, "send_file": sf, "account": url[:60]}
    save(c)
    print("Saved. A test message is sent now.")
    try:
        import extra_publishers
        print("Result: " + extra_publishers.send_webhook(c["webhook"], "Test from AutoPost Automation. Nothing to do.", None, "text", "Test"))
    except Exception as e:
        print("The test did not go through: " + str(e)[:100])
        print("Check the address in your tool. The details are saved, run this again to change them.")
    offer_switch("webhook")


ACTIONS = {"telegram": do_telegram, "telegram-channel": do_telegram_channel, "bluesky": do_bluesky,
           "discord": do_discord, "mastodon": do_mastodon, "x": do_x, "youtube": do_youtube,
           "tiktok": do_tiktok, "blog": do_blog, "facebook": do_facebook, "threads": do_threads,
           "linkedin": do_linkedin, "wordpress": do_wordpress, "webhook": do_webhook}


def menu():
    print("\nWhich account do you want to connect?\n")
    keys = list(ACTIONS)
    for i, k in enumerate(keys, 1):
        print("  %d  %s" % (i, k))
    print("  s  show status\n  q  quit")
    while True:
        v = ask("\nChoose a number")
        if v.lower() == "q":
            return
        if v.lower() == "s":
            status()
            continue
        if v.isdigit() and 1 <= int(v) <= len(keys):
            ACTIONS[keys[int(v) - 1]]()
        else:
            print("Type one of the numbers, s or q.")


def main():
    a = sys.argv[1:]
    if not a:
        menu()
    elif a[0] == "status":
        status()
    elif a[0] in ("on", "off"):
        if len(a) < 2:
            print("Say which platforms, for example:  connect.py on bluesky telegram")
            return
        switch(a[1:], a[0] == "on")
    elif a[0] in ACTIONS:
        ACTIONS[a[0]]()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
