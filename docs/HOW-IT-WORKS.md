# How AutoPost Automation works

## The idea in one paragraph

A team of agents makes posts and publishes them on a daily schedule. When nothing
is running, the team uses the free time to make posts ahead of time and keep them
ready. At each scheduled time the ready post is checked once more and published
within seconds. A watchdog watches everything so nothing stays stuck.

## What happens for one post

1. The clock says a slot is due, or the idle time manager decides there is free time.
2. If a finished post of that kind is waiting, jump to step 9.
3. NADIA picks the best topic from the topics ARJUN has ready. She checks the topic has never been used, and refuses real people stories and first person personal stories.
4. ARJUN attaches real source text and the verified facts. If there is no usable source, the post may make no factual claims.
5. MAYA writes the brief. SOPHIE writes the script from verified facts. DANIEL plans scenes and characters. LEO chooses the feeling and music.
6. OLIVER audits the package. ISABELLA edits across all the files and gives the go ahead.
7. NOAH makes the video, image or text. GRACE inspects it.
8. For a post made ahead of time, the job stops here and waits on the list.
9. ELLA publishes, one platform at a time, inside each platform's limits.
10. VICTORIA checks that each post is really live.
11. HELENA does the final audit, sends one summary message and records the topic in the history.

Rejections go up and down. A step that finds a problem sends the work back to the exact
agent that can fix it, with a reason and a retry limit.

## The three systems

### 1. Normal flow

The schedule. Eight posts a day. The schedule manager claims each slot once a day, so
a post can never go out twice.

### 2. Idle time flow

Every 10 minutes the system watchdog asks, in order:

1. Is automation on?
2. Is nothing running, the graphics card free and the text model up?
3. Which kind of post is below five on its waiting list? Video first, then image with text, then text.
4. Is there enough time before the next slot? Video needs 120 minutes, image 30, text 20.
5. Is a topic ready? If not, ARJUN looks for one first.

If every answer is yes, the full team makes the post. It stops after GRACE and joins the
waiting list. The normal schedule is never delayed.

### 3. Clean up flow

The clean up manager checks every hour. Seven days after a post is confirmed live it
deletes the work files and the final files. It writes every deletion to the log first.
It never touches posts that are not published, failed jobs waiting for retry, posts on
the waiting lists, the database, your settings or the program.

## Safety rules

- No invented facts. Only facts from a fetched source.
- No photo style video about a real named person.
- No first person stories as topics.
- No duplicates. A topic is checked against all used topics, the publish history and open jobs.
- Hard gates. The maker needs the editor's approval. The publisher needs the release decision.
- Only platforms on the allowed list are used.
- Deleting duplicate posts only removes extra copies of our own post, never the last one.

## When something fails

| Level | What happens |
|---|---|
| 0 | Try again after 30 seconds, 2 minutes, 10 minutes. Finished work is reused. |
| 1 | Send the work back to the exact agent with a precise reason. |
| 2 | Change approach: another model, another voice, another topic. |
| 3 | Park the job and carry on with the next slot. A stuck job never blocks the line. |
| 4 | Tell the owner: what happened, what was tried, what to do. |

| Situation | What the system does |
|---|---|
| A step crashes or hangs | The engine stops it, tries again within the budget, then reports it |
| Video takes long | Fine. It is judged by a heartbeat, not by the clock. Finished clips are reused on retry. |
| A platform rejects a post or is down | Only that platform fails. A retry skips what is already posted. |
| The text model is down or stuck | No work starts. The guard restarts it, but never during a render. |
| The dashboard or engine stops | The guard restarts them |
| Computer restarts | The guard task starts everything again |
| A waiting post is old, a duplicate or its file is missing | It is thrown away when taken, and a new one is made |
| Two engines try to take the same waiting post | A lock lets only one win |
| No topic is available | ARJUN looks for one immediately |

## Where things are

| Path | What |
|---|---|
| `scripts/department_queue.py` | The engine that runs jobs |
| `scripts/department.py` | The agents |
| `scripts/dispatcher.py` | The clock and the eight slots |
| `scripts/stock.py` | The waiting lists and the idle time rules |
| `scripts/supervisor.py` | The system watchdog |
| `scripts/retention.py` | The clean up manager |
| `scripts/publish.py` | The publishers |
| `scripts/dashboard.py` | The dashboard |
| `videogen/AGENT_SPEC.json` | Names, goals and limits of every agent |
| `config/rules.json` | The main settings |
| `config/connections.json` | Your accounts (private, never shared) |

Created by Mr Blank. See the [LICENSE](../LICENSE).
