# 🐕 Barkup

**AI-powered dog bark tracking because apparently our dog is single-handedly terrorising an entire suburb.**

## The Backstory

One fine day, we received a 500-word *handwritten* letter from a neighbour — let's call her Karen — detailing how our dog had allegedly barked for **2 hours straight** and was "impacting at least 60 people." Sixty. Six-zero. Our single, medium-sized dog was apparently causing a noise disturbance of such biblical proportions that it warranted a passive-aggressive novella, meticulously penned in cursive, and lovingly concluded with:

> *"Your loving neighbour"*

Rather than engage in a handwriting war, we decided to do what any reasonable person would do: build an over-engineered AI surveillance system to prove Karen wrong with cold, hard data.

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

Runs on a $6/month DigitalOcean droplet. A small price to pay for vindication.

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

Spoiler alert: the dog does not bark for 2 hours straight. He never has. But now we have the data to prove it, timestamped, classified by AI, and logged in a database that would make a data engineer weep.

Your loving developer. ❤️

---

*Built with [Claude Code](https://claude.com/claude-code) in a single afternoon, fuelled entirely by spite and a deep commitment to the truth.*
