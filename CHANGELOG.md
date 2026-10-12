# Changelog

## Version 3.0

AutoPost Automation 3.0 is the first version where the whole thing runs by itself,
day and night, without anyone touching it.

### New

- **Three systems that work together:** the normal flow, the idle time flow and the clean up flow.
- **Idle time posts.** Posts are made ahead of time while the computer is free and kept on waiting lists (video, image with text, text). At each time slot the post goes out within seconds.
- **A team of 14 agents** in place of the older 18 step chain. Several jobs were merged, so there are fewer handovers and fewer things that can stall.
- **Four always on helpers:** schedule manager, idle time manager, system watchdog and clean up manager.
- **Research that never stops.** The research agent keeps at least 10 checked topics ready, from news feeds, trends, video search and your own list, each on its own timer.
- **No invented facts.** Posts may only state facts from a real source that was fetched. No source means no factual claims. Checked by three agents.
- **A new dashboard.**
  - Organisation page with three views: chain, org chart and architecture.
  - Agent Office with the four break rooms in the four corners and the work rooms spread out.
  - Posts page with the waiting lists, recent jobs and the day plan.
  - Conversations page with the work chat of every job.
- **Clean up.** Old work files are deleted seven days after a post is confirmed live. Every deletion is logged first. Unpublished work is never touched.
- **Self healing.** Stuck jobs are found and fixed automatically. Failures follow a clear ladder: retry, send back to the right agent, change approach, park, tell the owner.
- **No flashing windows.** Every background process runs hidden.
- **Safe first start.** It ships paused, with no accounts connected and a default login you must change.
- **Author credit protection.** The author credit is kept in every file and in the dashboard, and is protected by a digital signature.

- **One click installer.** `INSTALL.bat` checks your computer, shows what you have and what is missing, installs the rest, finds your local text model, and ends with a readiness report.
- **`CONNECT.bat`.** Connect every social account with simple questions. It tests the details and saves them on your computer only.
- **More places to post.** Facebook Page, Threads, LinkedIn, WordPress and a Webhook that reaches Instagram, Pinterest, Reddit and others through Zapier, Make or n8n.
- **`DOCTOR.bat`.** A readiness report that says what is ready, what is missing, why, and how to fix it. It names the cause: not installed, not set up, your computer, internet, or a step only you can do.
- **`TROUBLESHOOTING.md`.** Common problems and their fixes.

### Fixed

- Posts published three times because of a missing check. Now there is a record before each publish and a read back before any retry.
- A topic being rejected by its own reservation.
- Old unpublished jobs being picked up again after a restart.
- A research feed being asked too often and blocking the whole run.
- A cleanup that could delete the drafts of a job that had not been published.
- A crash in the logger when a message contained an emoji.

### Known limits

- Characters can look slightly different from scene to scene in local video.
- Local video takes about 25 minutes for a short clip on an 8 GB graphics card.
- Some platforms need steps only the account owner can do, such as app approval for TikTok or a paid plan for X.
