"""Opsis URL-source adapter (D-048 Phase A).

URL → Playwright headless Chromium → viewport screenshot → local
file path. Isolated from claude_cli_interpreter so the Playwright
dependency is optional — file_source + interpreter work without
Playwright installed.

Install (when actually using):
    pip install playwright
    python -m playwright install chromium

Safety note (D-048 §Safety): runs in an isolated headless context
with no user cookies, no credentials. Do not reuse this adapter
for authenticated sessions.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def acquire_url(url: str, output_dir: Path,
                wait_ms: int = 1500,
                viewport_width: int = 1280,
                viewport_height: int = 800) -> Optional[Path]:
    """Render a URL and save a viewport screenshot.

    Returns the saved PNG path, or None on failure. Imports
    Playwright at call time so module import does not require the
    package.
    """
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError as e:
        logger.warning(
            f"url_source: Playwright not installed "
            f"(pip install playwright + playwright install chromium): {e}"
        )
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    # Sanitize URL for filename
    slug = "".join(c if c.isalnum() else "_" for c in url)[:80]
    out_path = output_dir / f"url_{ts}_{slug}.png"

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": viewport_width, "height": viewport_height},
                # Isolated — no stored cookies / credentials
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(wait_ms)
            page.screenshot(path=str(out_path), full_page=False)
            browser.close()
        return out_path
    except Exception as e:
        logger.warning(f"url_source: capture failed for {url!r}: {e}")
        return None
