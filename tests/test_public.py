"""Public site: no secrets, no account creation, abuse protection (HOMEPAGE_SPEC §10)."""

from __future__ import annotations

import time

import httpx
import pytest


@pytest.fixture(autouse=True)
def reset_access_rate_limit():
    """Each test starts with a clean per-IP submission budget.

    The limiter is deliberately aggressive (5/hour), so without this the later
    tests in this module would be throttled by the earlier ones.
    """
    from app.core.store import get_store

    store = get_store()
    if hasattr(store, "_windows"):
        store._windows.clear()
    yield
    if hasattr(store, "_windows"):
        store._windows.clear()


def post_request(url: str, **overrides):
    body = {
        "name": "Dara Sok",
        "email": f"dara{time.time_ns()}@example.com",
        "company": "Acme Sites",
        "use_case": "Transcript summarising.",
        "expected_usage": "1k to 10k",
        "accepted_terms": True,
    }
    body.update(overrides)
    return httpx.post(f"{url}/public/access-requests", json=body, timeout=30.0)


# ------------------------------------------------------------- public data
def test_public_config_exposes_base_url(gateway_url):
    data = httpx.get(f"{gateway_url}/public/config").json()
    assert data["app_name"]
    assert data["api_base_url"].endswith("/v1")
    assert data["support_email"]
    assert data["public_pricing"] is False, "pricing is off by default"


def test_public_models_hides_provider_and_ids(gateway_url, provider_id):
    data = httpx.get(f"{gateway_url}/public/models").json()
    assert data["models"], "seeded models should be listed"
    for model in data["models"]:
        assert "provider" not in model
        assert "provider_id" not in model
        assert "input_price_per_m" not in model, "pricing is off by default"
        assert set(model) <= {
            "id", "name", "context_length", "supports_vision", "supports_tools", "free"
        }


def test_public_models_has_no_secrets(gateway_url, provider_id):
    body = httpx.get(f"{gateway_url}/public/models").text
    assert "sk-gw-" not in body
    assert "fake-provider-key" not in body


def test_robots_blocks_console(gateway_url):
    body = httpx.get(f"{gateway_url}/robots.txt").text
    assert "Disallow: /console" in body
    assert "Disallow: /admin" in body
    assert "Sitemap:" in body


def test_sitemap_lists_public_pages_only(gateway_url):
    body = httpx.get(f"{gateway_url}/sitemap.xml").text
    assert "/models" in body and "/request-access" in body
    assert "/console" not in body
    assert "/login" not in body


def test_security_headers_present(gateway_url):
    headers = httpx.get(f"{gateway_url}/").headers
    assert "Content-Security-Policy" in headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"


# ---------------------------------------------------------- access requests
def test_valid_request_is_stored_as_pending(gateway_url, admin_client):
    email = f"valid{time.time_ns()}@example.com"
    response = post_request(gateway_url, email=email)
    assert response.status_code == 201
    assert "review" in response.json()["message"]

    rows = admin_client.get("/admin/access-requests").json()["requests"]
    match = next(r for r in rows if r["email"] == email)
    assert match["status"] == "pending"


def test_request_creates_no_user_partner_or_key(gateway_url, admin_client):
    before = (
        len(admin_client.get("/admin/members").json()["members"]),
        len(admin_client.get("/admin/partners").json()["partners"]),
        len(admin_client.get("/admin/keys").json()["keys"]),
    )
    email = f"noaccount{time.time_ns()}@example.com"
    assert post_request(gateway_url, email=email).status_code == 201

    after = (
        len(admin_client.get("/admin/members").json()["members"]),
        len(admin_client.get("/admin/partners").json()["partners"]),
        len(admin_client.get("/admin/keys").json()["keys"]),
    )
    assert before == after, "submitting the form must never create an account"


def test_terms_must_be_accepted(gateway_url):
    response = post_request(gateway_url, accepted_terms=False)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "terms_required"


