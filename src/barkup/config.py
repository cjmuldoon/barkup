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
    camera_device_id: str

    # Notion
    notion_api_key: str
    notion_database_id: str

    # Bark detection tuning
    bark_confidence_threshold: float = 0.3
    episode_cooldown_seconds: int = 30

    # GCP Service Account (for Pub/Sub)
    google_application_credentials: str | None = None

    # Telegram
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_allowed_users: str | None = None  # Comma-separated user IDs
    summary_hour: int = 20  # 8pm local time
    timezone: str = "Australia/Adelaide"

    # Clip storage
    clip_storage_path: str = "./clips"
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
