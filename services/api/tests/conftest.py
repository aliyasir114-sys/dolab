import os
import tempfile

import pytest

# The PostgreSQL CI job sets DOLAB_DATABASE_URL to a migrated database and the dolab_app role.
# Otherwise point the app at a throwaway SQLite file before it is imported.
if not os.getenv("DOLAB_DATABASE_URL", "").startswith("postgresql"):
    _db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    _db_file.close()
    os.environ["DOLAB_DATABASE_URL"] = f"sqlite:///{_db_file.name}"
os.environ["DOLAB_ENV"] = "test"
os.environ["DOLAB_CORS_ORIGINS"] = "https://web.dolab.test"
os.environ["DOLAB_MEDIA_DIR"] = tempfile.mkdtemp(prefix="dolab-media-")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine, is_sqlite  # noqa: E402
from app.main import app  # noqa: E402
from app.mailer import mailer  # noqa: E402
from app.ratelimit import login_limiter, reset_limiter  # noqa: E402


@pytest.fixture
def client():
    if is_sqlite:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    else:
        # The API role may not drop, create or truncate tables: empty them row by row instead.
        with engine.begin() as conn:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(table.delete())
    login_limiter.reset()
    reset_limiter.reset()
    mailer.sent.clear()
    # A real address for the connection, so trusted-proxy rules can be tested.
    with TestClient(app, base_url="http://testserver/v1", client=("127.0.0.1", 50000)) as test_client:
        yield test_client


@pytest.fixture
def signup(client):
    """Register a user and return their auth headers."""

    def _signup(email: str = "user@example.com", password: str = "correct-horse") -> dict[str, str]:
        response = client.post("/auth/register", json={"email": email, "password": password})
        assert response.status_code == 201, response.text
        token = client.post("/auth/login", json={"email": email, "password": password}).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return _signup
