from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Google OAuth2
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str

    # Google SDM
    sdm_project_id: str
    pubsub_project_id: str
    pubsub_subscription_id: str = "barkup-events"
    camera_device_ids: str  # Comma-separated device IDs, or "all"

    # Notion
    notion_api_key: str
    notion_database_id: str

    # Bark detection tuning
    bark_confidence_threshold: float = 0.45
    episode_cooldown_seconds: int = 30
    confidence_dismiss_below: float = 0.75   # Auto-dismiss as Not Bark
    confidence_confirm_above: float = 0.85   # Auto-confirm as Bark

    # GCP Service Account (for Pub/Sub)
    google_application_credentials: str | None = None

    # Telegram
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_allowed_users: str | None = None  # Comma-separated user IDs
    summary_hour: int = 20  # 8pm local time
    summary_minute: int = 30
    timezone: str = "Australia/Adelaide"

    # Monitoring schedule (always-on RTSP)
    monitor_start_hour: int = 7
    monitor_start_minute: int = 30
    monitor_end_hour: int = 20
    monitor_end_minute: int = 30
    stream_reconnect_delay: int = 10  # seconds between reconnect attempts

    # Clip storage
    clip_storage_path: str = "./clips"
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None

    # SQLite database
    db_path: str = "./data/barkup.db"

    # Web app
    flask_secret_key: str = "change-me-in-production"
    web_port: int = 5000
    web_username: str = "admin"
    web_password: str | None = None  # Set to enable login

    # LLM assessment (Claude API)
    anthropic_api_key: str | None = None

    # Camera names (optional, comma-separated matching camera_device_ids order)
    # e.g. "Indoor,Backyard"
    camera_names: str | None = None

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def get_camera_ids(self) -> list[str] | None:
        """Return list of camera IDs, or None for all cameras."""
        if self.camera_device_ids.lower() == "all":
            return None
        return [cid.strip() for cid in self.camera_device_ids.split(",")]

    def get_camera_name(self, device_id: str) -> str:
        """Return friendly name for a camera, or a short ID."""
        ids = self.get_camera_ids()
        names = (
            [n.strip() for n in self.camera_names.split(",")]
            if self.camera_names
            else []
        )
        if ids and device_id in ids:
            idx = ids.index(device_id)
            if idx < len(names):
                return names[idx]
        # Fallback: last 8 chars of device ID
        return device_id.split("/")[-1][:8] if "/" in device_id else device_id[:8]


settings = Settings()
