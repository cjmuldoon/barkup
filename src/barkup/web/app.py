"""Flask web application for eddieisagoodboy.com."""

import collections
import hashlib
import logging
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import (Flask, abort, redirect, render_template, request,
                   send_file, session, url_for)

from barkup.assessment import generate_assessment
from barkup.config import settings

logger = logging.getLogger(__name__)

_db = None
_health_callback = None

# Ring buffer log handler — keeps last 500 log lines in memory
_log_buffer = collections.deque(maxlen=500)


class BufferLogHandler(logging.Handler):
    def emit(self, record):
        try:
            _log_buffer.append(self.format(record))
        except Exception:
            pass


def _install_log_handler():
    handler = BufferLogHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    logging.getLogger().addHandler(handler)


def get_db():
    return _db


def create_app(db=None):
    global _db
    if db:
        _db = db

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.secret_key = settings.flask_secret_key

    def login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("logged_in"):
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return decorated

    @app.context_processor
    def inject_now():
        return {"now": datetime.now(ZoneInfo(settings.timezone))}

    # --- Public routes ---

    @app.route("/")
    def index():
        db = get_db()
        summary = db.get_daily_summary()
        all_time = db.get_all_time_stats()
        weekly = db.get_weekly_daily_totals(weeks=2)
        assessment = generate_assessment(summary)

        tz = ZoneInfo(settings.timezone)
        current_hour = datetime.now(tz).hour
        hourly = summary.get("hourly_bark_minutes", {})
        bark_this_hour = hourly.get(current_hour, 0)

        # During monitoring hours: mood based on current hour's barking
        # After monitoring ends (20:30+): mood based on total day
        monitoring_ended = current_hour >= settings.monitor_end_hour
        if monitoring_ended:
            total_episodes = summary.get("total_episodes", 0)
            if total_episodes > 15:
                mood = "devil"
            elif total_episodes <= 5:
                mood = "angel"
            else:
                mood = "neutral"
        else:
            if bark_this_hour > 2:
                mood = "devil"
            elif bark_this_hour < 0.5:
                mood = "angel"
            else:
                mood = "neutral"

        return render_template(
            "public.html",
            summary=summary,
            all_time=all_time,
            weekly=weekly,
            assessment=assessment,
            mood=mood,
            bark_this_hour=round(bark_this_hour, 1),
        )

    @app.route("/api/status")
    def api_status():
        db = get_db()
        summary = db.get_daily_summary()
        all_time = db.get_all_time_stats()
        tz = ZoneInfo(settings.timezone)
        current_hour = datetime.now(tz).hour
        hourly = summary.get("hourly_bark_minutes", {})
        bark_this_hour = hourly.get(current_hour, 0)

        monitoring_ended = current_hour >= settings.monitor_end_hour
        if monitoring_ended:
            total_episodes = summary.get("total_episodes", 0)
            mood = "devil" if total_episodes > 15 else ("angel" if total_episodes <= 5 else "neutral")
        else:
            mood = "devil" if bark_this_hour > 2 else ("angel" if bark_this_hour < 0.5 else "neutral")

        return {
            "today_episodes": summary["total_episodes"],
            "today_bark_minutes": summary["total_bark_minutes"],
            "all_time_episodes": all_time["total_episodes"],
            "peak_hour": summary["peak_hour"],
            "bark_this_hour": round(bark_this_hour, 1),
            "mood": mood,
        }

    @app.route("/api/random-clip")
    def api_random_clip():
        db = get_db()
        clip_path = db.get_random_clip_path()
        if clip_path and Path(clip_path).exists():
            return send_file(clip_path, mimetype="audio/wav")
        abort(404)

    # --- Auth routes ---

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "")

            db = get_db()
            user = db.get_user(username)
            if user:
                pw_hash = hashlib.sha256(password.encode()).hexdigest()
                if pw_hash == user["password_hash"]:
                    session["logged_in"] = True
                    session["username"] = username
                    return redirect(url_for("dashboard"))

            return render_template("login.html", error="Invalid credentials")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    # --- Protected routes ---

    @app.route("/dashboard")
    @login_required
    def dashboard():
        db = get_db()
        tz = ZoneInfo(settings.timezone)
        date = request.args.get("date")
        if not date:
            date = datetime.now(tz).strftime("%Y-%m-%d")

        summary = db.get_daily_summary(date)
        all_time = db.get_all_time_stats()
        # Get episodes for the selected date (not just recent)
        episodes = summary.get("episodes", [])
        # Also get dismissed/unconfirmed for the full table
        next_day = (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        all_day = db.get_episodes_for_range(date, next_day)

        return render_template(
            "dashboard.html",
            summary=summary,
            all_time=all_time,
            episodes=all_day,
            selected_date=date,
        )

    @app.route("/admin/config")
    @login_required
    def admin_config():
        return render_template(
            "config.html",
            settings={
                "notion_enabled": settings.notion_enabled,
                "bark_confidence_threshold": settings.bark_confidence_threshold,
                "confidence_dismiss_below": settings.confidence_dismiss_below,
                "confidence_confirm_above": settings.confidence_confirm_above,
                "episode_cooldown_seconds": settings.episode_cooldown_seconds,
                "monitor_start": f"{settings.monitor_start_hour:02d}:{settings.monitor_start_minute:02d}",
                "monitor_end": f"{settings.monitor_end_hour:02d}:{settings.monitor_end_minute:02d}",
                "timezone": settings.timezone,
                "anthropic_api_key": "***" + settings.anthropic_api_key[-4:] if settings.anthropic_api_key else "Not set",
                "telegram_enabled": bool(settings.telegram_bot_token),
                "web_port": settings.web_port,
                "db_path": settings.db_path,
                "clip_storage_path": settings.clip_storage_path,
            },
        )

    @app.route("/clips/<path:filename>")
    @login_required
    def serve_clip(filename):
        """Serve clip files (audio/video/snapshots) from the clips directory."""
        clip_dir = Path(settings.clip_storage_path).resolve()
        file_path = (clip_dir / filename).resolve()
        # Prevent path traversal
        if not str(file_path).startswith(str(clip_dir)):
            abort(403)
        if not file_path.exists():
            abort(404)
        return send_file(file_path)

    @app.route("/admin/health")
    @login_required
    def admin_health():
        health = {}
        if _health_callback:
            try:
                health = _health_callback()
            except Exception:
                health = {"error": "Failed to gather health metrics"}
        return render_template("health.html", health=health)

    @app.route("/admin/logs")
    @login_required
    def admin_logs():
        lines = list(_log_buffer)
        # Optional filter
        level = request.args.get("level", "").upper()
        search = request.args.get("search", "")
        if level:
            lines = [l for l in lines if f"[{level}]" in l]
        if search:
            lines = [l for l in lines if search.lower() in l.lower()]
        return render_template("logs.html", lines=lines, level=level, search=search)

    @app.route("/api/health")
    @login_required
    def api_health():
        if _health_callback:
            health = _health_callback()
            # Convert measure_since to string for JSON
            if health.get("measure_since"):
                health["measure_since"] = health["measure_since"].isoformat()
            return health
        return {"error": "Health callback not available"}

    @app.route("/api/logs")
    @login_required
    def api_logs():
        n = request.args.get("n", 100, type=int)
        lines = list(_log_buffer)[-n:]
        return {"lines": lines}

    @app.route("/api/episodes")
    @login_required
    def api_episodes():
        db = get_db()
        start = request.args.get("start")
        end = request.args.get("end")
        if start:
            episodes = db.get_episodes_for_range(start, end)
        else:
            episodes = db.get_recent_episodes(50)

        return [
            {
                "id": e["id"],
                "title": e["title"],
                "start_time": e["start_time"].isoformat(),
                "duration_seconds": e["duration_seconds"],
                "bark_time_seconds": e["bark_time_seconds"],
                "bark_count": e["bark_count"],
                "confidence": e["confidence"],
                "bark_type": e["bark_type"],
                "reason": e["reason"],
                "source": e["source"],
            }
            for e in episodes
        ]

    return app


def start_web(db, health_callback=None, host="0.0.0.0", port=None):
    """Start the Flask web server in a thread-compatible way."""
    global _health_callback
    _health_callback = health_callback
    _install_log_handler()
    port = port or settings.web_port
    app = create_app(db)
    _ensure_admin_user(db)
    logger.info("Starting web server on %s:%d", host, port)
    app.run(host=host, port=port, use_reloader=False, threaded=True)


def _ensure_admin_user(db):
    """Create the admin user if it doesn't exist and a password is configured."""
    if not settings.web_password:
        logger.warning("WEB_PASSWORD not set — dashboard login disabled")
        return
    user = db.get_user(settings.web_username)
    if not user:
        pw_hash = hashlib.sha256(settings.web_password.encode()).hexdigest()
        db.create_user(settings.web_username, pw_hash)
        logger.info("Created admin user: %s", settings.web_username)
