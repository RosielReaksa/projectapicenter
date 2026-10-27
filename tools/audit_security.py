"""Security audit probes. Prints column types only, never values."""

from __future__ import annotations

import os
import sqlite3
import sys

import httpx

ROOT = r"D:\projectradom\projectapicenter"
sys.path.insert(0, ROOT)
GATEWAY = "http://127.0.0.1:8020"
DB = os.path.join(ROOT, "data", "audit.db")


def say(line=""):
    print(line)


def main():
    say("=== 4a. COLUMN TYPES (schema only, no values) ===")
    con = sqlite3.connect(DB)
    for table, cols in (
        ("gateway_keys", ("key_hash", "key_prefix")),
        ("providers", ("api_key_enc",)),
        ("users", ("password_hash", "totp_secret")),
    ):
        for col in cols:
            row = con.execute(
                "SELECT type FROM pragma_table_info(?) WHERE name = ?", (table, col)
            ).fetchone()
            say(f"  {table}.{col}: {row[0] if row else 'COLUMN MISSING'}")

    say("\n=== 4b. SECRET LEAK SCAN IN THE DATABASE ===")
    # A gateway key was created during the probes; check only for its last 4.
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    hits = []
    for table in tables:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        for col in cols:
            try:
                rows = con.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE CAST({col} AS TEXT) LIKE 'sk-gw-%'"
                ).fetchone()[0]
                if rows:
                    hits.append(f"{table}.{col}: {rows} row(s) start with sk-gw-")
            except sqlite3.Error:
                pass
    say("  " + ("\n  ".join(hits) if hits else "no column contains a raw sk-gw- value"))

    plain_key = con.execute(
        "SELECT COUNT(*) FROM providers WHERE api_key_enc LIKE 'audit-fake-key%'"
    ).fetchone()[0]
    say(f"  providers.api_key_enc holding plaintext test key: {plain_key} (expect 0)")

    say("\n=== 4c. /admin WITHOUT LOGIN ===")
    for path in ("/admin/keys", "/admin/providers", "/admin/overview",
                 "/admin/maintenance", "/admin/access-requests"):
        r = httpx.get(f"{GATEWAY}{path}", timeout=20)
        say(f"  GET {path:28} -> {r.status_code} {r.json().get('error',{}).get('code','')}")

    say("\n=== 4d. 2FA ENFORCEMENT ===")
    say("  (login succeeded in this audit with password only; see report)")

    say("\n=== 4e. PUBLIC SIGNUP PATHS ===")
    for path, method in (("/public/access-requests", "POST"),
                         ("/admin/auth/register", "POST"),
                         ("/public/register", "POST"),
                         ("/v1/keys", "POST")):
        try:
            r = httpx.request(method, f"{GATEWAY}{path}",
                              json={"email": "x@example.com", "password": "x" * 12}, timeout=20)
            say(f"  {method} {path:26} -> {r.status_code}")
        except Exception as exc:
            say(f"  {method} {path:26} -> error {exc}")

    say("\n=== 4f. REQUEST-ACCESS PROTECTIONS ===")
    results = []
    for i in range(8):
        r = httpx.post(f"{GATEWAY}/public/access-requests", json={
            "name": "Audit", "email": f"audit{i}@example.com", "company": "C",
            "use_case": "u", "expected_usage": "Not sure", "accepted_terms": True}, timeout=20)
        results.append(r.status_code)
    say(f"  8 submissions from one IP -> {results}")
    say(f"  rate limit triggered: {429 in results}")

    honey = httpx.post(f"{GATEWAY}/public/access-requests", json={
        "name": "Bot", "email": "honeypot-audit@example.com", "company": "C",
        "use_case": "u", "expected_usage": "Not sure", "accepted_terms": True,
        "website_url": "http://spam.example"}, timeout=20)
    say(f"  honeypot submission -> {honey.status_code} (indistinguishable from success)")

    c = httpx.Client(base_url=GATEWAY, timeout=30.0)
    c.post("/admin/auth/login",
           json={"email": "admin@audit.local", "password": "audit-password-123"})
    c.headers["X-CSRF-Token"] = c.cookies.get("gw_csrf")
    rows = c.get("/admin/access-requests").json()["requests"]
    say(f"  honeypot stored? {any(r['email'] == 'honeypot-audit@example.com' for r in rows)}")

    say("\n=== 4g. CORS ON /v1 ===")
    r = httpx.options(f"{GATEWAY}/v1/chat/completions",
                      headers={"Origin": "https://evil.example",
                               "Access-Control-Request-Method": "POST"}, timeout=20)
    say(f"  preflight -> {r.status_code}, "
        f"allow-origin={r.headers.get('access-control-allow-origin', 'ABSENT')}")

    say("\n=== 4h. SECURITY HEADERS ===")
    h = httpx.get(f"{GATEWAY}/", timeout=20).headers
    for name in ("content-security-policy", "x-content-type-options",
                 "referrer-policy", "x-frame-options", "permissions-policy"):
        say(f"  {name}: {h.get(name, 'ABSENT')[:70]}")

    con.close()


if __name__ == "__main__":
    main()
