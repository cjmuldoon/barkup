"""Flask web application for eddieisagoodboy.com."""

import hashlib
import logging
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Flask, redirect, render_template, request, session, url_for

from barkup.assessment import generate_assessment
from barkup.config import settings

logger = logging.getLogger(__name__)

# Database instance — set by start_web() before app runs
_db = None


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
        assessment = generate_assessment(summary)

        # Determine Eddie's mood based on last hour's barking
        tz = ZoneInfo(settings.timezone)
        current_hour = datetime.now(tz).hour
        hourly = summary.get("hourly_bark_minutes", {})
        bark_this_hour = hourly.get(current_hour, 0)

        # Mood thresholds: >2 min barking in current hour = devil, <0.5 = angel
        if bark_this_hour > 2:
            mood = "devil"
        elif bark_this_hour < 0.5:
            mood = "angel"
        else:
            mood = "neutral"

        return render_template(
            "public.html",
            summary=summary,
            assessment=assessment,
            mood=mood,
            bark_this_hour=round(bark_this_hour, 1),
        )

    @app.route("/api/status")
    def api_status():
        db = get_db()
        summary = db.get_daily_summary()
        tz = ZoneInfo(settings.timezone)
        current_hour = datetime.now(tz).hour
        hourly = summary.get("hourly_bark_minutes", {})
        bark_this_hour = hourly.get(current_hour, 0)

        return {
            "total_episodes": summary["total_episodes"],
            "total_bark_minutes": summary["total_bark_minutes"],
            "peak_hour": summary["peak_hour"],
            "bark_this_hour": round(bark_this_hour, 1),
            "mood": "devil" if bark_this_hour > 2 else ("angel" if bark_this_hour < 0.5 else "neutral"),
        }

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
        date = request.args.get("date")
        if date:
            summary = db.get_daily_summary(date)
        else:
            summary = db.get_daily_summary()
            tz = ZoneInfo(settings.timezone)
            date = datetime.now(tz).strftime("%Y-%m-%d")

        recent = db.get_recent_episodes(50)

        return render_template(
            "dashboard.html",
            summary=summary,
            recent=recent,
            selected_date=date,
        )

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


def start_web(db, host="0.0.0.0", port=None):
    """Start the Flask web server in a thread-compatible way."""
    port = port or settings.web_port
    app = create_app(db)

    # Ensure default admin user exists
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
