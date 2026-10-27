"""Maintenance mode (MAINTENANCE_SPEC.md §14)."""

from __future__ import annotations

import time

import httpx
import pytest


def chat(url: str, key: str, **body):
    return httpx.post(
        f"{url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}], **body},
        timeout=60.0,
    )


def wait_for_status(url: str, key: str, expected: int, timeout: float = 10.0, **body) -> int:
    """Poll until the gateway reports `expected`, returning the last status.

    Maintenance state is cached in-process for up to 2 seconds, so a check made
    immediately after an admin write can still see the previous state. Polling
    makes the assertion about the eventual state, which is what the spec
    promises, instead of about how fast this machine propagated it.
    """
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        status = chat(url, key, **body).status_code
        if status == expected:
            return status
        time.sleep(0.1)
    return status


@pytest.fixture(autouse=True)
def clean_maintenance(admin_client):
    """Every test starts and ends with maintenance fully off."""
    admin_client.post("/admin/maintenance/global/stop", json={})
    admin_client.post("/admin/maintenance/degraded", json={"active": False})
    yield
    admin_client.post("/admin/maintenance/global/stop", json={})
    admin_client.post("/admin/maintenance/degraded", json={"active": False})
    for provider in admin_client.get("/admin/providers").json()["providers"]:
        if provider["paused"]:
            admin_client.post(f"/admin/providers/{provider['id']}/resume")
    for partner in admin_client.get("/admin/partners").json()["partners"]:
        if partner["suspended"]:
            admin_client.post(f"/admin/partners/{partner['id']}/resume")


# ------------------------------------------------------------- full maintenance
def test_maintenance_blocks_v1_with_structured_503(gateway_url, admin_client, provider_id, gateway_key):
    admin_client.post(
        "/admin/maintenance/global/start",
        json={"message": "Upgrading now.", "reason": "test", "allow_current_ip": False},
    )
    response = chat(gateway_url, gateway_key, model="ok-model")

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "maintenance"
    assert error["message"] == "Upgrading now."
    assert error["request_id"].startswith("req_")
    assert "retry-after" in {k.lower() for k in response.headers}
    assert response.headers["x-request-id"] == error["request_id"]


def test_maintenance_blocks_even_with_invalid_key(gateway_url, admin_client, provider_id):
    """Acceptance 1: the check runs before key lookup."""
    admin_client.post(
        "/admin/maintenance/global/start",
        json={"reason": "test", "allow_current_ip": False},
    )
    response = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": "Bearer sk-gw-totally-invalid"},
        json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        timeout=30,
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "maintenance"


def test_retry_after_uses_expected_end(gateway_url, admin_client, provider_id, gateway_key):
    from datetime import timedelta

    from app.utils import utcnow

    end = (utcnow() + timedelta(minutes=10)).replace(microsecond=0).isoformat() + "Z"
    admin_client.post(
        "/admin/maintenance/global/start",
        json={
            "message": "Back at 15:30.",
            "expected_end": end,
            "reason": "test",
            "allow_current_ip": False,
        },
    )
    response = chat(gateway_url, gateway_key, model="ok-model")
    assert response.status_code == 503
    assert "expected_end" in response.json()["error"]
    retry = int(response.headers["retry-after"])
    # Roughly ten minutes, and never below the 30s floor.
    assert 500 <= retry <= 660


def test_admin_health_and_public_stay_reachable(gateway_url, admin_client, provider_id):
    """Acceptance 2."""
    admin_client.post(
        "/admin/maintenance/global/start",
        json={"reason": "test", "allow_current_ip": False},
    )
    assert admin_client.get("/admin/keys").status_code == 200
    assert admin_client.get("/admin/maintenance").status_code == 200

    health = httpx.get(f"{gateway_url}/health", timeout=30)
    assert health.status_code == 200
    assert health.json()["status"] == "maintenance"

    assert httpx.get(f"{gateway_url}/public/status", timeout=30).json()["state"] == "maintenance"
    assert httpx.get(f"{gateway_url}/", timeout=30).status_code == 200


