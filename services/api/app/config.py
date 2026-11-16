"""Runtime settings, read from environment variables. No secrets live in the code."""
import os
from dataclasses import dataclass

_DEV_SECRET = "dev-only-secret-not-for-production-use-0000"


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str
    jwt_secret: str
    token_minutes: int
    refresh_days: int
    reset_minutes: int
    media_dir: str
    max_upload_bytes: int
    media_url_seconds: int


def load_settings() -> Settings:
    environment = os.getenv("DOLAB_ENV", "development")
    secret = os.getenv("DOLAB_JWT_SECRET", "")
    if not secret:
        if environment == "production":
            raise RuntimeError("DOLAB_JWT_SECRET must be set in production")
        secret = _DEV_SECRET
    return Settings(
        environment=environment,
        database_url=os.getenv("DOLAB_DATABASE_URL", "sqlite:///./dolab.db"),
        jwt_secret=secret,
        token_minutes=int(os.getenv("DOLAB_TOKEN_MINUTES", "15")),
        refresh_days=int(os.getenv("DOLAB_REFRESH_DAYS", "30")),
        reset_minutes=int(os.getenv("DOLAB_RESET_MINUTES", "30")),
        media_dir=os.getenv("DOLAB_MEDIA_DIR", "./media"),
        max_upload_bytes=int(os.getenv("DOLAB_MAX_UPLOAD_BYTES", str(8 * 1024 * 1024))),
        media_url_seconds=int(os.getenv("DOLAB_MEDIA_URL_SECONDS", "600")),
    )


settings = load_settings()
