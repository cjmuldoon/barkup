# 🐕 Barkup

**AI-powered dog bark tracking because apparently our dog is single-handedly terrorising an entire suburb.**

## The Backstory

The accused is **Eddie Dean**, a medium-to-small Groodle and certified Good Boy. Eddie's rap sheet includes: barking at the postman (fair), saying g'day to doggy colleagues getting their walk (reasonable), and barking at **Shadow** — the blue-grey cat from two doors down who is, by any objective measure, the *actual* neighbourhood terrorist. Shadow's hobbies include sitting on top of everyone's roofs and tormenting every dog in a three-block radius. Nobody writes Shadow a letter.

One fine day, we received a 500-word *handwritten* letter from a neighbour — let's call her Karen — detailing how Eddie had allegedly barked for **2 hours straight** and was "impacting at least 60 people." Sixty. Six-zero. One Groodle. Sixty victims. We can only assume Karen was out at our gate for the full 2 hours with a clipboard and a stopwatch, which ironically would give Eddie something to bark at.

The letter was a masterclass in passive aggression, meticulously penned in cursive, factually creative, and lovingly concluded with:

> *"Your loving neighbour"*

My wife's first instinct was to slap a shock collar on Eddie Dean. No trial. No evidence review. No due process. Just straight to the electric chair based on the uncorroborated testimony of one anonymous cursive enthusiast. Eddie's own mother was ready to turn on him.

Rather than electrocute the family dog on Karen's say-so, I decided to do what any reasonable person would do: build an over-engineered AI surveillance system to prove Karen wrong with cold, hard data. Eddie deserves justice. Shadow deserves the letter. And my wife owes Eddie an apology.

## What It Does

Barkup monitors a Nest Cam 24/7, uses Google's YAMNet machine learning model to classify audio in real-time, and logs every single bark to a Notion database with forensic precision. Because if someone's going to accuse your dog of a 2-hour bark-a-thon, you'd better have receipts.

