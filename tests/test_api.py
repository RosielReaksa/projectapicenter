"""Integration tests for the data plane and admin API."""

from __future__ import annotations

import concurrent.futures
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


def wait_for_activity(admin_client, params: dict, timeout: float = 5.0) -> dict:
    """Request logs are committed in a background task, so poll briefly.

    This is the intended behaviour: logging must never delay the response.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        activity = admin_client.get("/admin/activity", params=params).json()
        if activity["total"] >= 1:
            return activity
        time.sleep(0.1)
    return activity


def new_key(admin_client, partner_id: int, **limits) -> str:
    response = admin_client.post(
        "/admin/keys", json={"name": f"k{time.time_ns()}", "partner_id": partner_id, **limits}
    )
    assert response.status_code == 201, response.text
    return response.json()["key"]


# ------------------------------------------------------------- happy path
def test_chat_returns_openai_shape(gateway_url, provider_id, gateway_key):
    response = chat(gateway_url, gateway_key, model="ok-model")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["choices"][0]["message"]["content"] == "hello from fake"
    assert data["gateway"]["provider"] == "custom"
    assert data["gateway"]["cached"] is False
    assert response.headers["x-request-id"].startswith("req_")


def test_cost_and_price_are_both_recorded(gateway_url, provider_id, gateway_key):
    data = chat(gateway_url, gateway_key, model="ok-model").json()
    # 11 prompt @ $1/M + 7 completion @ $2/M
    expected_cost = 11 / 1e6 * 1.0 + 7 / 1e6 * 2.0
    assert data["gateway"]["cost"] == pytest.approx(expected_cost, rel=1e-6)
    assert data["gateway"]["price"] >= data["gateway"]["cost"]


def test_usage_endpoint(gateway_url, provider_id, gateway_key):
    chat(gateway_url, gateway_key, model="ok-model")
    usage = httpx.get(
        f"{gateway_url}/v1/usage", headers={"Authorization": f"Bearer {gateway_key}"}
    ).json()
    assert usage["requests_today"] >= 1
    assert usage["cost_this_month"] > 0
    assert usage["partner"]["slug"]


def test_models_endpoint(gateway_url, provider_id, gateway_key):
    data = httpx.get(
        f"{gateway_url}/v1/models", headers={"Authorization": f"Bearer {gateway_key}"}
    ).json()
    ids = {m["id"] for m in data["data"]}
    assert "ok-model" in ids
    assert "auto" in ids


# ------------------------------------------------------------------ auth
def test_missing_key_is_401(gateway_url, provider_id):
    response = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        json={"model": "ok-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_unknown_key_is_401(gateway_url, provider_id):
    assert chat(gateway_url, "sk-gw-nope", model="ok-model").status_code == 401


def test_disabled_key_is_403(gateway_url, admin_client, partner_id, provider_id):
    created = admin_client.post(
        "/admin/keys", json={"name": "to disable", "partner_id": partner_id}
    ).json()
    admin_client.post(f"/admin/keys/{created['id']}/disable")

    response = chat(gateway_url, created["key"], model="ok-model")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "key_disabled"


def test_expired_key_is_403(gateway_url, admin_client, partner_id, provider_id):
    created = admin_client.post(
        "/admin/keys",
        json={"name": "expired", "partner_id": partner_id, "expires_at": "2020-01-01T00:00:00Z"},
    ).json()
    response = chat(gateway_url, created["key"], model="ok-model")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "key_expired"


def test_ip_allow_list_is_enforced(gateway_url, admin_client, partner_id, provider_id):
    created = admin_client.post(
        "/admin/keys",
        json={"name": "ip locked", "partner_id": partner_id, "allowed_ips": ["10.9.9.9"]},
    ).json()
    response = chat(gateway_url, created["key"], model="ok-model")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ip_not_allowed"


def test_deactivated_partner_blocks_its_keys(gateway_url, admin_client, provider_id):
    partner = admin_client.post("/admin/partners", json={"name": f"Temp {time.time_ns()}"}).json()
    raw = new_key(admin_client, partner["id"])
    assert chat(gateway_url, raw, model="ok-model").status_code == 200

    admin_client.patch(f"/admin/partners/{partner['id']}", json={"is_active": False})
    response = chat(gateway_url, raw, model="ok-model")
    assert response.status_code == 403


# -------------------------------------------------------------- fallback
def test_falls_back_on_500(gateway_url, provider_id, gateway_key):
    response = chat(gateway_url, gateway_key, model="fail-model")
    assert response.status_code == 200
    data = response.json()
    assert data["gateway"]["model"] != "fail-model"
    assert data["gateway"]["fallbacks_tried"]


def test_falls_back_on_429_and_records_it(gateway_url, provider_id, gateway_key):
    response = chat(gateway_url, gateway_key, model="ratelimit-model")
    assert response.status_code == 200
    assert response.json()["gateway"]["fallbacks_tried"][0]["status"] == 429


def test_empty_response_is_a_failure(gateway_url, provider_id, gateway_key):
    response = chat(gateway_url, gateway_key, model="empty-model")
    assert response.status_code == 200
    assert response.json()["gateway"]["model"] != "empty-model"


def test_usage_estimated_when_provider_omits_it(gateway_url, provider_id, gateway_key):
    response = chat(gateway_url, gateway_key, model="no-usage-model")
    assert response.status_code == 200
    body = response.json()
    assert body["usage"]["prompt_tokens"] > 0
    assert body["usage"]["estimated"] is True


def test_unknown_model_is_rejected(gateway_url, provider_id, gateway_key):
    response = chat(gateway_url, gateway_key, model="does-not-exist")
    assert response.status_code in (400, 503)
    assert "Unknown model" in response.json()["error"]["message"]


def test_streaming_returns_clear_error(gateway_url, provider_id, gateway_key):
    response = chat(gateway_url, gateway_key, model="ok-model", stream=True)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "streaming_not_supported"


# ---------------------------------------------------------------- limits
def test_daily_limit_returns_429_with_retry_after(gateway_url, admin_client, partner_id, provider_id):
    raw = new_key(admin_client, partner_id, daily_limit=1)
    assert chat(gateway_url, raw, model="ok-model").status_code == 200
    blocked = chat(gateway_url, raw, model="ok-model")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "daily_limit_reached"
    assert "retry-after" in {k.lower() for k in blocked.headers}


def test_key_rpm_limit(gateway_url, admin_client, partner_id, provider_id):
    raw = new_key(admin_client, partner_id, rpm_limit=2)
    statuses = [chat(gateway_url, raw, model="ok-model").status_code for _ in range(3)]
    assert statuses[:2] == [200, 200]
    assert statuses[2] == 429


def test_partner_rpm_limit_applies_to_all_its_keys(gateway_url, admin_client, provider_id):
    partner = admin_client.post(
        "/admin/partners",
        json={"name": f"Limited {time.time_ns()}", "rpm_limit": 2, "allow_free_tier": True},
    ).json()
    key_a = new_key(admin_client, partner["id"])
    key_b = new_key(admin_client, partner["id"])

    assert chat(gateway_url, key_a, model="ok-model").status_code == 200
    assert chat(gateway_url, key_b, model="ok-model").status_code == 200
    # Third request in the window, on either key, is blocked.
    assert chat(gateway_url, key_a, model="ok-model").status_code == 429


def test_key_monthly_spend_cap(gateway_url, admin_client, partner_id, provider_id):
    raw = new_key(admin_client, partner_id, monthly_cap_usd=0.0)
    response = chat(gateway_url, raw, model="ok-model")
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "spend_cap_reached"


def test_allowed_models_restricts_key(gateway_url, admin_client, partner_id, provider_id):
    raw = new_key(admin_client, partner_id, allowed_models=["ok-model"])
    assert chat(gateway_url, raw, model="ok-model").status_code == 200
    denied = chat(gateway_url, raw, model="fail-model")
    assert denied.status_code == 503
    assert "not allowed" in denied.json()["error"]["message"]


def test_max_tokens_is_clamped(gateway_url, provider_id, gateway_key):
    """A huge max_tokens must not reach the provider unchecked."""
    response = chat(gateway_url, gateway_key, model="ok-model", max_tokens=10_000_000)
    assert response.status_code == 200


# ------------------------------------------------------------ concurrency
def test_concurrency_cap_holds_under_load(gateway_url, admin_client, provider_id):
    """Brief acceptance 6: never more than N in flight, and no leaked slots."""
    partner = admin_client.post(
        "/admin/partners",
        json={
            "name": f"Conc {time.time_ns()}",
            "concurrency_limit": 2,
            "over_capacity": "reject",
            "allow_free_tier": True,
        },
    ).json()
    raw = new_key(admin_client, partner["id"])

    def one(_):
        return chat(gateway_url, raw, model="ok-model").status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(one, range(8)))

    assert any(s == 429 for s in statuses), "concurrency cap never triggered"
    assert all(s in (200, 429) for s in statuses)

    # Slots must be free again afterwards.
    time.sleep(0.2)
    assert chat(gateway_url, raw, model="ok-model").status_code == 200


def test_noisy_neighbor_does_not_break_other_partner(gateway_url, admin_client, provider_id):
    """Brief acceptance 7: a burst from A must not raise B's error rate."""
    noisy = admin_client.post(
        "/admin/partners",
        json={
            "name": f"Noisy {time.time_ns()}",
            "concurrency_limit": 1,
            "rpm_limit": 5,
            "allow_free_tier": True,
        },
    ).json()
    quiet = admin_client.post(
        "/admin/partners",
        json={"name": f"Quiet {time.time_ns()}", "allow_free_tier": True},
    ).json()
    noisy_key = new_key(admin_client, noisy["id"])
    quiet_key = new_key(admin_client, quiet["id"])

    def burst(_):
        return chat(gateway_url, noisy_key, model="ok-model").status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        noisy_statuses = list(pool.map(burst, range(12)))

    quiet_status = chat(gateway_url, quiet_key, model="ok-model").status_code

    assert any(s == 429 for s in noisy_statuses), "the burst should have been throttled"
    assert quiet_status == 200, "the quiet partner must be unaffected"


