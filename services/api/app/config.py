"""Runtime settings, read from environment variables. No secrets live in the code."""
import ipaddress
import os
import tempfile
from dataclasses import dataclass
from urllib.parse import urlparse

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
    # Where ZIP exports are built before they are sent (plan 4.3). Never uploaded anywhere.
    export_tmp_dir: str
    # ZIP export limits (plan 4.3), checked against the export's own snapshot.
    export_max_bytes: int  # all photos together
    export_max_files: int  # photos
    export_max_data_bytes: int  # data.json
    export_per_hour: int  # per user, shared by every server instance
    export_concurrency: int  # at once, per server instance
    export_build_seconds: float
    # Photo storage: "local" (DOLAB_MEDIA_DIR; development) or "s3" (plan 4.6).
    storage: str
    s3_bucket: str
    s3_endpoint_url: str | None
    s3_region: str
    # Server-side encryption asked of the provider: "AES256", "aws:kms", or None (CI's MinIO only).
    s3_encryption: str | None
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
        export_tmp_dir=os.getenv("DOLAB_EXPORT_TMP_DIR", os.path.join(tempfile.gettempdir(), "dolab-exports")),
        export_max_bytes=_positive_int("DOLAB_EXPORT_MAX_BYTES", 500 * 1024 * 1024),
        export_max_files=_positive_int("DOLAB_EXPORT_MAX_FILES", 3000),
        export_max_data_bytes=_positive_int("DOLAB_EXPORT_MAX_DATA_BYTES", 8 * 1024 * 1024),
        export_per_hour=_positive_int("DOLAB_EXPORT_PER_HOUR", 3),
        export_concurrency=_positive_int("DOLAB_EXPORT_CONCURRENCY", 2),
        export_build_seconds=float(_positive_int("DOLAB_EXPORT_BUILD_SECONDS", 120)),
        **_storage_settings(environment),
        max_upload_bytes=int(os.getenv("DOLAB_MAX_UPLOAD_BYTES", str(8 * 1024 * 1024))),
        media_url_seconds=int(os.getenv("DOLAB_MEDIA_URL_SECONDS", "600")),
        cors_origins=_cors_origins(os.getenv("DOLAB_CORS_ORIGINS", "")),
        trusted_proxies=_trusted_proxies(os.getenv("DOLAB_TRUSTED_PROXIES", "")),
    )


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a whole number") from error
    if value < 1:
        raise RuntimeError(f"{name} must be at least 1")
    return value


def _storage_settings(environment: str) -> dict:
    backend = os.getenv("DOLAB_STORAGE", "local")
    if backend not in ("local", "s3"):
        raise RuntimeError("DOLAB_STORAGE must be 'local' or 's3'")
    if environment == "production" and backend != "s3":
        # Several server instances do not share a disk, and a container's disk does not last.
        raise RuntimeError("DOLAB_STORAGE must be 's3' in production")
    bucket = os.getenv("DOLAB_S3_BUCKET", "")
    if backend == "s3" and not bucket:
        raise RuntimeError("DOLAB_S3_BUCKET must be set when DOLAB_STORAGE is 's3'")
    encryption = os.getenv("DOLAB_S3_ENCRYPTION", "AES256")
    if encryption not in ("AES256", "aws:kms", "none"):
        raise RuntimeError("DOLAB_S3_ENCRYPTION must be 'AES256', 'aws:kms' or 'none'")
    if environment == "production" and encryption == "none":
        raise RuntimeError("DOLAB_S3_ENCRYPTION cannot be 'none' in production")
    endpoint_url = os.getenv("DOLAB_S3_ENDPOINT_URL") or None
    if environment == "production" and endpoint_url:
        parsed = urlparse(endpoint_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise RuntimeError("DOLAB_S3_ENDPOINT_URL must use https:// in production")
    return {
        "storage": backend,
        "s3_bucket": bucket,
        "s3_endpoint_url": endpoint_url,
        "s3_region": os.getenv("DOLAB_S3_REGION", "us-east-1"),
        "s3_encryption": None if encryption == "none" else encryption,
    }


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
