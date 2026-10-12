# Install AutoPost Automation

Windows 10 or 11. The installer does the hard work for you: it checks your
computer, installs what is missing, finds your local text model and connects to
it. You copy a few lines and press Enter. Plan for about 20 minutes, most of it
downloads.

---

## What computer do you need?

The system was built and tested on this computer. Yours does not need to be this
strong, but the closer it is, the more it can do.

| | Reference computer (tested) | Comfortable | Minimum |
|---|---|---|---|
| System | Windows 11 | Windows 10 or 11 | Windows 10 or 11 |
| Processor | Intel Core i9-14900HX (24 cores) | 8 cores or more | 4 cores |
| Memory | 32 GB | 16 GB | 8 GB |
| Graphics card | NVIDIA RTX 4060 Laptop, 8 GB | none needed for text and image posts | none |
| Free disk space | 100 GB or more | 40 GB | 15 GB |
| Text model | qwen2.5:14b | qwen2.5:7b | qwen2.5:3b |
| What works | Everything, with local video | Text and image posts | Text posts, simpler writing |

Local video needs an NVIDIA graphics card with 8 GB of memory or more. Without one,
everything else still works.

After you install, `DOCTOR.bat` tells you in plain words what your computer can run.

---

## The short way (three steps)

**Step 1. Download.** Open PowerShell (Start menu, type `PowerShell`) and run:

```
cd $HOME
winget install --id Git.Git -e --silent
```

Close PowerShell, open it again, then run:

```
git clone https://github.com/pavelblank/autopost-automation.git
cd autopost-automation
```

No Git? On the project page on GitHub press **Code**, **Download ZIP**, unzip it,
and open PowerShell inside the unzipped folder.

**Step 2. Install everything.** In the same window run:

```
.\INSTALL.bat
```

It does these things by itself, and tells you what it is doing:

1. Checks `winget`, the Windows installer.
2. Installs Python 3.13 if you do not have 3.11 or newer.
3. Installs FFmpeg if it is missing.
4. Installs Ollama (the local text model) if it is missing, starts it and checks it answers.
5. Creates the Python environment and installs every package.
6. Creates your settings files and saves the FFmpeg location.
7. Looks at your memory, picks a model that fits, uses one you already have or downloads one, and writes the name into your settings.
8. Runs a health check and shows what your computer can run.

Run it again at any time. It only does what is missing.

**Step 3. Start.**

```
.\START.bat
```

Your browser opens at `http://localhost:8600`. Log in with user `admin` and
password `change-me`, then change the password (see "First login" below).

That is the whole install. Posting stays off until you connect an account and turn
automation on.

---

## Connect your social accounts

Run this and pick an account from the list. It asks simple questions, tests the
details, and saves them on your computer only.

```
.\CONNECT.bat
```

Or connect one account directly. Copy the line, press Enter, answer the questions:

```
.\CONNECT.bat telegram
.\CONNECT.bat telegram-channel
.\CONNECT.bat bluesky
.\CONNECT.bat discord
.\CONNECT.bat mastodon
.\CONNECT.bat youtube
.\CONNECT.bat tiktok
.\CONNECT.bat x
.\CONNECT.bat blog
```

What each one asks for, in short:

| Account | You will need |
|---|---|
| Telegram | A bot made with @BotFather. The tool finds your chat by itself after you message the bot. |
| Telegram channel | A channel where your bot is an administrator. |
| Bluesky | Your handle and an app password from Bluesky settings. |
| Discord | A bot token and the channel id. |
| Mastodon | Your server name and an access token. |
| YouTube | A Google project with the YouTube Data API. The tool opens your browser to approve it. |
| TikTok | A TikTok developer app and a web page for the callback (the tool explains it). New apps upload as private until TikTok approves them. |
| X | Paid API keys. |
| Blog | The address and password of your own blog, if you have one. |

The tool shows the exact steps for each account before it asks anything.

See what is connected and what is switched on for posting:

```
.\CONNECT.bat status
```

Switch posting on or off for chosen accounts:

```
.\CONNECT.bat on bluesky telegram
.\CONNECT.bat off bluesky
```

Then open the dashboard, go to **Settings**, **Live status**, and press **Turn
automation on**. Nothing is posted before that.

---

## First login

Log in with `admin` and `change-me`. To change them, open `config\rules.json`
in Notepad, find `"dashboard_auth"`, and write your own user name and password.
Save the file, double click `STOP.bat`, then double click `START.bat`.

## Check that everything is healthy

```
.\DOCTOR.bat
```

It shows your computer, whether the text model answers, whether FFmpeg and all
packages are there, and which accounts are connected. Lines that start with `NOTE`
are advice. Lines that start with `FAIL` need fixing, and each one tells you how.

On the **Overview** page you should see Dashboard up, Ollama up, Department off and
Posting paused. That is correct for a first start.

## Optional: make video on your own graphics card

Only if you have an NVIDIA card with 8 GB or more.

```
.\INSTALL-VIDEO.bat
```

It checks your card, installs the video libraries and downloads the video model
(about 22 GB in total). A short video then takes roughly 25 minutes to render on an 8 GB card.

## Start automatically when Windows starts

```
schtasks /Create /TN "AutoPost Automation" /TR "\"%CD%\START.bat\"" /SC ONLOGON /RL LIMITED /F
```

Run that once, inside the project folder, in PowerShell. To remove it later:
`schtasks /Delete /TN "AutoPost Automation" /F`.

For round the clock posting, set Windows power settings so the computer does not
sleep while it is plugged in.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| `winget` was not found | Open the Microsoft Store, install or update **App Installer**, then run `.\INSTALL.bat` again. |
| A PowerShell window says scripts are disabled | Use `.\INSTALL.bat`. It starts PowerShell with the right setting for you. |
| The installer stops at Ollama | Open the **Ollama** app from the Start menu, then run `.\INSTALL.bat` again. |
| The model download is slow | It is a large file. Leave the window open. If it stops, run `.\INSTALL.bat` again and it continues. |
| The dashboard does not open | Wait 20 seconds and refresh. Run `.\DOCTOR.bat`. If port 8600 is busy, set another port first: `$env:AUTOPOST_PORT=8700` then `.\START.bat`. |
| "already running, this copy will exit" | It is already running. Look for the browser tab, or run `.\STOP.bat` first. |
| Posts are written but nothing is published | That is the safe default. Connect an account and turn automation on. |
| An account says it is not connected | Run `.\CONNECT.bat status`, then connect it again. |

## Update to a new version

```
.\STOP.bat
git pull
.\INSTALL.bat
.\START.bat
```

Your settings and your data are never part of the download, so an update does not
touch them.

---

Created by Mr Blank. See [LICENSE](LICENSE).