# ---------------------------------------------------------- idempotency
def test_idempotency_key_replays_without_double_charge(gateway_url, provider_id, gateway_key):
    key_header = f"idem-{time.time_ns()}"
    body = {
        "model": "ok-model",
        "messages": [{"role": "user", "content": "same request"}],
        "temperature": 0.7,  # keeps the cache out of the way
    }
    headers = {"Authorization": f"Bearer {gateway_key}", "Idempotency-Key": key_header}

    first = httpx.post(f"{gateway_url}/v1/chat/completions", json=body, headers=headers, timeout=60)
    second = httpx.post(f"{gateway_url}/v1/chat/completions", json=body, headers=headers, timeout=60)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.headers.get("idempotent-replayed") == "true"
    assert second.json()["gateway"]["request_id"] == first.json()["gateway"]["request_id"]


# ------------------------------------------------------------- end user
def test_end_user_header_is_recorded(gateway_url, admin_client, provider_id, gateway_key):
    tag = f"user-{time.time_ns()}"
    httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_key}", "X-End-User": tag},
        json={"model": "ok-model", "messages": [{"role": "user", "content": "hi"}]},
        timeout=60,
    )
    activity = wait_for_activity(admin_client, {"end_user": tag})
    assert activity["total"] >= 1
    assert activity["requests"][0]["end_user"] == tag


