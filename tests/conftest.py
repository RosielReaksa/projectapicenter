"""Pytest fixtures: isolated DB, fake provider, real HTTP against the gateway."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Configure the app before anything imports app.config.
TEST_DB = ROOT / "data" / "test.db"
for suffix in ("", "-wal", "-shm"):
    candidate = Path(str(TEST_DB) + suffix)
    if candidate.exists():
        candidate.unlink()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB.as_posix()}"
os.environ["ADMIN_EMAIL"] = "owner@test.local"
os.environ["ADMIN_PASSWORD"] = "test-password-123"
os.environ["SEED_DEMO_DATA"] = "true"
os.environ["GLOBAL_MONTHLY_CAP_USD"] = "5"
os.environ["LOG_PROMPTS"] = "metadata"
os.environ["COOKIE_SECURE"] = "false"
os.environ["REDIS_URL"] = ""          # in-memory store for tests
os.environ["LOG_FORMAT"] = "text"

OWNER_EMAIL = "owner@test.local"
OWNER_PASSWORD = "test-password-123"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ServerThread:
    def __init__(self, app, port: int):
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self, timeout: float = 40.0) -> None:
        self.thread.start()
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.server.started:
                return
            time.sleep(0.05)
        raise RuntimeError("server did not start")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)


@pytest.fixture(scope="session")
def fake_provider_url():
    from tests.fake_provider import fake

    port = _free_port()
    server = ServerThread(fake, port)
    server.start()
    yield f"http://127.0.0.1:{port}/v1"
    server.stop()


@pytest.fixture(scope="session")
def gateway_url(fake_provider_url):
    from app.main import app

    port = _free_port()
    server = ServerThread(app, port)
    server.start()
    yield f"http://127.0.0.1:{port}"
    server.stop()


@pytest.fixture(scope="session")
def admin_client(gateway_url):
    """Signed-in control-plane client, with cookies and CSRF handled."""
    with httpx.Client(base_url=gateway_url, timeout=60.0) as client:
        response = client.post(
            "/admin/auth/login",
            json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD},
        )
        assert response.status_code == 200, response.text
        csrf = client.cookies.get("gw_csrf")
        assert csrf
        client.headers["X-CSRF-Token"] = csrf
        yield client


@pytest.fixture(scope="session")
def provider_id(admin_client, fake_provider_url):
    """A working fake provider pointed at the fake server."""
    response = admin_client.post(
        "/admin/providers",
        json={
            "name": "custom",
            "label": "Fake",
            "base_url": fake_provider_url,
            "api_key": "fake-provider-key-abcdef123456",
            "priority": 1,
            "is_free_tier": False,
            "seed_models": False,
        },
    )
    if response.status_code == 409:
        providers = admin_client.get("/admin/providers").json()["providers"]
        pid = next(p["id"] for p in providers if p["name"] == "custom")
        admin_client.patch(
            f"/admin/providers/{pid}",
            json={"api_key": "fake-provider-key-abcdef123456", "base_url": fake_provider_url},
        )
    else:
        assert response.status_code == 201, response.text
        pid = response.json()["id"]

    for model_name in (
        "ok-model",
        "fail-model",
        "ratelimit-model",
        "bad-model",
        "empty-model",
        "no-usage-model",
        "auth-model",
    ):
        admin_client.post(
            "/admin/models",
            json={
                "provider_id": pid,
                "model_name": model_name,
                "input_price_per_m": 1.0,
                "output_price_per_m": 2.0,
            },
        )
    return pid


@pytest.fixture(scope="session")
def partner_id(admin_client):
    partners = admin_client.get("/admin/partners").json()["partners"]
    if partners:
        return partners[0]["id"]
    created = admin_client.post("/admin/partners", json={"name": "Test Partner"}).json()
    return created["id"]


def make_partner(admin_client, name: str, **limits) -> dict:
    response = admin_client.post(
        "/admin/partners", json={"name": name, "allow_free_tier": True, **limits}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def gateway_key(admin_client, partner_id):
    response = admin_client.post("/admin/keys", json={"name": "test key", "partner_id": partner_id})
    assert response.status_code == 201, response.text
    return response.json()["key"]


@pytest.fixture()
def other_partner(admin_client):
    return make_partner(admin_client, f"Other {time.time_ns()}")
