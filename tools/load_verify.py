"""Load verification for the connection-pool fix (LOAD_FIX).

Starts a fake provider and the gateway on this machine, points one model at the
fake with a configurable latency, then runs the concurrency scenarios the task
asks for and reports before/after style numbers from a single run:

    error rate, p50/p95/p99, requests/second, peak DB connections,
    shed count, memory growth, and partner B's p50 during partner A's burst.

Usage:
    .venv\\Scripts\\python.exe tools\\load_verify.py
    .venv\\Scripts\\python.exe tools\\load_verify.py --scenario noisy
    .venv\\Scripts\\python.exe tools\\load_verify.py --seconds 600 --concurrency 20

Everything runs in one machine and one database; the report states the pool
settings that were in effect, because those are part of the result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
FAKE_LATENCY = 2.0


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start(module: str, port: int, attr: str = "", log_path: Path | None = None):
    target = f"{module}:{attr}" if attr else module
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    # Environment variables take precedence over .env, so the run is isolated
    # from whatever the developer has configured locally.
    env_file = ROOT / ".env.loadtest"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    sink = open(log_path, "w", encoding="utf-8") if log_path else subprocess.DEVNULL
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", target, "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning", "--workers", "1",
         # uvicorn closes idle keep-alive connections after 5s by default. A
         # load client that reuses a pooled connection just as it closes sees a
         # client-side ReadError, which looks like a gateway failure but is not.
         "--timeout-keep-alive", "120"],
        cwd=str(ROOT), env=env,
        stdout=sink, stderr=subprocess.STDOUT,
    )
    proc._log_sink = sink  # type: ignore[attr-defined]
    return proc


def stop_all(procs: list[subprocess.Popen]) -> None:
    """Terminate every child and confirm none survived, so the test database is
    never left locked by an orphan."""
    for proc in procs:
        try:
            proc.terminate()
        except Exception:
            pass
    for proc in procs:
        try:
            proc.wait(timeout=15)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        sink = getattr(proc, "_log_sink", None)
        if sink not in (None, subprocess.DEVNULL):
            try:
                sink.close()
            except Exception:
                pass


def wait_http(url: str, timeout: float = 60.0) -> None:
    import httpx

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(url, timeout=2)
            return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"nothing answered at {url}")


def rss_mb(pid: int) -> float | None:
    """Resident memory of one process, via the Win32 API (no psutil, no shell)."""
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            ok = psapi.GetProcessMemoryInfo(
                handle, ctypes.byref(counters), counters.cb
            )
            if not ok:
                return None
            return round(counters.WorkingSetSize / (1024 * 1024), 1)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def setup(admin, gateway: str, fake_url: str, model_latency_model: str) -> tuple[str, str]:
    """Register the fake provider and return (partner_id, key)."""
    providers = admin.get("/admin/providers").json()["providers"]
    custom = next((p for p in providers if p["name"] == "custom"), None)
    if custom is None:
        created = admin.post(
            "/admin/providers",
            json={"name": "custom", "label": "Fake", "base_url": fake_url,
                  "api_key": "load-test-key-abcdef123456", "priority": 1,
                  "seed_models": False},
        )
        pid = created.json()["id"] if created.status_code == 201 else created.json()["id"]
    else:
        pid = custom["id"]
    admin.patch(f"/admin/providers/{pid}",
                json={"api_key": "load-test-key-abcdef123456", "base_url": fake_url})
    admin.post(f"/admin/providers/{pid}/resume")

    for model in ("ok-model", model_latency_model):
        admin.post("/admin/models", json={
            "provider_id": pid, "model_name": model,
            "input_price_per_m": 1.0, "output_price_per_m": 2.0})


def make_partner(admin, name: str, **limits) -> dict:
    partner = admin.post("/admin/partners", json={
        "name": name, "allow_free_tier": True,
        "concurrency_limit": 5000, "rpm_limit": 10_000_000,
        "monthly_cap_usd": 1000.0, **limits}).json()
    key = admin.post("/admin/keys", json={"name": f"k-{name}", "partner_id": partner["id"]}).json()["key"]
    return {"id": partner["id"], "key": key}


async def run_load(gateway: str, key: str, model: str, concurrency: int, seconds: float):
    """Return per-request (status, latency_ms) samples plus peak DB checkouts."""
    import httpx

    stop_at = time.time() + seconds
    results: list[tuple[int, float]] = []
    peak_checked_out = 0
    client_errors: dict[str, int] = {}

    async with httpx.AsyncClient(
        base_url=gateway, timeout=120.0,
        limits=httpx.Limits(max_connections=concurrency + 20,
                            max_keepalive_connections=concurrency + 20),
    ) as client:
        stop_sampler = asyncio.Event()

        async def sample_pool():
            nonlocal peak_checked_out
            while not stop_sampler.is_set():
                try:
                    capacity = await asyncio.to_thread(_health_capacity, gateway)
                    peak_checked_out = max(
                        peak_checked_out, int(capacity.get("pool", {}).get("checkedout", 0))
                    )
                except Exception:
                    pass
                await asyncio.sleep(0.25)

        async def worker():
            while time.time() < stop_at:
                began = time.perf_counter()
                try:
                    response = await client.post(
                        "/v1/chat/completions",
                        headers={"Authorization": f"Bearer {key}"},
                        json={"model": model,
                              "messages": [{"role": "user", "content": "load"}]},
                    )
                    code = response.status_code
                except Exception as exc:
                    # Record the exception type so client-side failures are
                    # distinguishable from gateway failures in the report.
                    client_errors[type(exc).__name__] = (
                        client_errors.get(type(exc).__name__, 0) + 1
                    )
                    code = 0
                results.append((code, (time.perf_counter() - began) * 1000))

        sampler = asyncio.create_task(sample_pool())
        await asyncio.gather(*(worker() for _ in range(concurrency)), return_exceptions=True)
        stop_sampler.set()
        await sampler

    return results, peak_checked_out, client_errors


def _health_capacity(gateway: str) -> dict:
    import httpx

    return httpx.get(f"{gateway}/health", timeout=30).json().get("capacity", {})


def summarise(results: list[tuple[int, float]], elapsed: float) -> dict:
    if not results:
        return {"total": 0}
    statuses: dict[int, int] = {}
    for code, _ in results:
        statuses[str(code)] = statuses.get(str(code), 0) + 1
    latencies = sorted(ms for _, ms in results)

    def pct(p: float) -> float:
        return round(latencies[min(len(latencies) - 1, int(len(latencies) * p))], 1)

    total = len(results)
    ok = statuses.get("200", 0)
    shed = statuses.get("429", 0)
    # A deliberate shed is a capacity decision, not a failure. Report both, so
    # the two cannot be confused.
    unexpected = total - ok - shed
    return {
        "total_requests": total,
        "requests_per_second": round(total / elapsed, 1),
        "error_rate_pct": round((total - ok) / total * 100, 2),
        "unexpected_error_rate_pct": round(unexpected / total * 100, 2),
        "status_counts": statuses,
        "latency_ms": {"p50": pct(0.50), "p95": pct(0.95), "p99": pct(0.99),
                       "max": round(latencies[-1], 1)},
        "latency_ms_200_only": _lat(results),
    }


def gateway_capacity(gateway: str) -> dict:
    import httpx

    data = httpx.get(f"{gateway}/health", timeout=30).json()
    return data.get("capacity", {})


def read_env_file(path: Path) -> dict[str, str]:
    """The env values the gateway was actually started with."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def pool_wait_seconds(gateway: str) -> dict:
    """Histogram sums from /metrics, so pool waiting is a measured number."""
    import httpx

    try:
        text = httpx.get(f"{gateway}/metrics", timeout=30).text
    except Exception:
        return {}
    out: dict[str, float] = {}
    for line in text.splitlines():
        if line.startswith("gateway_db_pool_wait_seconds_sum"):
            out["sum"] = float(line.rsplit(" ", 1)[1])
        elif line.startswith("gateway_db_pool_wait_seconds_count"):
            out["count"] = float(line.rsplit(" ", 1)[1])
        elif line.startswith("gateway_requests_shed_total"):
            out["shed_total"] = float(line.rsplit(" ", 1)[1])
    if "sum" in out and "count" in out and out["count"]:
        out["mean_ms"] = round(out["sum"] / out["count"] * 1000, 3)
    return out


