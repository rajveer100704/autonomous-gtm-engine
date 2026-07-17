"""
Browser Service — abstract interface over any browser automation backend.

Today: Playwright (via PlaywrightAdapter).
Tomorrow: Browserbase, Steel.dev, or any other cloud browser — same interface.

Usage:
    from gtm_engine.browser.browser_service import BrowserService
    browser = BrowserService()
    await browser.navigate("https://linkedin.com/in/johndoe")
    content = await browser.get_text(".profile-info")
    await browser.click(".connect-button")
    await browser.close()
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any
import logging

log = logging.getLogger("gtm.browser_service")


class AbstractBrowserAdapter(ABC):
    """
    Protocol that every browser backend must satisfy.
    Swap Playwright for Browserbase by swapping the adapter, not the callers.
    """

    @abstractmethod
    async def start(self, headless: bool = True, state_file: str | None = None) -> None:
        """Launch or connect the browser."""

    @abstractmethod
    async def navigate(self, url: str) -> None:
        """Navigate to a URL."""

    @abstractmethod
    async def click(self, selector: str, timeout: int = 5000) -> bool:
        """Click an element. Returns True if found and clicked."""

    @abstractmethod
    async def type_text(self, selector: str, text: str, delay: int = 50) -> bool:
        """Type into an element. Returns True if found."""

    @abstractmethod
    async def get_text(self, selector: str) -> str:
        """Return innerText of first matching element, or empty string."""

    @abstractmethod
    async def wait_for(self, selector: str, timeout: int = 5000) -> bool:
        """Wait for element to appear. Returns True if found."""

    @abstractmethod
    async def save_state(self, path: str) -> None:
        """Persist browser storage state (cookies, localStorage) to file."""

    @abstractmethod
    async def screenshot(self, path: str) -> None:
        """Take a screenshot."""

    @abstractmethod
    async def close(self) -> None:
        """Close browser and release resources."""

    @abstractmethod
    async def random_delay(self, min_s: float = 1.0, max_s: float = 4.0) -> None:
        """Wait a human-like random interval to avoid bot detection."""


class BrowserService:
    """
    Facade that delegates to a chosen adapter.
    Default adapter: Playwright (lazy-imported to avoid hard dependency).
    """

    def __init__(self, adapter: AbstractBrowserAdapter | None = None) -> None:
        self._adapter = adapter  # injected or lazily created

    def _get_adapter(self) -> AbstractBrowserAdapter:
        if self._adapter is None:
            from gtm_engine.browser.playwright_adapter import PlaywrightAdapter
            self._adapter = PlaywrightAdapter()
        return self._adapter

    async def start(self, headless: bool = True, state_file: str | None = None) -> None:
        log.info("BrowserService: starting (headless=%s)", headless)
        await self._get_adapter().start(headless=headless, state_file=state_file)

    async def navigate(self, url: str) -> None:
        await self._get_adapter().navigate(url)

    async def click(self, selector: str, timeout: int = 5000) -> bool:
        return await self._get_adapter().click(selector, timeout)

    async def type_text(self, selector: str, text: str, delay: int = 50) -> bool:
        return await self._get_adapter().type_text(selector, text, delay)

    async def get_text(self, selector: str) -> str:
        return await self._get_adapter().get_text(selector)

    async def wait_for(self, selector: str, timeout: int = 5000) -> bool:
        return await self._get_adapter().wait_for(selector, timeout)

    async def save_state(self, path: str) -> None:
        await self._get_adapter().save_state(path)

    async def screenshot(self, path: str) -> None:
        await self._get_adapter().screenshot(path)

    async def close(self) -> None:
        await self._get_adapter().close()

    async def random_delay(self, min_s: float = 1.0, max_s: float = 4.0) -> None:
        await self._get_adapter().random_delay(min_s, max_s)

    async def __aenter__(self) -> "BrowserService":
        await self.start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()
