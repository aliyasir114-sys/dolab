from contextlib import asynccontextmanager

from fastapi import FastAPI

from .db import Base, engine
from .routers import auth, dashboard, donations, items


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Development convenience. Production schema changes go through migrations (not set up yet).
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="DOLAB API", version="0.1.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(items.router)
app.include_router(donations.router)
app.include_router(dashboard.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