async def noisy(gateway: str, noisy_key: str, quiet_key: str, model: str,
                duration: float, concurrency: int):
    """A bursts while B is sampled; report B before and during."""
    import httpx

    async def sample_b(client) -> list[tuple[int, float]]:
        out = []
        while time.time() < sample_until:
            began = time.perf_counter()
            try:
                response = await client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {quiet_key}"},
                    json={"model": model, "messages": [{"role": "user", "content": "b"}]})
                out.append((response.status_code, (time.perf_counter() - began) * 1000))
            except Exception:
                out.append((0, (time.perf_counter() - began) * 1000))
            await asyncio.sleep(0.2)
        return out

    async with httpx.AsyncClient(base_url=gateway, timeout=120.0,
                                 limits=httpx.Limits(max_connections=concurrency + 20,
                                                     max_keepalive_connections=concurrency + 20)) as client:
        # Baseline: B alone.
        global sample_until
        sample_until = time.time() + duration
        baseline = await sample_b(client)

        # During: A bursts continuously while B is sampled.
        sample_until = time.time() + duration
        burst_until = sample_until
        burst = asyncio.create_task(_burst(client, noisy_key, model, burst_until, concurrency))
        during = await sample_b(client)
        await burst
        return baseline, during


async def _burst(client, key: str, model: str, until: float, concurrency: int):
    async def worker():
        while time.time() < until:
            try:
                await client.post("/v1/chat/completions",
                                  headers={"Authorization": f"Bearer {key}"},
                                  json={"model": model,
                                        "messages": [{"role": "user", "content": "a"}]})
            except Exception:
                pass
    await asyncio.gather(*(worker() for _ in range(concurrency)), return_exceptions=True)


