"""
core/proxy.py — SmartProxy residential proxy management.

Correct SmartProxy URL format (both rotating and sticky sessions).
No fake REST API — SmartProxy works via authenticated HTTP proxy protocol.
Includes health checking, automatic session rotation, and per-account
sticky session persistence.
"""
from __future__ import annotations

import asyncio
import random
import string
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from config.settings import settings
from utils.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ProxySession:
    session_id: str
    country: str
    url: str                        # Full authenticated URL (never log this)
    created_at: float = field(default_factory=time.monotonic)
    request_count: int = 0
    is_banned: bool = False

    @property
    def age_minutes(self) -> float:
        return (time.monotonic() - self.created_at) / 60

    @property
    def is_expired(self) -> bool:
        """SmartProxy sticky sessions last up to 30 minutes."""
        return self.age_minutes >= settings.smartproxy.session_duration_minutes

    def as_playwright_dict(self) -> dict:
        """Return format Playwright's browser.new_context(proxy=...) expects."""
        return {"server": self.url}

    def as_httpx_dict(self) -> dict:
        """Return format httpx.AsyncClient(proxies=...) expects."""
        return {"http://": self.url, "https://": self.url}

    def masked_url(self) -> str:
        return settings.smartproxy.masked(self.url)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class ProxyManager:
    """
    Manages per-account proxy sessions with automatic rotation.

    Design decisions:
    - One sticky session per account (same IP = same identity = lower suspicion)
    - Sessions are rotated before they expire (proactive, not reactive)
    - Banned sessions are immediately replaced
    - Health checks use httpbin.org/ip (lightweight, reliable)
    """

    HEALTH_CHECK_URL = "https://httpbin.org/ip"
    HEALTH_CHECK_TIMEOUT = 12.0  # seconds

    def __init__(self) -> None:
        # account_id → ProxySession
        self._sessions: dict[str, ProxySession] = {}
        self._lock = asyncio.Lock()
        sp = settings.smartproxy
        self._user = sp.user
        self._password = sp.password

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_for_account(
        self,
        account_id: str,
        country: str = "us",
        force_new: bool = False,
    ) -> ProxySession:
        """
        Return the current sticky session for this account.
        Creates a new one if it doesn't exist, is expired, or is banned.
        """
        async with self._lock:
            existing = self._sessions.get(account_id)

            if existing and not force_new and not existing.is_expired and not existing.is_banned:
                return existing

            # Need a new session
            session = self._create_session(country)
            self._sessions[account_id] = session

            log.info(
                "proxy_session_created",
                account=account_id,
                session=session.session_id,
                country=country,
                proxy=session.masked_url(),
            )
            return session

    async def mark_banned(self, account_id: str) -> ProxySession | None:
        """
        Call this when a request returns a ban/block response.
        Immediately rotates to a fresh IP.
        """
        async with self._lock:
            existing = self._sessions.get(account_id)
            if existing:
                existing.is_banned = True
                log.warning(
                    "proxy_banned",
                    account=account_id,
                    session=existing.session_id,
                )
            # Return a new session
        return await self.get_for_account(account_id, force_new=True)

    async def health_check(self, session: ProxySession) -> bool:
        """
        Verify proxy works and return True/False.
        Logs the external IP (useful for debugging geo-targeting).
        """
        try:
            async with httpx.AsyncClient(
                proxies=session.as_httpx_dict(),
                timeout=self.HEALTH_CHECK_TIMEOUT,
            ) as client:
                resp = await client.get(self.HEALTH_CHECK_URL)
                resp.raise_for_status()
                ip = resp.json().get("origin", "unknown")
                log.info(
                    "proxy_health_ok",
                    session=session.session_id,
                    external_ip=ip,
                    country=session.country,
                )
                return True
        except Exception as exc:
            log.warning(
                "proxy_health_failed",
                session=session.session_id,
                error=str(exc),
            )
            return False

    async def rotate_expiring_sessions(self) -> None:
        """
        Background task — call every few minutes to proactively rotate
        sessions that are close to the 30-minute SmartProxy timeout.
        """
        async with self._lock:
            expiring = [
                (aid, sess)
                for aid, sess in self._sessions.items()
                if sess.age_minutes >= settings.smartproxy.session_duration_minutes - 2
            ]

        for account_id, old_sess in expiring:
            await self.get_for_account(account_id, country=old_sess.country, force_new=True)
            log.info("proxy_proactive_rotation", account=account_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_session(self, country: str) -> ProxySession:
        """Generate a new sticky-session proxy URL."""
        session_id = _random_session_id()
        url = settings.smartproxy.sticky_url(session_id, country)
        return ProxySession(
            session_id=session_id,
            country=country,
            url=url,
        )


def _random_session_id(length: int = 8) -> str:
    """Generate a random alphanumeric session ID."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))
