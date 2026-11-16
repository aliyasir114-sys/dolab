from contextlib import asynccontextmanager

from fastapi import FastAPI

from .db import Base, engine
from .routers import auth, dashboard, donations, items, photos


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Development convenience. Production schema changes go through migrations (not set up yet).
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="DOLAB API", version="0.1.0", lifespan=lifespan)
# All endpoints live under /v1 so the contract can change later without breaking old clients.
for module in (auth, items, photos, donations, dashboard):
    app.include_router(module.router, prefix="/v1")


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
