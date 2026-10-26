"""Import every module so syntax and import errors surface at once."""

from __future__ import annotations

import importlib
import pkgutil

import app

failures = []
for module in pkgutil.walk_packages(app.__path__, prefix="app."):
    try:
        importlib.import_module(module.name)
    except Exception as exc:  # noqa: BLE001 - this script reports, it does not raise
        failures.append((module.name, exc))

if failures:
    for name, exc in failures:
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    raise SystemExit(1)
print("All modules imported cleanly.")
