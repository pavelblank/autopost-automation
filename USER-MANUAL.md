# AutoPost Automation: User Manual

How to use AutoPost Automation every day. If you have not installed it yet, start
with [INSTALL.md](INSTALL.md).

---

## 1. The big picture

AutoPost Automation makes posts and publishes them for you. A post goes through
a team of 14 agents. Each agent does one small job and hands the work to the next.

There are three parts that work together:

| Part | What it does | When |
|---|---|---|
| Normal flow | Makes and publishes eight posts a day at set times | At each time slot |
| Idle time flow | Makes posts ahead of time while the computer is free, and keeps them ready | Every 10 minutes it checks if the machine is free |
| Clean up flow | Deletes old work files after a post is confirmed live | Every hour |

The normal schedule always comes first. Idle work never makes a scheduled post late.

## 2. First day checklist

1. Start the program with `START.bat`.
2. Log in and change the password (see INSTALL, step 7).
3. Connect at least one account (section 5).
4. Open **Settings**, then **Live status**. Press **Turn automation on**.
5. Watch the **Agent Office** page. Within a few minutes the research agent starts
   finding topics, and when the computer is free the team starts making posts
   ahead of time.

If you only want to try it without posting anything, skip step 3 and 4. The
program stays paused and publishes nothing.

## 3. The dashboard pages

### Overview

- **Status pills** at the top: Dashboard, Ollama (the text model), Trigger poller (the engine that starts jobs), Department (on or off), Posting (live or paused).
- **Safety rails**: a plain list of what can and cannot happen right now.
- **Organisation**: three views of the team.
  - **Chain**: the seven stages a post passes through, top to bottom.
  - **Org chart**: who reports to whom. Mr Blank at the top, the Department Head, five teams, and the always on operations desk.
  - **Architecture**: the three systems side by side with their triggers.
  - The dot colour is the live state: blue is working now, green is done or healthy, grey is waiting, amber is retrying, red is stopped.
- **This PC**: the services, the graphics card and the disk.

### Posts

- **Published posts**: what went out, where, and when.
- **Waiting lists**: research topics, and the finished posts waiting for their time slot (video, image with text, text only). The bar shows how many are ready out of the target of five.
- **Recent jobs**: time, topic, format and where it was published.
- **Today's plan** and **Platform connections**.

### Research

The topics the research agent found, where each came from, and which ones were
refused and why.

### Conversations

Pick a job on the left. On the right you see the work chat: every handover between
agents, who is waiting for whom, and every rejection with the reason.

### Agent Office

A live picture of the team. Every sprite is a real agent reading its state from the
database. Working agents sit at their desk and a speech bubble shows what they are
doing. Idle agents wander to the four corners: cafeteria, meeting room, game zone
and lounge. The tabs next to the picture show a table of agents, the activity feed
and a readiness check. Viewing the office never changes anything.

### Settings

- **Live status**: turn automation on or off.
- **Connections**: which platforms are connected and switched on.
- **Posting limits**: the most posts per day for each platform.
- **Safety rails**: blocked words and privacy rules.
- **Behaviour** and **Department**: how the team works.

## 4. The agents, in plain words

| Agent | Job |
|---|---|
| NADIA | Opens the job. Picks the best topic for the post that is due. Checks it has not been used before. |
| ARJUN | Researches. Finds topics and fetches real source text so facts can be checked. |
| MAYA | Creative director. Writes the brief. |
| SOPHIE | Writes the script and the text. Uses only verified facts. |
| DANIEL | Plans the scenes, characters and backgrounds for video and image posts. |
| LEO | Chooses the feeling and the music. |
| OLIVER | Audits the plan. Looks for missing parts, garbage text and unproven facts. |
| ISABELLA | Editor in chief. Makes everything consistent, then gives the go ahead. |
| NOAH | Makes the video, image or text from the approved files. |
| GRACE | Inspects the result. Checks the file, the length, the sound and that the text is the approved text. |
| ELLA | Publishes, one platform at a time, inside each platform's limits. |
| VICTORIA | Checks every post is really live. |
| HELENA | Department head. Final audit. Sends one summary message and records the topic so it is never picked again. |

Four helpers are always on and are not part of one job:

| Helper | Job |
|---|---|
| SCHEDULE MANAGER | The clock. Starts each of the eight daily posts. |
| IDLE TIME MANAGER | Starts making posts ahead of time when the machine is free. |
| SYSTEM WATCHDOG | Finds stuck jobs and fixes them. Tells you only when it cannot. |
| CLEAN UP MANAGER | Deletes old work files, carefully. |

## 5. Connecting your accounts

Run `.\CONNECT.bat` and pick an account from the list, or connect one directly:

```
.\CONNECT.bat telegram
.\CONNECT.bat telegram-channel
.\CONNECT.bat bluesky
.\CONNECT.bat discord
.\CONNECT.bat mastodon
.\CONNECT.bat facebook
.\CONNECT.bat threads
.\CONNECT.bat linkedin
.\CONNECT.bat wordpress
.\CONNECT.bat webhook
.\CONNECT.bat youtube
.\CONNECT.bat tiktok
.\CONNECT.bat x
.\CONNECT.bat blog
```

The tool shows the exact steps for the account first, asks for the details, tests them,
saves them on this computer only, and then asks if you want to switch posting on for it.