def test_invalid_email_is_rejected(gateway_url):
    assert post_request(gateway_url, email="not-an-email").status_code == 400


def test_missing_field_is_rejected(gateway_url):
    assert post_request(gateway_url, name="").status_code == 400


def test_use_case_length_is_capped(gateway_url):
    assert post_request(gateway_url, use_case="x" * 1001).status_code == 400


def test_honeypot_stores_nothing(gateway_url, admin_client):
    email = f"bot{time.time_ns()}@example.com"
    response = post_request(gateway_url, email=email, website_url="http://spam.example")
    # A bot must not learn that it was caught.
    assert response.status_code == 201
    assert response.json()["ok"] is True

    rows = admin_client.get("/admin/access-requests").json()["requests"]
    assert not any(r["email"] == email for r in rows), "honeypot submission was stored"


def test_same_email_dedupes_within_a_day(gateway_url, admin_client):
    email = f"dupe{time.time_ns()}@example.com"
    assert post_request(gateway_url, email=email, company="First").status_code == 201
    assert post_request(gateway_url, email=email, company="Second").status_code == 201

    rows = admin_client.get("/admin/access-requests").json()["requests"]
    matches = [r for r in rows if r["email"] == email]
    assert len(matches) == 1, "repeated submissions must update one record"
    assert matches[0]["company"] == "Second"


def test_rate_limit_blocks_repeated_submissions(gateway_url):
    statuses = [
        post_request(gateway_url, email=f"burst{i}{time.time_ns()}@example.com").status_code
        for i in range(8)
    ]
    assert 429 in statuses, "per-IP rate limit should trigger"
    assert statuses.count(429) >= 2


# ------------------------------------------------------------------- admin
def test_approve_returns_prefill_and_audits(gateway_url, admin_client):
    email = f"approve{time.time_ns()}@example.com"
    post_request(gateway_url, email=email, company="Approve Co")
    rows = admin_client.get("/admin/access-requests?status=pending").json()["requests"]
    row = next(r for r in rows if r["email"] == email)

    result = admin_client.post(f"/admin/access-requests/{row['id']}/approve").json()
    assert result["request"]["status"] == "approved"
    assert result["prefill"]["name"] == "Approve Co"
    assert result["prefill"]["login_email"] == email

    audit = admin_client.get("/admin/audit").json()["entries"]
    assert any(
        e["action"] == "access_request.approve" and e["target"] == email for e in audit
    )


def test_reject_records_note_and_audits(gateway_url, admin_client):
    email = f"reject{time.time_ns()}@example.com"
    post_request(gateway_url, email=email)
    rows = admin_client.get("/admin/access-requests?status=pending").json()["requests"]
    row = next(r for r in rows if r["email"] == email)

    result = admin_client.post(
        f"/admin/access-requests/{row['id']}/reject", json={"notes": "Not a fit."}
    ).json()
    assert result["status"] == "rejected"
    assert result["notes"] == "Not a fit."

    audit = admin_client.get("/admin/audit").json()["entries"]
    assert any(e["action"] == "access_request.reject" for e in audit)


def test_html_in_request_is_stored_verbatim_for_escaping(gateway_url, admin_client):
    email = f"xss{time.time_ns()}@example.com"
    payload = "<script>alert(1)</script>"
    post_request(gateway_url, email=email, name=payload, use_case=payload)

    rows = admin_client.get("/admin/access-requests").json()["requests"]
    row = next(r for r in rows if r["email"] == email)
    # Stored as data, not executed. The UI renders it as text.
    assert row["name"] == payload
    assert row["use_case"] == payload


def test_access_requests_are_owner_only(gateway_url, partner_login_client):
    assert partner_login_client.get("/admin/access-requests").status_code == 403


@pytest.fixture(scope="module")
def partner_login_client(admin_client, gateway_url):
    partner = admin_client.post(
        "/admin/partners", json={"name": f"AR {time.time_ns()}", "allow_free_tier": True}
    ).json()
    email = f"ar{time.time_ns()}@example.com"
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