def _lat(results):
    values = sorted(ms for code, ms in results if code == 200)
    if not values:
        return {}
    return {
        "p50": round(values[len(values) // 2], 1),
        "p95": round(values[min(len(values) - 1, int(len(values) * 0.95))], 1),
        "errors": sum(1 for code, _ in results if code != 200),
        "n": len(results),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="all",
                        choices=["all", "20", "50", "200", "noisy"])
    parser.add_argument("--seconds", type=float, default=0)
    parser.add_argument("--concurrency", type=int, default=0)
    parser.add_argument("--gateway-log", default="", help="Capture the gateway log here.")
    args = parser.parse_args()

    procs: list[subprocess.Popen] = []
    try:
        return _run(args, procs)
    finally:
        stop_all(procs)


def _run(args, procs: list[subprocess.Popen]) -> int:
    import httpx

    fake_port, gw_port = free_port(), free_port()
    fake_proc = start("tests.fake_provider", fake_port, "fake")
    procs.append(fake_proc)
    fake_url = f"http://127.0.0.1:{fake_port}/v1"
    wait_http(f"{fake_url}/models")

    gw_log = Path(args.gateway_log) if args.gateway_log else None
    gw_proc = start("app.main", gw_port, "app", log_path=gw_log)
    procs.append(gw_proc)
    gateway = f"http://127.0.0.1:{gw_port}"
    wait_http(f"{gateway}/health")

    from app.config import settings as app_settings

    admin = httpx.Client(base_url=gateway, timeout=60.0)
    login = admin.post(
        "/admin/auth/login",
        json={
            "email": os.environ.get("ADMIN_EMAIL", "load@test.local"),
            "password": os.environ.get("ADMIN_PASSWORD", "load-password-123"),
        },
    )
    if login.status_code != 200:
        print(f"admin login failed: {login.status_code} {login.text[:300]}")
        return 1
    admin.headers["X-CSRF-Token"] = admin.cookies.get("gw_csrf", "")

    setup(admin, gateway, fake_url, "slow2-model")
    partner = make_partner(admin, f"Load {time.time_ns()}")
    noisy_partner = make_partner(admin, f"Noisy {time.time_ns()}")
    quiet_partner = make_partner(admin, f"Quiet {time.time_ns()}")

    report: dict = {
        "machine": {"os": "Windows 11", "python": sys.version.split()[0]},
        # Read back from the gateway that actually served the load, not from
        # this driver process, so the numbers describe the real instance.
        "database": {},
        "provider_latency_s": FAKE_LATENCY,
        "scenarios": {},
    }

    live_env = read_env_file(ROOT / ".env.loadtest")
    capacity = gateway_capacity(gateway)
    report["database"] = {
        "url_scheme": live_env.get("DATABASE_URL", "").split("+")[0].split(":")[0],
        "database_url": live_env.get("DATABASE_URL"),
        "pool": capacity.get("pool"),
        "pool_timeout_s": capacity.get("db_pool_timeout_seconds"),
        "shared_state": "redis" if live_env.get("REDIS_URL") else "memory",
    }
    report["capacity_config"] = capacity
    report["max_in_flight"] = capacity.get("in_flight", {}).get("cap")

    rss_before = rss_mb(gw_proc.pid)

    scenarios = ["20", "50", "200"] if args.scenario == "all" else [args.scenario]

    for name in scenarios:
        concurrency = args.concurrency or int(name)
        seconds = args.seconds or (600 if concurrency == 20 else 300)
        model = "slow2-model"

        began = time.time()
        results, peak_checked_out, client_errors = asyncio.run(
            run_load(gateway, partner["key"], model, concurrency, seconds)
        )
        elapsed = time.time() - began

        summary = summarise(results, elapsed)
        summary["concurrency"] = concurrency
        summary["duration_s"] = round(elapsed, 1)
        summary["peak_db_connections_checked_out"] = peak_checked_out
        summary["client_side_exceptions"] = client_errors
        summary["capacity_after"] = gateway_capacity(gateway)
        summary["db_pool_wait_seconds"] = pool_wait_seconds(gateway)
        report["scenarios"][f"concurrency_{concurrency}"] = summary
        print(json.dumps(summary, indent=2), flush=True)

        # Let breakers cool down between scenarios.
        time.sleep(5)

    if args.scenario in ("all", "noisy"):
        began = time.time()
        baseline, during = asyncio.run(
            noisy(gateway, noisy_partner["key"], quiet_partner["key"], "slow2-model",
                  duration=args.seconds or 60, concurrency=20)
        )
        b, d = _lat(baseline), _lat(during)
        delta = (d.get("p50", 0) - b.get("p50", 0)) if b and d else None
        pct = round(delta / b["p50"] * 100, 1) if delta is not None and b.get("p50") else None
        report["scenarios"]["noisy_neighbour"] = {
            "concurrency_a": 20,
            "duration_s": round(time.time() - began, 1),
            "b_baseline": b,
            "b_during_burst": d,
            "p50_delta_ms": delta,
            "p50_delta_pct": pct,
        }
        print(json.dumps(report["scenarios"]["noisy_neighbour"], indent=2), flush=True)

    rss_after = rss_mb(gw_proc.pid)
    report["memory_mb"] = {
        "before": rss_before,
        "after": rss_after,
        "growth": round((rss_after or 0) - (rss_before or 0), 1) if rss_before and rss_after else None,
    }

    out = ROOT / "LOAD_VERIFY_RESULT.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())