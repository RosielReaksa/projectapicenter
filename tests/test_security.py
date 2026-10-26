"""Secrets must never leak, in any response, log line or the database."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SECRET = "fake-provider-key-abcdef123456"


def test_provider_key_encrypted_at_rest(provider_id):
    from app.config import BASE_DIR

    raw = (BASE_DIR / "data" / "test.db").read_bytes()
    assert SECRET.encode() not in raw, "provider key stored in plaintext"


def test_gateway_key_not_stored_in_plaintext(admin_client, partner_id):
    from app.config import BASE_DIR

    created = admin_client.post(
        "/admin/keys", json={"name": "leak check", "partner_id": partner_id}
    ).json()
    raw = (BASE_DIR / "data" / "test.db").read_bytes()
    assert created["key"].encode() not in raw


def test_provider_key_not_in_any_response(gateway_url, admin_client, provider_id, gateway_key):
    chat = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_key}"},
        json={"model": "ok-model", "messages": [{"role": "user", "content": "hi"}]},
        timeout=60,
    )
    bodies = [
        admin_client.get("/admin/providers").text,
        admin_client.get("/admin/overview").text,
        admin_client.get("/admin/activity").text,
        admin_client.get("/admin/settings").text,
        admin_client.get("/admin/export").text,
        httpx.get(f"{gateway_url}/health").text,
        chat.text,
    ]
    for body in bodies:
        assert SECRET not in body


def test_provider_key_not_in_error_path(gateway_url, admin_client, provider_id, gateway_key):
    """A provider rejecting its key must not echo the key back."""
    admin_client.post(
        "/admin/models",
        json={"provider_id": provider_id, "model_name": "auth-model", "is_enabled": True},
    )
    response = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_key}"},
        json={"model": "auth-model", "messages": [{"role": "user", "content": "hi"}]},
        timeout=60,
    )
    assert SECRET not in response.text
    providers = admin_client.get("/admin/providers").json()["providers"]
    assert SECRET not in str(providers)


def test_gateway_key_only_logged_as_prefix(gateway_url, admin_client, provider_id, gateway_key):
    tag = f"keyprefix-{__import__('time').time_ns()}"
    httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_key}", "X-End-User": tag},
        json={"model": "ok-model", "messages": [{"role": "user", "content": "hi"}]},
        timeout=60,
    )
    # Filter to this exact request: the log is paginated and shared with other tests.
    import time

    deadline = time.time() + 5
    activity = {"requests": []}
    while time.time() < deadline and not activity["requests"]:
        activity = admin_client.get("/admin/activity", params={"end_user": tag}).json()
        if not activity["requests"]:
            time.sleep(0.1)

    assert activity["requests"], "request was not logged"
    body = str(activity)
    assert gateway_key not in body, "full gateway key leaked into activity"
    assert gateway_key[:12] in body, "key prefix should be visible for identification"


def test_audit_log_records_admin_actions(admin_client, partner_id):
    admin_client.post("/admin/keys", json={"name": "audited", "partner_id": partner_id})
    entries = admin_client.get("/admin/audit").json()["entries"]
    actions = {e["action"] for e in entries}
    assert "key.create" in actions


def test_export_omits_secrets(admin_client, provider_id):
    data = admin_client.get("/admin/export").json()
    assert SECRET not in str(data)
    providers = data["providers"]
    assert all("api_key" not in p for p in providers)
