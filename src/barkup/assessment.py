"""LLM-powered behavior assessment for Eddie using Claude API."""

import logging
import time

import httpx

from barkup.config import settings

logger = logging.getLogger(__name__)

# Cache assessment for 5 minutes to avoid excessive API calls
_cache: dict = {"text": None, "timestamp": 0}
CACHE_TTL = 300


def generate_assessment(summary: dict) -> str:
    """Generate a fun behavior assessment using Claude API.

    Args:
        summary: Daily summary dict from BarkDatabase.get_daily_summary()

    Returns:
        A 1-3 sentence personality-driven assessment string.
    """
    # Check cache
    now = time.time()
    if _cache["text"] and (now - _cache["timestamp"]) < CACHE_TTL:
        return _cache["text"]

    if not settings.anthropic_api_key:
        return _generate_fallback(summary)

    total = summary.get("total_episodes", 0)
    bark_min = summary.get("total_bark_minutes", 0)
    dismissed = summary.get("dismissed", 0)
    peak_hour = summary.get("peak_hour")
    hourly = summary.get("hourly_bark_minutes", {})

    peak_str = f"{peak_hour}:00" if peak_hour is not None else "N/A"

    prompt = f"""You are Eddie the dog's behavior analyst. Eddie is a beloved pet whose barking is tracked by AI.
Based on today's data, write 1-2 sentences assessing Eddie's behavior. Be playful, warm, and personality-driven.
Refer to Eddie by name. Use dog-related puns or humor where natural. Keep it concise.

Today's stats:
- Confirmed bark episodes: {total}
- Total bark time: {bark_min:.1f} minutes
- Auto-dismissed (false alarms): {dismissed}
- Peak barking hour: {peak_str}
- Hourly breakdown: {dict(sorted(hourly.items()))}

If there are zero episodes, celebrate Eddie's good behavior.
If there are many episodes or lots of bark time, be gently dramatic about it.
Don't use hashtags or emojis. Just plain text."""

    try:
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 150,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=15,
        )
        resp.raise_for_status()
        text = resp.json()["content"][0]["text"].strip()
        _cache["text"] = text
        _cache["timestamp"] = now
        return text
    except Exception:
        logger.exception("Claude API call failed, using fallback")
        return _generate_fallback(summary)


def _generate_fallback(summary: dict) -> str:
    """Rule-based fallback when Claude API is unavailable."""
    total = summary.get("total_episodes", 0)
    bark_min = summary.get("total_bark_minutes", 0)

    if total == 0:
        return "Eddie has been an absolute angel today. Not a single bark — golden boy status achieved."
    elif total <= 2 and bark_min < 1:
        return "Eddie's been mostly chill today with just a couple of minor woofs. A very good boy overall."
    elif total <= 5:
        return f"Eddie's had a moderately vocal day with {total} bark episodes. Nothing out of the ordinary for our furry sentinel."
    elif bark_min > 5:
        return f"Eddie's been on high alert today — {total} episodes and {bark_min:.1f} minutes of barking. Someone's taking their guard dog duties very seriously."
    else:
        return f"Eddie logged {total} bark episodes today. He's got opinions and he's not afraid to share them."