def test_readyz_drains_while_health_stays_up(gateway_url, admin_client, provider_id):
    admin_client.post(
        "/admin/maintenance/global/start",
        json={"reason": "test", "allow_current_ip": False},
    )
    assert httpx.get(f"{gateway_url}/health", timeout=30).status_code == 200
    assert httpx.get(f"{gateway_url}/readyz", timeout=30).status_code == 503


def test_allow_listed_ip_passes(gateway_url, admin_client, provider_id, gateway_key):
    """Acceptance 3: requests from an allowed IP still work."""
    # The test client connects from 127.0.0.1, so allow that explicitly.
    admin_client.post(
        "/admin/maintenance/global/start",
        json={"reason": "test", "allow_ips": ["127.0.0.1"], "allow_current_ip": False},
    )
    response = chat(gateway_url, gateway_key, model="ok-model")
    assert response.status_code == 200


def test_stop_restores_service(gateway_url, admin_client, provider_id, gateway_key):
    admin_client.post(
        "/admin/maintenance/global/start",
        json={"reason": "test", "allow_current_ip": False},
    )
    assert wait_for_status(gateway_url, gateway_key, 503, model="ok-model") == 503

    admin_client.post("/admin/maintenance/global/stop", json={"reason": "done"})
    assert wait_for_status(gateway_url, gateway_key, 200, model="ok-model") == 200


def test_start_requires_a_reason(admin_client, provider_id):
    response = admin_client.post("/admin/maintenance/global/start", json={"reason": ""})
    assert response.status_code == 400


# ------------------------------------------------------------ provider pause
def test_paused_provider_is_skipped(gateway_url, admin_client, provider_id, gateway_key):
    """Acceptance 7: traffic moves to other providers, no failures caused by the pause."""
    assert chat(gateway_url, gateway_key, model="ok-model").status_code == 200

    admin_client.post(f"/admin/providers/{provider_id}/pause", json={"reason": "testing"})
    try:
        # Either another provider answers, or none is available — never a hang.
        response = chat(gateway_url, gateway_key, model="ok-model")
        assert response.status_code in (200, 503)
        if response.status_code == 503:
            assert response.json()["error"]["code"] == "no_provider_available"
    finally:
        admin_client.post(f"/admin/providers/{provider_id}/resume")


def test_pausing_only_provider_yields_503_not_hang(gateway_url, admin_client, provider_id, gateway_key):
    """Acceptance 8."""
    admin_client.post(f"/admin/providers/{provider_id}/pause", json={"reason": "testing"})
    try:
        # The catalog is cached for up to 10s, so allow the pause to land.
        deadline = time.time() + 15
        response = chat(gateway_url, gateway_key, model="ok-model")
        while response.status_code != 503 and time.time() < deadline:
            time.sleep(0.2)
            response = chat(gateway_url, gateway_key, model="ok-model")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "no_provider_available"
    finally:
        admin_client.post(f"/admin/providers/{provider_id}/resume")


def test_paused_provider_reported_in_state(admin_client, provider_id):
    admin_client.post(f"/admin/providers/{provider_id}/pause", json={"reason": "testing"})
    state = admin_client.get("/admin/maintenance").json()
    match = next(p for p in state["providers"] if p["id"] == provider_id)
    assert match["paused"] is True
    assert match["paused_reason"] == "testing"


