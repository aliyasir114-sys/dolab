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
    cors_origins: tuple[str, ...]
    trusted_proxy_hops: int


def load_settings() -> Settings:
    environment = os.getenv("DOLAB_ENV", "development")
    secret = os.getenv("DOLAB_JWT_SECRET", "")
    if not secret:
        if environment == "production":
            raise RuntimeError("DOLAB_JWT_SECRET must be set in production")
        secret = _DEV_SECRET
    database_url = os.getenv("DOLAB_DATABASE_URL", "sqlite:///./dolab.db")
    if environment == "production" and not database_url.startswith("postgresql"):
        raise RuntimeError("DOLAB_DATABASE_URL must be PostgreSQL in production")
    return Settings(
        environment=environment,
        database_url=database_url,
        jwt_secret=secret,
        token_minutes=int(os.getenv("DOLAB_TOKEN_MINUTES", "15")),
        refresh_days=int(os.getenv("DOLAB_REFRESH_DAYS", "30")),
        reset_minutes=int(os.getenv("DOLAB_RESET_MINUTES", "30")),
        media_dir=os.getenv("DOLAB_MEDIA_DIR", "./media"),
        max_upload_bytes=int(os.getenv("DOLAB_MAX_UPLOAD_BYTES", str(8 * 1024 * 1024))),
        media_url_seconds=int(os.getenv("DOLAB_MEDIA_URL_SECONDS", "600")),
        cors_origins=_cors_origins(os.getenv("DOLAB_CORS_ORIGINS", "")),
        trusted_proxy_hops=int(os.getenv("DOLAB_TRUSTED_PROXY_HOPS", "0")),
    )


def _cors_origins(raw: str) -> tuple[str, ...]:
    """Exact origins allowed to call the API from a browser. A wildcard is refused."""
    origins = tuple(origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip())
    for origin in origins:
        if "*" in origin or not origin.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise RuntimeError(f"DOLAB_CORS_ORIGINS: not an allowed origin: {origin!r}")
    return origins


settings = load_settings()
