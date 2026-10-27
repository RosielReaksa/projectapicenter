"""Load test against the fake provider. Reports p50/p95/p99, rps, error rate, memory."""

from __future__ import annotations

import concurrent.futures
import json
import os
import socket
import statistics
import subprocess
import sys
import threading
import time

import httpx

ROOT = r"D:\projectradom\projectapicenter"
sys.path.insert(0, ROOT)
GATEWAY = "http://127.0.0.1:8020"
PY = os.path.join(ROOT, ".venv", "Scripts", "python.exe")

DURATION = int(os.environ.get("AUDIT_LOAD_SECONDS", "600"))
CONCURRENCY = int(os.environ.get("AUDIT_LOAD_CONCURRENCY", "20"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_fake():
    port = free_port()
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "tests.fake_provider:fake", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "error"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{port}/v1/models", timeout=2)
            return f"http://127.0.0.1:{port}/v1", proc
        except Exception:
            time.sleep(0.3)
    raise RuntimeError("no fake")


def gateway_rss_mb() -> float | None:
    """Resident memory of the uvicorn process serving the gateway."""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process python -ErrorAction SilentlyContinue | "
             "Where-Object { $_.Path -like '*projectapicenter*' } | "
             "Measure-Object WorkingSet64 -Sum).Sum"],
            text=True, timeout=30).strip()
        return round(int(out) / (1024 * 1024), 1) if out and out.isdigit() else None
    except Exception:
        return None


def main():
    fake, proc = start_fake()
    c = httpx.Client(base_url=GATEWAY, timeout=60.0)
    c.post("/admin/auth/login",
           json={"email": "admin@audit.local", "password": "audit-password-123"})
    c.headers["X-CSRF-Token"] = c.cookies.get("gw_csrf")

    providers = c.get("/admin/providers").json()["providers"]
    for p in providers:
        if p["name"] in ("custom", "custom2"):
            c.post(f"/admin/providers/{p['id']}/resume")
            c.patch(f"/admin/providers/{p['id']}",
                    json={"api_key": "audit-fake-key-0000abcd", "base_url": fake})
            c.post("/admin/models", json={"provider_id": p["id"], "model_name": "ok-model",
                                          "input_price_per_m": 1.0, "output_price_per_m": 2.0})

    partner = c.post("/admin/partners", json={
        "name": f"Load {int(time.time())}", "allow_free_tier": True,
        "rpm_limit": 100000, "concurrency_limit": 500,
        "monthly_cap_usd": 100.0}).json()
    key = c.post("/admin/keys",
                 json={"name": "load", "partner_id": partner["id"]}).json()["key"]

    latencies: list[float] = []
    statuses: dict[int, int] = {}
    lock = threading.Lock()
    stop_at = time.time() + DURATION
    mem_samples: list[float] = []

    def worker():
        local_lat: list[float] = []
        local_status: dict[int, int] = {}
        with httpx.Client(timeout=90.0) as client:
            while time.time() < stop_at:
                t0 = time.perf_counter()
                try:
                    r = client.post(
                        f"{GATEWAY}/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"model": "ok-model",
                              "messages": [{"role": "user", "content": "load"}]},
                    )
                    code = r.status_code
                except Exception:
                    code = 0
                elapsed = (time.perf_counter() - t0) * 1000
                local_lat.append(elapsed)
                local_status[code] = local_status.get(code, 0) + 1
        with lock:
            latencies.extend(local_lat)
            for k, v in local_status.items():
                statuses[k] = statuses.get(k, 0) + v

    mem_before = gateway_rss_mb()
    started = time.time()
    monitor_stop = threading.Event()

    def monitor():
        while not monitor_stop.is_set():
            value = gateway_rss_mb()
            if value:
                mem_samples.append(value)
            monitor_stop.wait(30)

    mon = threading.Thread(target=monitor, daemon=True)
    mon.start()

    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        list(pool.map(lambda _: worker(), range(CONCURRENCY)))

    monitor_stop.set()
    elapsed = time.time() - started
    mem_after = gateway_rss_mb()

    total = len(latencies)
    errors = sum(v for k, v in statuses.items() if k != 200)
    lat = sorted(latencies)

    def pct(p):
        return lat[min(len(lat) - 1, int(len(lat) * p))] if lat else 0

    report = {
        "duration_seconds": round(elapsed, 1),
        "concurrency": CONCURRENCY,
        "total_requests": total,
        "requests_per_second": round(total / elapsed, 1),
        "error_rate_pct": round(errors / total * 100, 2) if total else 0,
        "status_counts": statuses,
        "latency_ms": {
            "p50": round(pct(0.50), 1),
            "p95": round(pct(0.95), 1),
            "p99": round(pct(0.99), 1),
            "max": round(lat[-1], 1) if lat else 0,
        },
        "memory_mb": {
            "before": mem_before,
            "after": mem_after,
            "samples": mem_samples[:20],
            "growth": round((mem_after or 0) - (mem_before or 0), 1),
        },
    }
    print(json.dumps(report, indent=2))
    proc.terminate()


if __name__ == "__main__":
    main()
