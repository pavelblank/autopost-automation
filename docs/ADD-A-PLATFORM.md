# How to add a new place to post

Use this when you want to add another social site later, for example a Facebook group, X,
Reddit or any other page that allows automatic posting. It takes about six small steps.
Look at how Threads or WordPress is done and copy the pattern.

First check that the site really allows automatic posting. You need an official way in,
such as an API, an access token or a webhook. Never use a password and fake browser clicks.

## The six steps

**1. Write the sender.** Open `scripts/extra_publishers.py` and add a function:

```python
def send_reddit(c, text, media_path=None, kind="text", title=""):
    # c is the settings block saved for this platform
    # return the link of the new post, or a short proof text
    # raise RuntimeError("clear message") when something is wrong
```

Then add it to the `SENDERS` list and to `PLATFORMS` at the bottom of the file.

**2. Teach the tool to connect it.** Open `scripts/connect.py`. Copy one of the `do_...` functions
(for example `do_wordpress`). It prints the steps, asks for the details, tests them, saves them and
calls `offer_switch("name")`. Add it to `ACTIONS`, `NAMES` and `NICE`.

**3. Tell the publisher to use it.** Open `scripts/publish.py`, find the line
`for _xp in ("threads", "linkedin", "wordpress", "webhook"):` and add your platform name to it.

**4. Say what it can carry.** Open `scripts/dispatcher.py`, find `_KIND_TARGETS`, and add the
name to the kinds of post it accepts (text, image, video).

**5. Set the length rules.** Open `scripts/content_format.py`, find `PROFILES`, and add a line with
the character limit of the platform, how many hashtags it likes, and whether it takes long text.

**6. Make it show up nicely.** Add the friendly name to `PLATFORM` in `scripts/report_format.py`,
and to the `NAME` list in `assets/ui/home.html`. Optionally add a posting limit under `limits` in
`config/rules.json`.

## Test it before real use

1. Copy `tools/test_extra_publishers.py` and add a case. It uses a fake local server, so nothing
   goes online.
2. Run it: `venv\Scripts\python.exe tools\test_extra_publishers.py`.
3. Connect the real account: `.\CONNECT.bat yourname`.
4. Switch it on: `.\CONNECT.bat on yourname`.
5. Run `.\DOCTOR.bat` and check the platform shows as ready.

## Good to know

- The live check by VICTORIA accepts a platform that returns a web link, or a clear proof text.
  If the site gives you no link, return a short text such as `reddit:accepted`.
- A platform is used only when it is connected and switched on.
- Each platform has its own limits. Keep the limit in step 5 a little below the real one.
- New sender code that is not covered by a test should be tested on a private or test account first.

Created by Mr Blank. See the [LICENSE](../LICENSE).
