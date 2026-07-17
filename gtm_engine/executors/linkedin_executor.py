"""
LinkedIn Executor — browser-based LinkedIn outreach via the BrowserService abstraction.

Design:
  - Uses BrowserService → PlaywrightAdapter (swappable for Browserbase etc.)
  - Persists session in browser/linkedin_state.json (gitignored)
  - Human-approval mode: opens browser, fills form, waits for human click
  - Autonomous mode: clicks Send automatically
  - Daily rate limit guard (max 20 connection requests/day)

Mock mode (GTM_MOCK_MODE=true):
  Skips the browser entirely. Returns a mock success response.

Usage:
    from gtm_engine.executors.linkedin_executor import LinkedInExecutor

    executor = LinkedInExecutor()
    await executor.init()  # loads saved session or logs in
    result = await executor.send_connection_request(
        profile_url="https://linkedin.com/in/johndoe",
        message="Hi John, I noticed your company recently...",
    )
    await executor.close()
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

log = logging.getLogger("gtm.linkedin_executor")

STATE_FILE = "browser/linkedin_state.json"
DAILY_LIMIT = 20
LINKEDIN_BASE = "https://www.linkedin.com"


class LinkedInExecutor:
    """
    Playwright-powered LinkedIn automation.
    All LinkedIn UI logic lives here — nothing else knows about selectors.
    """

    def __init__(self) -> None:
        from gtm_engine.browser.browser_service import BrowserService
        self._browser = BrowserService()
        self._initialized = False
        self._daily_count = 0

    async def init(
        self,
        email: str | None = None,
        password: str | None = None,
        headless: bool = True,
    ) -> None:
        """Start browser and load saved session or log in fresh."""
        from gtm_engine.config import settings

        os.makedirs("browser", exist_ok=True)

        headless = headless if not settings.human_approval_mode else False
        await self._browser.start(headless=headless, state_file=STATE_FILE)

        # Check if session is still valid
        await self._browser.navigate(f"{LINKEDIN_BASE}/feed/")
        await asyncio.sleep(2)

        is_logged_in = await self._browser.wait_for('[data-test-icon="home-zero"]', timeout=3000)
        if not is_logged_in:
            is_logged_in = await self._browser.wait_for(".global-nav__me-photo", timeout=3000)

        if not is_logged_in:
            log.info("LinkedInExecutor: no valid session, attempting login")
            _email = email or settings.linkedin_email
            _pass = password or settings.linkedin_password
            if not _email or not _pass:
                raise ValueError(
                    "LinkedIn credentials required. Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in .env "
                    "or pass email/password to init()."
                )
            await self._login(_email, _pass)

        self._initialized = True
        log.info("LinkedInExecutor: initialized")

    async def _login(self, email: str, password: str) -> None:
        """Perform LinkedIn login flow."""
        await self._browser.navigate(f"{LINKEDIN_BASE}/login")
        await self._browser.type_text("#username", email)
        await self._browser.type_text("#password", password)
        await self._browser.click('[type="submit"]')
        await self._browser.random_delay(3.0, 5.0)

        # Check for challenge (CAPTCHA, 2FA)
        is_challenge = await self._browser.wait_for("#captcha-challenge", timeout=3000)
        is_verify = await self._browser.wait_for("#input__email_verification_pin", timeout=2000)

        if is_challenge or is_verify:
            log.warning(
                "LinkedInExecutor: login challenge detected — "
                "complete it manually in the browser window"
            )
            await asyncio.sleep(30)  # Give user 30s to solve challenge

        # Save session for next run
        await self._browser.save_state(STATE_FILE)
        log.info("LinkedInExecutor: login complete, session saved")

    async def send_connection_request(
        self,
        profile_url: str,
        message: str,
        human_approval: bool | None = None,
    ) -> dict[str, Any]:
        """
        Navigate to a LinkedIn profile and send a connection request with a personalized note.

        Args:
            profile_url: Full LinkedIn profile URL
            message: Personalized connection note (max 300 chars)
            human_approval: If True, opens browser and waits for human to click Send.
                            If None, uses settings.human_approval_mode.

        Returns:
            {"success": True/False, "action": "connection_sent/human_pending/...", ...}
        """
        from gtm_engine.config import settings

        if settings.mock_mode:
            log.info("LinkedInExecutor [MOCK]: connection to %s | msg: %s", profile_url, message[:60])
            return {
                "success": True,
                "action": "connection_sent",
                "profile_url": profile_url,
                "mock": True,
            }

        if not self._initialized:
            raise RuntimeError("Call await executor.init() before sending")

        if self._daily_count >= DAILY_LIMIT:
            log.warning("LinkedInExecutor: daily limit (%d) reached", DAILY_LIMIT)
            return {"success": False, "action": "rate_limited", "profile_url": profile_url}

        _human_approval = human_approval if human_approval is not None else settings.human_approval_mode

        try:
            await self._browser.navigate(profile_url)

            # Try "Connect" button (1st-degree: not connected)
            connected = await self._browser.click(
                'button[aria-label*="Connect"]', timeout=4000
            )
            if not connected:
                # Might be behind "More" dropdown
                more_clicked = await self._browser.click(
                    'button[aria-label*="More actions"]', timeout=3000
                )
                if more_clicked:
                    connected = await self._browser.click(
                        '[aria-label*="Connect"]', timeout=3000
                    )

            if not connected:
                return {
                    "success": False,
                    "action": "no_connect_button",
                    "profile_url": profile_url,
                }

            # Add a note
            note_clicked = await self._browser.click('button[aria-label*="Add a note"]', timeout=3000)
            if note_clicked:
                # Truncate message to LinkedIn's 300-char limit
                note_text = message[:299]
                await self._browser.type_text('textarea[name="message"]', note_text)
                await self._browser.random_delay(1.0, 2.0)

            if _human_approval:
                log.info(
                    "LinkedInExecutor: human approval mode — review the browser and click Send"
                )
                await asyncio.sleep(60)  # Wait up to 60s for human
                return {
                    "success": True,
                    "action": "human_pending",
                    "profile_url": profile_url,
                }
            else:
                sent = await self._browser.click('button[aria-label*="Send"]', timeout=5000)
                if sent:
                    self._daily_count += 1
                    log.info(
                        "LinkedInExecutor: connection sent to %s (daily=%d)",
                        profile_url, self._daily_count,
                    )
                    await self._browser.random_delay(3.0, 8.0)
                    return {
                        "success": True,
                        "action": "connection_sent",
                        "profile_url": profile_url,
                        "daily_count": self._daily_count,
                    }
                return {"success": False, "action": "send_failed", "profile_url": profile_url}

        except Exception as exc:
            log.error("LinkedInExecutor.send_connection_request: %s", exc)
            return {"success": False, "action": "error", "error": str(exc), "profile_url": profile_url}

    async def extract_profile_info(self, profile_url: str) -> dict[str, str]:
        """
        Extract basic info from a LinkedIn profile page.
        Returns name, title, company — useful for enriching lead data.
        """
        from gtm_engine.config import settings

        if settings.mock_mode:
            return {"name": "Mock Lead", "title": "CTO", "company": "Mock Corp"}

        try:
            await self._browser.navigate(profile_url)
            name = await self._browser.get_text("h1.text-heading-xlarge")
            title = await self._browser.get_text(".text-body-medium.break-words")
            company = await self._browser.get_text(
                '[aria-label*="Current company"] .inline-show-more-text'
            )
            return {"name": name.strip(), "title": title.strip(), "company": company.strip()}
        except Exception as exc:
            log.warning("LinkedInExecutor.extract_profile_info: %s", exc)
            return {}

    async def close(self) -> None:
        await self._browser.close()
        self._initialized = False
