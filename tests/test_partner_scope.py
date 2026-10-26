"""A partner login must never see the owner's cost, margin, or upstream setup."""

from __future__ import annotations

import time

import httpx
import pytest


@pytest.fixture(scope="module")
def partner_login(admin_client, gateway_url):
    """A console login bound to a partner, with its own key and traffic."""
    partner = admin_client.post(
        "/admin/partners",
        json={"name": f"Scoped {time.time_ns()}", "monthly_cap_usd": 12.0, "allow_free_tier": True},
    ).json()
    email = f"partner{time.time_ns()}@example.com"
    created = admin_client.post(
        "/admin/members",
        json={
            "email": email,
            "password": "partner-pass-123",
            "role": "partner",
            "partner_id": partner["id"],
        },
    )
    assert created.status_code == 201, created.text

    key = admin_client.post(
        "/admin/keys", json={"name": "partner key", "partner_id": partner["id"]}
    ).json()

    client = httpx.Client(base_url=gateway_url, timeout=30.0)
    login = client.post(
        "/admin/auth/login", json={"email": email, "password": "partner-pass-123"}
    )
    assert login.status_code == 200, login.text
    client.headers["X-CSRF-Token"] = client.cookies.get("gw_csrf")

    yield {"client": client, "partner": partner, "key": key["key"], "email": email}
    client.close()


def _wait_activity(client, params, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get("/admin/activity", params=params).json()
        if data["total"] >= 1:
            return data
        time.sleep(0.1)
    return data


# ------------------------------------------------------- cost is hidden
def test_partner_usage_hides_cost_and_margin(gateway_url, partner_login, provider_id):
    tag = f"scoped-{time.time_ns()}"
    httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {partner_login['key']}", "X-End-User": tag},
        json={"model": "ok-model", "messages": [{"role": "user", "content": "hi"}]},
        timeout=60,
    )
    data = partner_login["client"].get("/admin/usage", params={"days": 30}).json()

    assert data["role"] == "partner"
    assert data["by_partner"], "partner should still see its own totals"
    for row in data["by_partner"]:
        assert row["price"] is None, "partner must not see the price breakdown"
        assert row["margin"] is None, "partner must not see our margin"
    assert all(row["price"] is None for row in data["by_key"])
    assert all(row["price"] is None for row in data["by_model"])


def test_partner_overview_hides_margin(gateway_url, partner_login, provider_id):
    data = partner_login["client"].get("/admin/overview").json()
    assert data["role"] == "partner"
    assert data["month"]["margin_usd"] is None
    assert data["month"]["price_usd"] is None


def test_partner_overview_uses_its_own_cap(gateway_url, partner_login):
    data = partner_login["client"].get("/admin/overview").json()
    # The partner's own cap (12.0), never the global kill switch (5.0 in tests).
    assert data["month"]["cap_usd"] == pytest.approx(12.0)


def test_partner_activity_hides_cost(gateway_url, partner_login, provider_id):
    tag = f"scoped-act-{time.time_ns()}"
    httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {partner_login['key']}", "X-End-User": tag},
        json={"model": "ok-model", "messages": [{"role": "user", "content": "hi"}]},
        timeout=60,
    )
    data = _wait_activity(partner_login["client"], {"end_user": tag})
    assert data["total"] >= 1
    row = data["requests"][0]
    assert row["price_usd"] is None, "partner must not see the price field"
    # cost_usd is repurposed to mean "what this partner was charged".
    assert row["cost_usd"] >= 0


def test_partner_cannot_read_provider_setup(gateway_url, partner_login):
    client = partner_login["client"]
    assert client.get("/admin/providers").status_code == 403
    assert client.get("/admin/models").status_code == 403
    assert client.get("/admin/routing").status_code == 403


def test_partner_lookup_has_no_providers(gateway_url, partner_login):
    data = partner_login["client"].get("/admin/lookup").json()
    assert data["providers"] == [], "partners must not see upstream providers"
    assert data["models"] == []
    assert all(k["id"] for k in data["keys"])
    # Only their own partner appears.
    assert [p["id"] for p in data["partners"]] == [partner_login["partner"]["id"]]


def test_partner_cannot_manage_other_partners(gateway_url, partner_login, admin_client):
    others = admin_client.get("/admin/partners").json()["partners"]
    other_id = next(p["id"] for p in others if p["id"] != partner_login["partner"]["id"])
    response = partner_login["client"].patch(f"/admin/partners/{other_id}", json={"name": "hijack"})
    assert response.status_code == 403


def test_partner_cannot_create_key_for_another_partner(gateway_url, partner_login, admin_client):
    others = admin_client.get("/admin/partners").json()["partners"]
    other_id = next(p["id"] for p in others if p["id"] != partner_login["partner"]["id"])
    response = partner_login["client"].post(
        "/admin/keys", json={"name": "sneaky", "partner_id": other_id}
    )
    assert response.status_code == 403


def test_partner_only_sees_own_keys(gateway_url, partner_login):
    keys = partner_login["client"].get("/admin/keys").json()["keys"]
    assert keys, "partner should see its own key"
    assert all(k["partner_id"] == partner_login["partner"]["id"] for k in keys)


def test_partner_only_sees_own_activity(gateway_url, partner_login, provider_id):
    data = partner_login["client"].get("/admin/activity", params={"limit": 100}).json()
    for row in data["requests"]:
        assert row["partner_id"] == partner_login["partner"]["id"]


def test_partner_cannot_read_settings_or_audit(gateway_url, partner_login):
    client = partner_login["client"]
    assert client.get("/admin/settings").status_code == 403
    assert client.get("/admin/audit").status_code == 403
    assert client.get("/admin/members").status_code == 403


def test_partner_export_is_forbidden(gateway_url, partner_login):
    assert partner_login["client"].get("/admin/export").status_code == 403


# ------------------------------------------------------ owner still sees it
def test_owner_still_sees_cost_and_margin(admin_client, provider_id, gateway_key, gateway_url):
    # Generate some traffic first so there is a row to inspect.
    httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_key}"},
        json={"model": "ok-model", "messages": [{"role": "user", "content": "hi"}]},
        timeout=60,
    )
    deadline = time.time() + 5
    data = {"by_partner": []}
    while time.time() < deadline and not data["by_partner"]:
        data = admin_client.get("/admin/usage", params={"days": 30}).json()
        time.sleep(0.1)

    assert data["role"] == "owner"
    assert data["by_partner"]
    row = data["by_partner"][0]
    assert row["margin"] is not None
    assert row["price"] is not None


def test_owner_still_sees_providers(admin_client, provider_id):
    assert admin_client.get("/admin/providers").status_code == 200
    assert admin_client.get("/admin/routing").status_code == 200
    assert admin_client.get("/admin/models").status_code == 200