# ---------------------------------------------------------- partner suspension
def test_suspended_partner_gets_403_only_for_itself(gateway_url, admin_client, provider_id):
    """Acceptance 9."""
    a = admin_client.post(
        "/admin/partners", json={"name": f"SusA {time.time_ns()}", "allow_free_tier": True}
    ).json()
    b = admin_client.post(
        "/admin/partners", json={"name": f"SusB {time.time_ns()}", "allow_free_tier": True}
    ).json()
    key_a = admin_client.post("/admin/keys", json={"name": "a", "partner_id": a["id"]}).json()["key"]
    key_b = admin_client.post("/admin/keys", json={"name": "b", "partner_id": b["id"]}).json()["key"]

    assert chat(gateway_url, key_a, model="ok-model").status_code == 200

    admin_client.post(
        f"/admin/partners/{a['id']}/suspend",
        json={"reason_internal": "unpaid invoice 4412", "reason_public": "Please contact billing."},
    )
    try:
        blocked = chat(gateway_url, key_a, model="ok-model")
        assert blocked.status_code == 403
        assert blocked.json()["error"]["code"] == "partner_suspended"
        assert blocked.json()["error"]["message"] == "Please contact billing."
        # The internal reason must never leak.
        assert "unpaid invoice 4412" not in blocked.text

        # The other partner is unaffected.
        assert chat(gateway_url, key_b, model="ok-model").status_code == 200
    finally:
        admin_client.post(f"/admin/partners/{a['id']}/resume")


def test_suspension_without_public_reason_uses_generic_message(gateway_url, admin_client, provider_id):
    partner = admin_client.post(
        "/admin/partners", json={"name": f"SusC {time.time_ns()}", "allow_free_tier": True}
    ).json()
    key = admin_client.post(
        "/admin/keys", json={"name": "c", "partner_id": partner["id"]}
    ).json()["key"]
    admin_client.post(
        f"/admin/partners/{partner['id']}/suspend", json={"reason_internal": "internal only"}
    )
    try:
        response = chat(gateway_url, key, model="ok-model")
        assert response.status_code == 403
        assert "suspended" in response.json()["error"]["message"].lower()
        assert "internal only" not in response.text
    finally:
        admin_client.post(f"/admin/partners/{partner['id']}/resume")


def test_suspension_requires_internal_reason(admin_client, partner_id):
    response = admin_client.post(
        f"/admin/partners/{partner_id}/suspend", json={"reason_internal": ""}
    )
    assert response.status_code == 400


# -------------------------------------------------------------- degraded mode
def test_degraded_max_tokens_is_enforced(gateway_url, admin_client, provider_id, gateway_key):
    """Acceptance 10."""
    admin_client.post(
        "/admin/maintenance/degraded",
        json={"active": True, "max_tokens": 100, "reason": "high load"},
    )
    try:
        response = chat(gateway_url, gateway_key, model="ok-model", max_tokens=5000)
        if response.status_code != 429:
            time.sleep(2.5)  # degraded state is cached for up to 2s
            response = chat(gateway_url, gateway_key, model="ok-model", max_tokens=5000)
        assert response.status_code == 429
        error = response.json()["error"]
        assert error["code"] == "degraded_mode"
        assert "retry-after" in {k.lower() for k in response.headers}

        # A request inside the limit still works.
        assert chat(gateway_url, gateway_key, model="ok-model", max_tokens=50).status_code == 200
    finally:
        admin_client.post("/admin/maintenance/degraded", json={"active": False})


def test_degraded_state_is_reported(gateway_url, admin_client, provider_id):
    admin_client.post(
        "/admin/maintenance/degraded",
        json={"active": True, "paid_only": True, "reduce_concurrency_pct": 50},
    )
    try:
        state = admin_client.get("/admin/maintenance").json()["degraded"]
        assert state["active"] is True
        assert state["paid_only"] is True
        assert any("concurrency" in r for r in state["restrictions"])
        # Public status says degraded, without internal detail.
        public = httpx.get(f"{gateway_url}/public/status", timeout=30).json()
        assert public["state"] == "degraded"
    finally:
        admin_client.post("/admin/maintenance/degraded", json={"active": False})


# --------------------------------------------------------------- audit trail
def test_every_action_is_recorded(admin_client, provider_id):
    """Acceptance 12."""
    admin_client.post(
        "/admin/maintenance/global/start",
        json={"reason": "audit check", "allow_current_ip": False},
    )
    admin_client.post("/admin/maintenance/global/stop", json={"reason": "audit check done"})
    admin_client.post(f"/admin/providers/{provider_id}/pause", json={"reason": "audit"})
    admin_client.post(f"/admin/providers/{provider_id}/resume")

    history = admin_client.get("/admin/maintenance/history").json()["entries"]
    actions = {(h["level"], h["action"], h["source"]) for h in history}
    assert ("global", "on", "dashboard") in actions
    assert ("global", "off", "dashboard") in actions
    assert ("provider", "on", "dashboard") in actions

    audit = admin_client.get("/admin/audit").json()["entries"]
    assert any(e["action"] == "maintenance.global.on" for e in audit)