| Platform | You will need | Posts as |
|---|---|---|
| Telegram | A bot from @BotFather | text, image, video |
| Telegram channel | A channel where the bot is an administrator | text, image, video |
| Bluesky | Handle and an app password | text, image |
| Discord | A bot token and a channel id | text, image, video |
| Mastodon | Server name and access token | text, image |
| Facebook Page | A Page access token and the Page ID | text, image, video |
| Threads | A Threads user ID and access token | text |
| LinkedIn | An access token (renew about every 60 days) | text |
| WordPress | Site address, user name and an application password | full article, with the picture |
| Webhook | A web address from Zapier, Make, n8n or IFTTT | everything, passed on to any app |
| YouTube | A Google project with the YouTube Data API, approved in your browser | video |
| TikTok | A TikTok developer app and a hosted callback page | video, private until approved |
| X | Paid API keys | text |
| Your own blog | Its address and password | full post |

Instagram, Pinterest, Reddit, Tumblr and other apps: use the Webhook, then let Zapier, Make or
n8n pass the post on.

Useful commands:

```
.\CONNECT.bat status                 shows what is connected and what is switched on
.\CONNECT.bat on bluesky telegram    switches posting on for these
.\CONNECT.bat off bluesky            switches posting off
```

A platform is used only if it is connected and switched on. Nothing is posted until you
turn automation on (Settings, Live status).

Never share `config\connections.json`. It holds your passwords and tokens. The project ships
only an empty example file, and `.gitignore` keeps the real one out of Git.

## 6. The daily schedule

Eight posts a day, at these times:

| Time | Post |
|---|---|
| 8:45 AM | Image with text |
| 10:15 AM | Text |
| 12:00 PM | Photo style video |
| 2:30 PM | Image with text |
| 5:30 PM | Text |
| 7:30 PM | Image with text |
| 9:30 PM | Text |
| 11:00 PM | Cartoon video |

To change the times, open `scripts\dispatcher.py` and edit the list called `SLOTS`
near the top. Restart the program afterwards.

## 7. Idle time posts (made ahead of time)

When nothing is running, the graphics card is free and the next slot is far enough
away, the team makes a full post and puts it on a waiting list instead of
publishing it. At the slot time the post is checked once more and published within
seconds.

The order of work is video first, then image with text, then text, until each list
has five posts ready. A video is only started if the next slot is at least 120
minutes away. Image needs 30 minutes and text needs 20.

If three stock jobs fail within three hours, idle production pauses for two hours,
then tries again.

## 8. Clean up

Seven days after a post is confirmed live, the clean up manager deletes its work
files and final files. Logs older than 14 days are deleted too. Every deletion is
written to `data\retention.log` before it happens.

It never deletes: posts that are not confirmed published, failed jobs waiting for a
retry, posts on the waiting lists, the database, your settings and the program
files.

## 9. Safety rules

- It starts paused. Nothing is published until you turn automation on.
- A post may only state names, numbers, dates and quotes that came from a real
  source the research agent fetched. No usable source means no factual claims.
- No photo style video about a real named person.
- No first person personal stories as topics.
- A topic is never used twice. Every topic is checked against everything published.
- The maker never starts without the editor's approval file. The publisher never
  publishes without the release decision.
- Platforms are used only if they are on the allowed list.
- New YouTube and TikTok uploads start private.

## 10. When something goes wrong

The system tries to fix problems itself, in this order:

1. Try again after 30 seconds, then 2 minutes, then 10 minutes.
2. Send the work back to the exact agent that can fix it, with a clear reason.
3. Change the approach: another model, another voice, another topic.
4. Park the job and carry on with the next slot, so a stuck job never blocks the line.
5. Tell you, with what happened, what was tried and what to do.

If you do see a problem:

| Problem | What to do |
|---|---|
| Overview shows a red pill | Read the pill text. Ollama down: open the Ollama app. Poller stopped: press START.bat again. |
| A job shows stopped in Conversations | Read the last message of the job. Most failures fix themselves within 10 to 30 minutes. |
| One platform failed | Only that platform fails. The others still publish. Check its details in `connections.json`. |
| Nothing is being made | Check Settings, Live status. Automation may be off. Check the Ollama pill. |
| Disk filling up | Check `out\` and `STOCK\`. The clean up manager handles old files after 7 days. |

## 11. Useful commands

`.\DOCTOR.bat` shows what your computer can run, what is ready and what is missing, with the reason and the fix for each.

Run these in PowerShell in the project folder.

```
venv\Scripts\python.exe scripts\department_queue.py --status
venv\Scripts\python.exe scripts\stock.py --list
venv\Scripts\python.exe tools\test_department.py
```

The first shows the state of all jobs. The second lists the waiting posts. The
third runs the tests on a scratch copy of the database. The tests never post anything.

## 12. Stop and start

- `START.bat` starts everything.
- `STOP.bat` stops the dashboard, the engine and the guard of this folder. It does
  not stop Ollama.
- `START-DASHBOARD-ONLY.bat` starts only the dashboard, for looking around.

## 13. Back up

Copy these two folders somewhere safe now and then:

- `config` (your settings and account details, keep it private)
- `data` (the database and history)

## 14. About the author and the license

AutoPost Automation was created by **Mr Blank**. You can use it, change it and
share it for free. You cannot remove the author name or say the work is yours.
The credit is in every source file, in the dashboard footer and in the logs.
It is protected by a digital signature that only the author can create. If the
credit files are changed or deleted, the program puts them back. A copy that no
longer matches the author's signature shows a notice on every page saying it is an
unofficial modified copy.

Read the full rules in [LICENSE](LICENSE).
