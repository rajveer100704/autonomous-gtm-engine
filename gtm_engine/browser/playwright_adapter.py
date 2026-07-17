"""
Playwright adapter — implements AbstractBrowserAdapter using Playwright.

Install: pip install playwright && playwright install chromium

This is the production adapter. For cloud browsers, create a BrowserbaseAdapter
or SteelAdapter implementing the same interface.
"""
from __future__ import annotations

import asyncio
import random
import logging
from typing import TYPE_CHECKING

from gtm_engine.browser.browser_service import AbstractBrowserAdapter

if TYPE_CHECKING:
    from playwright.async_api import Page, BrowserContext

log = logging.getLogger("gtm.playwright_adapter")


class PlaywrightAdapter(AbstractBrowserAdapter):
    """
    Playwright implementation of the browser interface.
    Human-mimicking: random delays, realistic viewport, non-headless user agent.
    """

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context: "BrowserContext | None" = None
        self._page: "Page | None" = None

    async def start(self, headless: bool = True, state_file: str | None = None) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )

        context_kwargs: dict = {
            "viewport": {"width": 1280, "height": 800},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }
        if state_file:
            try:
                import os
                if os.path.exists(state_file):
                    context_kwargs["storage_state"] = state_file
                    log.info("PlaywrightAdapter: loaded session from %s", state_file)
            except Exception as exc:
                log.warning("Could not load state file %s: %s", state_file, exc)

        self._context = await self._browser.new_context(**context_kwargs)
        self._page = await self._context.new_page()

        # Remove automation fingerprint
        await self._page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        log.info("PlaywrightAdapter: browser started (headless=%s)", headless)

    async def navigate(self, url: str) -> None:
        assert self._page, "Call start() first"
        await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await self.random_delay(1.5, 3.0)

    async def click(self, selector: str, timeout: int = 5000) -> bool:
        try:
            await self._page.wait_for_selector(selector, timeout=timeout)
            await self._page.click(selector)
            await self.random_delay(0.5, 1.5)
            return True
        except Exception as exc:
            log.debug("click(%s) failed: %s", selector, exc)
            return False

    async def type_text(self, selector: str, text: str, delay: int = 50) -> bool:
        try:
            await self._page.wait_for_selector(selector, timeout=5000)
            await self._page.type(selector, text, delay=delay)
            return True
        except Exception as exc:
            log.debug("type_text(%s) failed: %s", selector, exc)
            return False

    async def get_text(self, selector: str) -> str:
        try:
            el = await self._page.query_selector(selector)
            return (await el.inner_text()) if el else ""
        except Exception:
            return ""

    async def wait_for(self, selector: str, timeout: int = 5000) -> bool:
        try:
            await self._page.wait_for_selector(selector, timeout=timeout)
            return True
        except Exception:
            return False

    async def save_state(self, path: str) -> None:
        if self._context:
            await self._context.storage_state(path=path)
            log.info("PlaywrightAdapter: saved session state to %s", path)

    async def screenshot(self, path: str) -> None:
        if self._page:
            await self._page.screenshot(path=path)

    async def close(self) -> None:
        if self._page:
            await self._page.close()
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        log.info("PlaywrightAdapter: closed")

    async def random_delay(self, min_s: float = 1.0, max_s: float = 4.0) -> None:
        delay = random.uniform(min_s, max_s)
        await asyncio.sleep(delay)