# ------------------------------------------------------------- scheduled windows
def test_window_create_edit_cancel(admin_client):
    from datetime import timedelta

    from app.utils import utcnow

    start = (utcnow() + timedelta(days=1)).replace(microsecond=0).isoformat() + "Z"
    end = (utcnow() + timedelta(days=1, hours=2)).replace(microsecond=0).isoformat() + "Z"

    created = admin_client.post(
        "/admin/maintenance/windows",
        json={
            "starts_at": start,
            "ends_at": end,
            "banner_text": "Planned upgrade",
            "auto_enable": True,
        },
    )
    assert created.status_code == 201, created.text
    window_id = created.json()["id"]

    windows = admin_client.get("/admin/maintenance/windows").json()["windows"]
    assert any(w["id"] == window_id for w in windows)

    patched = admin_client.patch(
        f"/admin/maintenance/windows/{window_id}", json={"banner_text": "Updated text"}
    )
    assert patched.status_code == 200

    assert admin_client.delete(f"/admin/maintenance/windows/{window_id}").status_code == 204
    after = admin_client.get("/admin/maintenance/windows").json()["windows"]
    match = next(w for w in after if w["id"] == window_id)
    assert match["status"] == "cancelled"


def test_window_rejects_end_before_start(admin_client):
    from datetime import timedelta

    from app.utils import utcnow

    start = (utcnow() + timedelta(days=1)).isoformat() + "Z"
    end = (utcnow() + timedelta(hours=1)).isoformat() + "Z"
    response = admin_client.post(
        "/admin/maintenance/windows",
        json={"starts_at": start, "ends_at": end, "banner_text": "bad"},
    )
    assert response.status_code == 400


def test_banner_shows_inside_lead_time(admin_client):
    """Acceptance 15: the public status carries the upcoming window."""
    from datetime import timedelta

    from app.utils import utcnow

    start = (utcnow() + timedelta(hours=2)).replace(microsecond=0)
    end = start + timedelta(hours=1)
    created = admin_client.post(
        "/admin/maintenance/windows",
        json={
            "starts_at": start.isoformat() + "Z",
            "ends_at": end.isoformat() + "Z",
            "banner_text": "Window soon",
            "auto_enable": False,
        },
    ).json()

    try:
        status = admin_client.get("/admin/maintenance").json()
        assert any(w["id"] == created["id"] for w in status["windows"])
    finally:
        admin_client.delete(f"/admin/maintenance/windows/{created['id']}")


# ------------------------------------------------------------------ owner only
def test_partner_cannot_touch_maintenance(gateway_url, admin_client, partner_login_client):
    assert partner_login_client.get("/admin/maintenance").status_code == 403
    assert (
        partner_login_client.post(
            "/admin/maintenance/global/start", json={"reason": "nope"}
        ).status_code
        == 403
    )


@pytest.fixture(scope="module")
def partner_login_client(admin_client, gateway_url):
    partner = admin_client.post(
        "/admin/partners", json={"name": f"Maint {time.time_ns()}", "allow_free_tier": True}
    ).json()
    email = f"maint{time.time_ns()}@example.com"
    admin_client.post(
        "/admin/members",
        json={
            "email": email,
            "password": "partner-pass-123",
            "role": "partner",
            "partner_id": partner["id"],
        },
    )
    client = httpx.Client(base_url=gateway_url, timeout=30.0)
    client.post("/admin/auth/login", json={"email": email, "password": "partner-pass-123"})
    client.headers["X-CSRF-Token"] = client.cookies.get("gw_csrf")
    yield client
    client.close()
