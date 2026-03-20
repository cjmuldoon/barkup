"""LLM-powered behavior assessment for Eddie using Claude API."""

import logging
import time

import httpx

from barkup.config import settings

logger = logging.getLogger(__name__)

# Cache assessment for 5 minutes to avoid excessive API calls
_cache: dict = {"text": None, "timestamp": 0, "mood": None}
CACHE_TTL = 300


def generate_assessment(summary: dict, mood: str = "angel",
                        bark_this_hour: float = 0, bark_score: float = 0,
                        avg_bark_count: float = 0, period: str = "during") -> str:
    """Generate a fun behavior assessment using Claude API.

    Args:
        summary: Daily summary dict from BarkDatabase.get_daily_summary()
        mood: Current mood classification (angel/neutral/devil)
        bark_this_hour: Minutes of barking in the current hour
        bark_score: The weighted bark score (0-100, 50=average)
        avg_bark_count: 14-day average daily bark count
        period: "during", "after", or "before" monitoring

    Returns:
        A 1-3 sentence personality-driven assessment string.
    """
    now = time.time()
    if _cache["text"] and _cache["mood"] == mood and (now - _cache["timestamp"]) < CACHE_TTL:
        return _cache["text"]

    if not settings.anthropic_api_key:
        return _generate_fallback(summary, mood, bark_this_hour, bark_score, avg_bark_count, period)

    total = summary.get("total_episodes", 0)
    bark_min = summary.get("total_bark_minutes", 0)
    bark_count = summary.get("total_bark_count", 0)
    dismissed = summary.get("dismissed", 0)
    peak_hour = summary.get("peak_hour")
    hourly = summary.get("hourly_bark_minutes", {})

    peak_str = f"{peak_hour}:00" if peak_hour is not None else "N/A"

    if period == "during":
        time_context = f"""Currently in the monitoring window. Eddie's mood right now is '{mood}'.
In the current hour he has barked for {bark_this_hour:.1f} minutes.
His bark score for today so far is {bark_score:.0f}/100 (50 = average day, below 25 = good boy, above 65 = bad boy).
His 14-day average daily bark count is {avg_bark_count:.0f}.
Today's total bark count so far is {bark_count}."""
    elif period == "after":
        time_context = f"""Monitoring has ended for the day. This is the end-of-day summary.
Eddie's final mood for today is '{mood}'.
His bark score for today was {bark_score:.0f}/100 (50 = average day).
His 14-day average daily bark count is {avg_bark_count:.0f}.
Today's total bark count was {bark_count}."""
    else:
        time_context = f"""This is the morning after. The data below is from yesterday.
Eddie's mood for yesterday was '{mood}'.
His bark score was {bark_score:.0f}/100 (50 = average day).
His 14-day average daily bark count is {avg_bark_count:.0f}.
Yesterday's total bark count was {bark_count}."""

    prompt = f"""You are Eddie the dog's behavior analyst for eddieisagoodboy.com. Eddie is a Groodle whose barking is tracked by AI.

Write 1-2 sentences assessing Eddie's behavior that ALIGNS with his current mood classification.
- If mood is 'angel': emphasise why he's being good right now. If today's overall numbers are high, acknowledge the day has been busy but note he's settled down / been quiet recently.
- If mood is 'neutral': balanced take, neither praising nor dramatic.
- If mood is 'devil': emphasise the intensity — reference bark count or bark time being above average. Be gently dramatic.

Compare today's numbers against his average to give context (e.g. "well below his daily average of X" or "already double his usual").

{time_context}

Day stats:
- Confirmed bark episodes: {total}
- Total bark time: {bark_min:.1f} minutes
- Total bark count: {bark_count}
- Auto-dismissed (false alarms): {dismissed}
- Peak barking hour: {peak_str}

Be playful, warm, and personality-driven. Refer to Eddie by name. Keep it concise (1-2 sentences).
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
        _cache["mood"] = mood
        return text
    except Exception:
        logger.exception("Claude API call failed, using fallback")
        return _generate_fallback(summary, mood, bark_this_hour, bark_score, avg_bark_count, period)


def _generate_fallback(summary: dict, mood: str = "angel",
                       bark_this_hour: float = 0, bark_score: float = 0,
                       avg_bark_count: float = 0, period: str = "during") -> str:
    """Rule-based fallback when Claude API is unavailable."""
    total = summary.get("total_episodes", 0)
    bark_min = summary.get("total_bark_minutes", 0)
    bark_count = summary.get("total_bark_count", 0)
    avg_str = f"{avg_bark_count:.0f}" if avg_bark_count else "unknown"

    if mood == "angel":
        if total == 0:
            return "Eddie has been an absolute angel. Not a single bark — golden boy status achieved."
        if bark_this_hour < 0.5 and period == "during":
            return f"Eddie's been quiet this hour. Today he's logged {bark_count} barks across {total} episodes, well below his daily average of {avg_str} — earning his good boy status."
        return f"Eddie's logged {bark_count} barks today across {total} episodes, but that's below his daily average of {avg_str}. Good boy confirmed."
    elif mood == "devil":
        return f"Eddie's been on a barking spree — {bark_count} barks and {bark_min:.1f} minutes of barking across {total} episodes, well above his daily average of {avg_str}. Someone's taking guard duty very seriously today."
    else:
        return f"Eddie's having an average day with {bark_count} barks across {total} episodes — roughly in line with his daily average of {avg_str}. Keeping watch, but not overdoing it."
