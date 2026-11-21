from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import Base, engine, is_sqlite
from .routers import auth, dashboard, donations, items, photos


@asynccontextmanager
async def lifespan(app: FastAPI):
    # SQLite is for local development and the quick test run only: create its tables here.
    # PostgreSQL tables come from the migrations alone (alembic upgrade head, run by the deploy
    # identity before the server starts); the API role cannot create tables there at all.
    if is_sqlite:
        Base.metadata.create_all(engine)
    yield


app = FastAPI(title="DOLAB API", version="0.1.0", lifespan=lifespan)
if settings.cors_origins:
    # Exact origins only (no wildcard, refused in config). No cookies: the API uses bearer tokens.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        # Response headers the web app reads: the item count and how long to wait after a 429.
        expose_headers=["X-Total-Count", "Retry-After"],
        max_age=600,
    )
# All endpoints live under /v1 so the contract can change later without breaking old clients.
for module in (auth, items, photos, donations, dashboard):
    app.include_router(module.router, prefix="/v1")


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