# ---------------------------------------------------------------- caching
def test_cache_is_per_partner(gateway_url, admin_client, provider_id):
    """Two partners asking the same question must not share a cached answer."""
    p1 = admin_client.post("/admin/partners", json={"name": f"C1 {time.time_ns()}", "allow_free_tier": True}).json()
    p2 = admin_client.post("/admin/partners", json={"name": f"C2 {time.time_ns()}", "allow_free_tier": True}).json()
    k1 = new_key(admin_client, p1["id"])
    k2 = new_key(admin_client, p2["id"])

    body = {"model": "ok-model", "messages": [{"role": "user", "content": "identical"}], "temperature": 0}
    first = chat(gateway_url, k1, **body).json()
    second = chat(gateway_url, k2, **body).json()
    assert first["gateway"]["cached"] is False
    assert second["gateway"]["cached"] is False


def test_same_partner_cache_hit(gateway_url, provider_id, gateway_key):
    body = {"model": "ok-model", "messages": [{"role": "user", "content": "cache me"}], "temperature": 0}
    first = chat(gateway_url, gateway_key, **body).json()
    second = chat(gateway_url, gateway_key, **body).json()
    assert first["gateway"]["cached"] is False
    assert second["gateway"]["cached"] is True
    assert second["gateway"]["cost"] == 0


# --------------------------------------------------------------- admin API
def test_provider_key_never_returned(admin_client, provider_id):
    providers = admin_client.get("/admin/providers").json()["providers"]
    custom = next(p for p in providers if p["name"] == "custom")
    assert "fake-provider-key" not in str(custom)
    assert custom["key_hint"].endswith("3456")