**Features:**
- 🎙️ **AI Bark Classification** — YAMNet distinguishes barks, howls, yips, whimpers, and growls. Science, Karen.
- ⏱️ **Duration Tracking** — Exact start time, end time, and duration of every episode. Down to the second. Shows actual bark time vs total episode span, so a "6 minute episode" that was really 15 seconds of barking doesn't look worse than it is.
- 📊 **Confidence Scoring** — How confident the AI is that it was actually a bark and not, say, 60 people gasping in horror.
- 🚫 **False Positive Filtering** — Multi-layered: suppresses when TV, speech, music, instruments (brass, woodwind — trumpet practice won't frame Eddie), or household impacts (bangs, clatters, doors) score higher than bark. Plus uses a sliding window requiring 2+ bark frames within 5 frames to confirm an episode — single bangs get discarded.
- 🎯 **Always-On Monitoring** — Continuous RTSP audio stream during configurable hours (default 7:30 AM – 8:30 PM). Zero detection latency — barks are caught within 1 second. Auto-reconnects on stream failure with exponential backoff, plus periodic full reconnects every 15 minutes to prevent RTSP relay stalls. Stall detection triggers immediate reconnect if no data for 15 seconds.
- 🔀 **Cross-Referencing** — YAMNet detections are cross-referenced with Nest Sound events. Each bark is tagged with its source: "YAMNet" (AI only), "Nest" (Google only), or "Both" (both agree). Build ground truth by replying to validate or dismiss.
- 🎬 **Audio & Video Clips** — Records audio and video during bark episodes only (not continuously). Reply `clip`, `video`, or `snapshot` to any Telegram notification to get the files sent straight to you.
- 📱 **Telegram Notifications** — Real-time alerts when barking is detected. React with 👍 to confirm bark or 👎 to dismiss, or reply naturally to log context. Any unrecognised reply is saved as a comment on the Notion page.
- 🏠 **Home Status Tracking** — Send `home` in Telegram to mark yourself as home — all subsequent bark episodes are auto-tagged "Owner Home" in Notion until you send `not home`. Works as a general message or reply to a notification.
- 📋 **Notion Database** — A beautiful, searchable log of every bark episode with all the metadata you could dream of.
- 📝 **Hierarchical Summaries** — Nightly report at 8:30pm with confirmed/not-bark/unconfirmed breakdown. On-demand: `summary week` gives daily breakdown, `summary month` gives weekly breakdown, `summary 2026` gives monthly breakdown. Stats (bark count, bark time, longest episode) are calculated from confirmed barks only.
- 🔧 **Health Check** — Nightly report after the daily summary with processing rate (% real-time), disk usage, and clip directory stats. Also available on-demand: send `health` or `status` in Telegram anytime. Health timer starts on first audio frame to exclude startup overhead from the percentage.
- 🗑️ **Auto Clip Cleanup** — Videos auto-deleted after 7 days, audio and snapshots after 21 days. No manual housekeeping required.
- 🔗 **Nest Cam Links** — Jump straight to the camera footage for any event.
- 📷 **Multi-Camera Support** — Monitor multiple Nest Cams with friendly names. Each bark is tagged to the camera that heard it.

## Architecture

```
Always-On RTSP Stream (7:30am–8:30pm) → ffmpeg Audio → YAMNet Classification
    → Episode Tracking → Notion + Telegram

Nest Cam → Google Pub/Sub → Snapshots + Cross-Referencing
    → Source tagging: YAMNet / Nest / Both
```

## Tech Stack

- **Python 3.12** with Docker
- **Google Smart Device Management API** for Nest Cam events
- **Google Cloud Pub/Sub** for real-time event delivery
- **YAMNet (TFLite)** for audio classification — runs on CPU, no GPU needed
- **Notion API** for the tracking database
- **Telegram Bot API** for notifications and interaction

## Deployment

We run ours on a Hetzner CX22 (2 shared vCPU, 4GB RAM) for ~€3.79/month — cheaper than a coffee. YAMNet inference takes ~1 second per frame on modest hardware, so any box with 2+ cores will handle real-time processing comfortably. A small price to pay for vindication. You've got plenty of options:

- **Cloud VPS** — Hetzner CX22 (~€3.79/mo for 2 vCPU/4GB — best value), DigitalOcean ($18/mo for 2 vCPU/1GB), or any cheap VPS with Docker and 2+ cores
- **Home PC / Mac** — if you've got something always on, just `docker compose up -d`
- **Raspberry Pi 4/5** — more than enough power, perfect for a dedicated bark sentinel
- **Old laptop in a cupboard** — finally a use for that 2015 MacBook Air
- **NAS** — Synology/QNAP with Docker support works great

Basically anything that can run Docker and has an internet connection. Eddie's legal defence doesn't require enterprise infrastructure.

```bash
# Clone
git clone https://github.com/cjmuldoon/barkup.git
cd barkup

# Configure (copy and fill in your credentials)
cp .env.example .env

# Deploy
bash scripts/deploy.sh
```

## Setup Requirements

- Google Device Access registration ($5 one-time)
- Google Cloud project with Pub/Sub and SDM API enabled
- Nest Cam with the camera linked to your Google account
- Notion workspace with an integration token
- Telegram bot (via @BotFather)
- A neighbour with a flair for creative writing

## Telegram Commands

**Reactions on any bark notification:**
- 👍 — confirms as Bark in Notion
- 👎 — marks as Not Bark in Notion

**Reply to any bark notification naturally:**
- `not bark` / `false positive` / `false alarm` — marks as Not Bark
- `was bark` / `confirmed` / `barked` / `genuine` — confirms as Bark (validates Nest-only detections)
- `clip` / `audio` / `sound` — sends the audio clip for that episode
- `video` / `footage` — sends the video clip for that episode
- `snapshot` / `photo` — sends the snapshot image
- `home` / `away` / `not home` — logs whether you were home + toggles persistent home status
- `I was home and intervened, it was the mailman` — all parsed automatically
- `delivery` / `stranger` / `cat` / `boredom` / `anxiety` / `doorbell` — auto-detected reasons
- Any other reply — saved as a comment on the Notion page

**On-demand commands (send as a new message, not a reply):**
- `home` / `I'm home` — marks you as home; all future episodes auto-tagged "Owner Home" until you say otherwise
- `not home` / `leaving` — clears the home flag
- `summary` — today's episodes (flat list)
- `summary yesterday` / `summary March 5` — specific date (flat list)
- `summary week` / `summary weekly` / `summary last week` — daily breakdown
- `summary month` / `summary monthly` / `summary March` — weekly breakdown
- `summary year` / `summary yearly` / `summary 2026` — monthly breakdown
- `summary last month` / `summary last year` — previous period
- `health` / `status` — system health check (processing rate, disk, clips)

## The Verdict

Spoiler alert: Eddie does not bark for 2 hours straight. He never has. But now we have the data to prove it, timestamped, classified by AI, and logged in a database. And if it turns out he *does* bark for an extended period, we'll know exactly when, why, and that Shadow was almost certainly on a roof somewhere nearby looking smug.

Your loving developer. ❤️

---

*Built with [Claude Code](https://claude.com/claude-code) in a single afternoon, fuelled entirely by spite and a deep commitment to the truth.*
