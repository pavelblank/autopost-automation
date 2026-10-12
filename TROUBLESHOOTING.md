# Troubleshooting: common problems and how to fix them

Start here every time:

```
.\DOCTOR.bat
```

It prints a checklist of what you have and what is missing. Under every missing
item it says **why it matters**, the **cause** and **how to fix it**. The cause is
one of these:

| Cause | Meaning |
|---|---|
| not installed yet | A tool is missing. Run `.\INSTALL.bat` and it installs it. |
| not set up yet | The tool is there but a setting is missing. `.\INSTALL.bat` sets it. |
| your computer | The hardware is too small for that feature. Everything else still works. |
| internet | The computer cannot reach a website the feature needs. |
| a step only you can do | For example logging in to your own account or changing your password. |

If an item is not marked as a program problem, it is not one. The report is made
to show what is not ready and why, not to blame anything.

---

## Installing

**`winget` was not found**
Cause: Windows is missing its installer tool.
Fix: Open the Microsoft Store, search **App Installer**, install or update it, then run `.\INSTALL.bat` again.

**PowerShell says running scripts is disabled**
Cause: a Windows safety setting.
Fix: Use `.\INSTALL.bat`. It starts PowerShell with the right setting for that one run, and changes nothing permanent.

**`python` is not recognised after the install**
Cause: the window was opened before Python was installed, so it does not know the new program yet.
Fix: Close PowerShell, open it again, run `.\INSTALL.bat` again.

**The package install stops with a red error**
Cause: usually a temporary network problem.
Fix: Run `.\INSTALL.bat` again. It continues where it stopped. If one package keeps failing, the message names it. Run `venv\Scripts\python.exe -m pip install NAME` for that one.

**The model download is slow or stops**
Cause: it is a large file (4 to 9 GB).
Fix: Leave the window open. If it stops, run `.\INSTALL.bat` again. The download continues.

**Not enough disk space**
Cause: your computer. The program needs at least 15 GB free, and 100 GB if you make local video.
Fix: Free space on the drive, then run `.\INSTALL.bat` again.

## Text model (Ollama)

**DOCTOR says "Ollama is running: MISSING"**
Cause: the Ollama app is not running.
Fix: Open **Ollama** from the Start menu. Or run `ollama serve` in a PowerShell window and leave it open.

**DOCTOR says the text model is not installed**
Fix: `ollama pull qwen2.5:7b` (or the name DOCTOR shows), then run `.\DOCTOR.bat` again.

**The model does not answer in time**
Cause: the first answer after starting loads the model into memory, which can take a minute. If it never answers, the model is too big for your memory.
Fix: Run `.\DOCTOR.bat` again. If it still fails, use a smaller model: `ollama pull qwen2.5:3b`, then run `.\INSTALL.bat`, which sets it for you.

**Posts are very short or poor**
Cause: a small model writes simpler text.
Fix: Use a bigger model if your memory allows. 16 GB memory suits a 7B model, 32 GB suits a 14B model.

## Starting

**The dashboard does not open**
Fix: Wait 20 seconds and refresh `http://localhost:8600`. Run `.\DOCTOR.bat`. If something else uses port 8600, start on another port:
```
$env:AUTOPOST_PORT=8700
.\START.bat
```
Then open `http://localhost:8700`.

**"already running, this copy will exit"**
Cause: the program is already running. This is the safety lock that stops two copies running.
Fix: Look for the browser tab, or run `.\STOP.bat` and then `.\START.bat`.

**I forgot the dashboard password**
Fix: Open `config\rules.json`, find `"dashboard_auth"`, and write a new password. Then `.\STOP.bat` and `.\START.bat`.

## Posting

**Posts are written but nothing is published**
Cause: this is the safe default. Posting is off until you turn it on.
Fix: Connect an account (`.\CONNECT.bat`), then open the dashboard, Settings, Live status, and press **Turn automation on**.

**An account says it is not connected**
Fix: `.\CONNECT.bat status` shows the state. Connect it again with `.\CONNECT.bat NAME`.

**An account is connected but nothing goes there**
Cause: it is connected but not switched on for posting.
Fix: `.\CONNECT.bat on NAME`.

**One platform fails and the others work**
Cause: that platform refused the post or its details changed.
Fix: Open **Conversations**, pick the job, read the message from ELLA or VICTORIA. It names the platform and the reason. Then reconnect that platform.

**Telegram: "I could not see a message yet"**
Fix: Open your bot in Telegram, press START, send it any message, then press Enter in the window.

**Telegram channel: the channel cannot be reached**
Fix: The bot must be an administrator of the channel with permission to post.

**YouTube: no browser window opens**
Fix: Copy the address the tool prints into your browser. Choose your Google account and allow access.

**YouTube: "access blocked" or "app not verified"**
Cause: your Google project is in testing mode.
Fix: In the Google Cloud console, OAuth consent screen, add your own Google account as a test user.

**TikTok: videos are private**
Cause: new TikTok apps can only upload as private until TikTok approves the app.
Fix: This is normal. Make the video public in the TikTok app, or submit your app for review.

**X: posting fails**
Cause: posting on X needs a paid API plan.
Fix: Upgrade your X developer plan, then run `.\CONNECT.bat x` again.

**LinkedIn: the token stopped working**
Cause: LinkedIn tokens expire after about 60 days.
Fix: Run `.\CONNECT.bat linkedin` and paste a new token.

**Threads, Instagram, Pinterest and others**
Threads works with `.\CONNECT.bat threads`. For Instagram, Pinterest, Reddit, Tumblr and more, use `.\CONNECT.bat webhook` with a Zapier, Make or n8n flow.

## Video

**"No NVIDIA graphics card"**
Cause: your computer. Local video needs an NVIDIA card with 8 GB of memory or more.
Fix: Text and image posts work without it. Nothing is wrong with the program.

**Video setup fails at PyTorch**
Fix: Update your NVIDIA driver from nvidia.com, then run `.\INSTALL-VIDEO.bat` again.

**A video takes very long**
Cause: normal. A short video takes about 25 minutes on an 8 GB card. The system watches a heartbeat, not the clock, and never stops a render that is still working.

## Running day to day

**The computer went to sleep and posts were missed**
Cause: Windows power settings.
Fix: Set sleep to Never while plugged in. After a restart, the system catches up safely.

**The disk is filling up**
Cause: finished posts and videos are kept for 7 days after publishing.
Fix: The clean up manager deletes them automatically after 7 days. Check `data\retention.log`. You can lower the days in `config\rules.json`, setting `retention`, `final_days`.

**Several posts appear in the waiting list but nothing is published**
Cause: posts are made ahead of time and wait for their time slot. This is how it is designed.
Fix: Check **Posts**, Today's plan, for the next slot time.

**A job shows "stopped"**
Fix: Most failures fix themselves within 10 to 30 minutes. If one stays, open **Conversations**, pick the job and read the last message. It says what happened and what was tried.

## Still stuck?

1. Run `.\DOCTOR.bat` and keep the output.
2. Look at the newest file in the `logs` folder.
3. Open an issue on the GitHub project page and paste the DOCTOR output. Never paste your `connections.json`.

---

Created by Mr Blank. See [LICENSE](LICENSE).
