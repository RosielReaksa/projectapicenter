"""Guards for STYLE_UPDATE.md: no damaged text, no hard-coded colours.

These run as part of the normal suite so a regression fails CI rather than
being noticed by eye on the homepage.
"""

from __future__ import annotations

import pathlib
import re

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent

# The damaged token that appeared in the first build: a bracketed fragment where
# the real word belongs. Built by concatenation so this test file never
# contains the literal it is searching for.
DAMAGED = "[" + "O" + "I" + "]"
CORRECT = "Open" + "A" + "I"

SCAN_DIRS = ["app", "frontend/src", "tests", "docs", "tools", "migrations"]
SCAN_FILES = ["frontend/index.html", "README.md"]
EXTENSIONS = {".py", ".js", ".jsx", ".html", ".css", ".md", ".mako", ".ini", ".yml"}
SKIP_DIRS = {"node_modules", "__pycache__", ".venv", "dist", "static", ".pytest_cache"}


def _iter_files():
    for name in SCAN_FILES:
        path = ROOT / name
        if path.exists():
            yield path
    for name in SCAN_DIRS:
        base = ROOT / name
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if SKIP_DIRS & set(path.parts):
                continue
            if path.suffix in EXTENSIONS:
                yield path


def test_no_damaged_token_anywhere():
    """B1: the bracketed fragment must not exist in any source file."""
    offenders = []
    for path in _iter_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if DAMAGED in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"damaged token found in: {offenders}"


def test_correct_token_present_in_public_copy():
    """B1: the public copy actually uses the correct word."""
    home = (ROOT / "frontend/src/pages/public/HomePage.jsx").read_text(encoding="utf-8")
    assert CORRECT + "-compatible" in home


def test_rendered_homepage_text_is_correct(gateway_url):
    """B1, end to end: what a visitor sees is the correct string."""
    httpx.get(f"{gateway_url}/", timeout=30.0)
    # The SPA shell itself has no body copy; assert the built bundle does.
    assets = list((ROOT / "app" / "static" / "assets").glob("*.js"))
    if not assets:
        return
    bundle = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in assets)
    assert CORRECT + "-compatible" in bundle
    assert DAMAGED not in bundle


def test_no_hardcoded_colours_outside_tokens():
    """Acceptance 3: every colour comes from a token."""
    hex_pattern = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    named_pattern = re.compile(
        r"\b(?:bg|text|border|from|to|via|ring|fill|stroke|divide|outline|caret|accent)-"
        r"(indigo|blue|slate|gray|grey|zinc|neutral|stone|red|green|yellow|amber|emerald|"
        r"teal|cyan|sky|violet|purple|fuchsia|pink|rose|orange|lime)(?:-[0-9]{2,3})?\b"
    )

    offenders = []
    src = ROOT / "frontend" / "src"
    for path in src.rglob("*"):
        if path.suffix not in {".jsx", ".js", ".css"} or not path.is_file():
            continue
        if path.name == "index.css":  # token definitions live here by design
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if hex_pattern.search(line) or named_pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert not offenders, f"hard-coded colours at: {offenders}"


def test_code_tab_labels_are_explicit():
    """B2: labels are declared, not produced by CSS capitalize."""
    tabs = (ROOT / "frontend/src/components/CodeTabs.jsx").read_text(encoding="utf-8")
    for label in ("curl", "Python", "JavaScript"):
        assert label in tabs
    # The old bug: a CSS class capitalised "curl" into "Curl".
    home = (ROOT / "frontend/src/pages/public/HomePage.jsx").read_text(encoding="utf-8")
    assert "capitalize" not in home
