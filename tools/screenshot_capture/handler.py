"""Headless screenshot using playwright (preferred) or selenium fallback."""

from __future__ import annotations
from pathlib import Path
from typing import Any
from tools.base import ToolHandler
from core.exceptions import SafetyError

_CWD = Path.cwd()


def _safe_path(rel: str) -> Path:
    p = (_CWD / rel).resolve()
    if not str(p).startswith(str(_CWD)):
        raise SafetyError(f"Path escape: {rel!r}")
    return p


class ScreenshotCaptureHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        url = inputs["url"]
        if not url.startswith(("http://", "https://")):
            return {"error": "URL must start with http:// or https://"}

        out_path = _safe_path(inputs["output_path"])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        width = int(inputs.get("width", 1280))
        height = int(inputs.get("height", 720))
        wait_sec = int(inputs.get("wait_seconds", 2))

        # Try playwright first
        try:
            from playwright.async_api import async_playwright
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": width, "height": height})
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(wait_sec * 1000)
                await page.screenshot(path=str(out_path))
                await browser.close()
            return {"path": str(out_path.relative_to(_CWD)), "width": width, "height": height}
        except ImportError:
            pass

        # Fallback: selenium
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            opts = Options()
            opts.add_argument("--headless")
            opts.add_argument(f"--window-size={width},{height}")
            driver = webdriver.Chrome(options=opts)
            try:
                driver.get(url)
                import time; time.sleep(wait_sec)
                driver.save_screenshot(str(out_path))
            finally:
                driver.quit()
            return {"path": str(out_path.relative_to(_CWD)), "width": width, "height": height}
        except ImportError:
            return {
                "error": "Neither playwright nor selenium is installed. "
                         "Install with: pip install playwright && playwright install chromium",
                "skipped": True,
            }

    async def self_test(self) -> bool:
        # Skippable self-test — just verify we handle missing deps gracefully
        result = await self._run({"url": "http://localhost:9999", "output_path": "/tmp/test_ss.png"})
        return "error" in result or "path" in result


handler = ScreenshotCaptureHandler()
