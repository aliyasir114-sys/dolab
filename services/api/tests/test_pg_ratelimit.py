"""Step C-1 on PostgreSQL: the sign-in limit is shared by parallel requests and by separate
server processes (plan 4.7, acceptance of step C: "a test with two server instances proves the
limit is shared")."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from app import ratelimit as rl
from app.db import engine
from tests.test_pg_concurrency import parallel

pytestmark = pytest.mark.skipif(engine.dialect.name != "postgresql", reason="needs PostgreSQL")

API_DIR = Path(__file__).resolve().parents[1]
EMAIL = "shared@example.com"
PASSWORD = "correct-horse"


def test_parallel_guesses_cannot_pass_the_limit_together(client):
    # Twelve wrong guesses at once from one address: whatever the timing, at most five go on.
    results = parallel(12, lambda _i: rl.LoginLimiter().start(EMAIL, "198.51.100.1").wait)
    allowed = [wait for wait in results if wait is None]
    assert 1 <= len(allowed) <= 5, results
    # And afterwards the address is blocked.
    assert rl.login_limiter.start(EMAIL, "198.51.100.1").wait is not None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """The API in its own uvicorn process, on the same database as the tests."""

    def __init__(self) -> None:
        self.port = _free_port()
        env = {**os.environ, "DOLAB_ENV": "test", "DOLAB_MEDIA_DIR": tempfile.mkdtemp(prefix="dolab-media-")}
        env.pop("DOLAB_TRUSTED_PROXIES", None)
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            cwd=API_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self.url = f"http://127.0.0.1:{self.port}"
        deadline = time.monotonic() + 30
        while True:
            try:
                if httpx.get(f"{self.url}/health", timeout=1).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            if self.process.poll() is not None or time.monotonic() > deadline:
                output = self.process.stdout.read().decode() if self.process.stdout else ""
                self.stop()
                raise RuntimeError(f"server did not start:\n{output}")
            time.sleep(0.2)

    def login(self, password: str) -> httpx.Response:
        return httpx.post(f"{self.url}/v1/auth/login", json={"email": EMAIL, "password": password}, timeout=10)

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


def test_two_server_processes_share_the_limit(client):
    first, second = Server(), Server()
    try:
        registered = httpx.post(f"{first.url}/v1/auth/register", json={"email": EMAIL, "password": PASSWORD}, timeout=10)
        assert registered.status_code == 201, registered.text
        # Five wrong passwords, spread over both processes. In memory each would have seen 2 or 3.
        for n in range(5):
            server = first if n % 2 == 0 else second
            assert server.login("wrong-password").status_code == 401
        for server in (first, second):
            refused = server.login(PASSWORD)
            assert refused.status_code == 429
            assert 0 < int(refused.headers["Retry-After"]) <= 900

        # A restart does not forget it either.
        first.stop()
        first = Server()
        assert first.login(PASSWORD).status_code == 429
    finally:
        first.stop()
        second.stop()