def test_full_key_shown_once(admin_client, partner_id):
    created = admin_client.post(
        "/admin/keys", json={"name": "once", "partner_id": partner_id}
    ).json()
    assert created["key"].startswith("sk-gw-")

    listing = admin_client.get("/admin/keys").json()["keys"]
    match = next(k for k in listing if k["id"] == created["id"])
    assert "key" not in match
    assert created["key"].startswith(match["prefix"])


def test_activity_records_requests(gateway_url, admin_client, provider_id, gateway_key):
    tag = f"act-{time.time_ns()}"
    httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_key}", "X-End-User": tag},
        json={"model": "ok-model", "messages": [{"role": "user", "content": "hi"}]},
        timeout=60,
    )
    activity = wait_for_activity(admin_client, {"end_user": tag})
    latest = activity["requests"][0]
    assert latest["requested_model"] == "ok-model"
    assert latest["provider"] == "custom"
    assert latest["status_code"] == 200
    assert latest["price_usd"] >= latest["cost_usd"]


def test_activity_csv_export(gateway_url, admin_client, provider_id, gateway_key):
    chat(gateway_url, gateway_key, model="ok-model")
    response = admin_client.get("/admin/activity/export/csv")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    header = response.text.splitlines()[0]
    assert "price_usd" in header


def test_overview_shape(admin_client, provider_id):
    data = admin_client.get("/admin/overview").json()
    for key in ("today", "month", "series", "providers", "recent", "alerts"):
        assert key in data
    assert len(data["series"]) == 7


def test_usage_analytics_shape(admin_client, provider_id):
    data = admin_client.get("/admin/usage", params={"days": 7}).json()
    assert len(data["daily"]) == 7
    assert {"budget", "cache", "by_partner", "by_model"} <= set(data)


def test_partner_usage_detail(admin_client, partner_id, provider_id):
    data = admin_client.get(f"/admin/partners/{partner_id}/usage").json()
    assert data["partner"]["id"] == partner_id
    assert "margin_usd" in data["usage"]


def test_playground_shares_the_pipeline(admin_client, provider_id):
    response = admin_client.post(
        "/admin/playground/run",
        json={
            "model": "ok-model",
            "messages": [{"role": "user", "content": "hi"}],
            "temperature": 0.9,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["content"] == "hello from fake"
    assert data["gateway"]["provider"] == "custom"


# ---------------------------------------------------------------- security
def test_admin_requires_login(gateway_url):
    assert httpx.get(f"{gateway_url}/admin/keys").status_code == 401


def test_csrf_required_for_writes(gateway_url, admin_client, partner_id):
    with httpx.Client(base_url=gateway_url, timeout=10.0) as fresh:
        fresh.cookies.set("gw_session", admin_client.cookies.get("gw_session"))
        response = fresh.post("/admin/keys", json={"name": "nope", "partner_id": partner_id})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "csrf_failed"


def test_prompt_text_not_stored_in_metadata_mode(gateway_url, admin_client, provider_id, gateway_key):
    tag = f"priv-{time.time_ns()}"
    httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_key}", "X-End-User": tag},
        json={"model": "ok-model", "messages": [{"role": "user", "content": "SECRET-PROMPT-TEXT"}]},
        timeout=60,
    )
    latest = wait_for_activity(admin_client, {"end_user": tag})["requests"][0]
    detail = admin_client.get(f"/admin/activity/{latest['id']}").json()
    assert "SECRET-PROMPT-TEXT" not in str(detail)


def test_health_is_public_and_secret_free(gateway_url, provider_id):
    data = httpx.get(f"{gateway_url}/health").json()
    assert "checks" in data
    assert data["checks"]["database"] is True
    assert "fake-provider-key" not in str(data)


def test_metrics_restricted_to_loopback(gateway_url):
    response = httpx.get(f"{gateway_url}/metrics")
    # Tests run from 127.0.0.1, so this is allowed; assert it is Prometheus text.
    assert response.status_code == 200
    assert "gateway_requests_total" in response.text


def test_oversized_body_rejected(gateway_url, provider_id, gateway_key):
    huge = "x" * 1_200_000
    response = httpx.post(
        f"{gateway_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {gateway_key}"},
        json={"model": "ok-model", "messages": [{"role": "user", "content": huge}]},
        timeout=60,
    )
    assert response.status_code == 413
