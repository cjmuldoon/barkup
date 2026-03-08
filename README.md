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
- ⏱️ **Duration Tracking** — Exact start time, end time, and duration of every episode. Down to the second.
- 📊 **Confidence Scoring** — How confident the AI is that it was actually a bark and not, say, 60 people gasping in horror.
- 📱 **Telegram Notifications** — Real-time alerts when barking is detected. Reply naturally to log if you were home, intervened, or note the reason.
- 📋 **Notion Database** — A beautiful, searchable log of every bark episode with all the metadata you could dream of.
- 📝 **Nightly Summary** — Daily report at 8pm with total episodes, duration, and stats. Evidence, served fresh.
- 🔗 **Nest Cam Links** — Jump straight to the camera footage for any event.

## Architecture

```
Nest Cam → Google Pub/Sub → Start RTSP Stream → ffmpeg Audio Extraction
    → YAMNet Classification → Episode Tracking → Notion + Telegram
```

## Tech Stack

- **Python 3.12** with Docker
- **Google Smart Device Management API** for Nest Cam events
- **Google Cloud Pub/Sub** for real-time event delivery
- **YAMNet (TFLite)** for audio classification — runs on CPU, no GPU needed
- **Notion API** for the tracking database
- **Telegram Bot API** for notifications and interaction

## Deployment

We run ours on a $6/month DigitalOcean droplet for convenience. A small price to pay for vindication. But since YAMNet runs happily on CPU and the whole thing idles 99% of the time, you've got plenty of options:

- **Cloud VPS** — DigitalOcean ($6/mo), Hetzner ($3.80/mo), or any cheap VPS with Docker
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

Reply to any bark notification naturally:
- `home` — you were home
- `I was home and intervened, it was the mailman` — all parsed automatically
- `doorbell` / `stranger` / `cat` / `boredom` — auto-detected reasons

## The Verdict

Spoiler alert: Eddie does not bark for 2 hours straight. He never has. But now we have the data to prove it, timestamped, classified by AI, and logged in a database. And if it turns out he *does* bark for an extended period, we'll know exactly when, why, and that Shadow was almost certainly on a roof somewhere nearby looking smug.

Your loving developer. ❤️

---

*Built with [Claude Code](https://claude.com/claude-code) in a single afternoon, fuelled entirely by spite and a deep commitment to the truth.*
