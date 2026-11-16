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
        token_minutes=int(os.getenv("DOLAB_TOKEN_MINUTES", "10080")),
    )


settings = load_settings()
