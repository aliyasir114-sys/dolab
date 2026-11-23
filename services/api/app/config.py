"""Runtime settings, read from environment variables. No secrets live in the code."""
import ipaddress
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
    # Proxies whose X-Forwarded-For is believed (the load balancer, the web server). Empty: none.
    trusted_proxies: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]


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
        trusted_proxies=_trusted_proxies(os.getenv("DOLAB_TRUSTED_PROXIES", "")),
    )


def _trusted_proxies(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Addresses or CIDR ranges, comma-separated: "10.0.0.0/8, 192.0.2.7"."""
    if os.getenv("DOLAB_TRUSTED_PROXY_HOPS"):
        # Replaced: a hop count trusted the header whoever sent it. Refuse rather than ignore it.
        raise RuntimeError("DOLAB_TRUSTED_PROXY_HOPS is no longer used; list the proxies in DOLAB_TRUSTED_PROXIES")
    networks = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=True))
        except ValueError as error:
            raise RuntimeError(f"DOLAB_TRUSTED_PROXIES: not an address or range: {part!r}") from error
    return tuple(networks)


def _cors_origins(raw: str) -> tuple[str, ...]:
    """Exact origins allowed to call the API from a browser. A wildcard is refused."""
    origins = tuple(origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip())
    for origin in origins:
        if "*" in origin or not origin.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise RuntimeError(f"DOLAB_CORS_ORIGINS: not an allowed origin: {origin!r}")
    return origins


settings = load_settings()
