"""
platforms/base.py — Abstract base class for all social media platforms.

Every platform (Instagram, Facebook, Twitter, TikTok) extends this.
Defines the contract every platform must implement and shares common
logic: login state checking, screenshot capture, and session validation.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page


class LoginStatus(str, Enum):
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    CHECKPOINT = "checkpoint"   # Suspicious activity / 2FA challenge
    BANNED = "banned"
    UNKNOWN = "unknown"


@dataclass
class PostResult:
    success: bool
    platform: str
    post_url: str | None = None
    post_id: str | None = None
    error: str | None = None
    screenshot: str | None = None


@dataclass
class EngagementResult:
    action: str                 # "like" | "follow" | "comment" | "share"
    target: str
    success: bool
    error: str | None = None


@dataclass
class AccountStats:
    followers: int = 0
    following: int = 0
    posts: int = 0
    username: str = ""
    display_name: str = ""
    raw: dict = field(default_factory=dict)


class BasePlatform(abc.ABC):
    """
    Base class for all social media platform automations.

    Subclasses must implement all abstract methods.
    """

    platform_name: str = "unknown"
    base_url: str = ""

    def __init__(self, page: "Page", account_id: str) -> None:
        self._page = page
        self._account_id = account_id

    # ------------------------------------------------------------------
    # Required: subclasses must implement
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def check_login_status(self) -> LoginStatus:
        """Navigate to home and determine current login state."""
        ...

    @abc.abstractmethod
    async def login(self, username: str, password: str, fa_secret: str = "") -> LoginStatus:
        """Perform login. Return resulting LoginStatus."""
        ...

    @abc.abstractmethod
    async def post_text(self, text: str) -> PostResult:
        """Post a plain-text post/tweet/update."""
        ...

    @abc.abstractmethod
    async def post_image(self, image_path: str, caption: str = "") -> PostResult:
        """Post an image with optional caption."""
        ...

    @abc.abstractmethod
    async def like_post(self, post_url: str) -> EngagementResult:
        """Like/react to a post at the given URL."""
        ...

    @abc.abstractmethod
    async def follow_user(self, username: str) -> EngagementResult:
        """Follow a user by username."""
        ...

    @abc.abstractmethod
    async def comment_on_post(self, post_url: str, comment: str) -> EngagementResult:
        """Leave a comment on a post."""
        ...

    @abc.abstractmethod
    async def get_account_stats(self) -> AccountStats:
        """Fetch follower count and other profile stats."""
        ...

    # ------------------------------------------------------------------
    # Shared helpers (available to all subclasses)
    # ------------------------------------------------------------------

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> None:
        await self._page.goto(url, wait_until=wait_until, timeout=30_000)

    async def screenshot(self, label: str = "") -> str:
        from config.settings import settings
        from utils.logger import get_logger
        import datetime

        ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        name = f"{self._account_id}_{self.platform_name}_{label}_{ts}.png"
        path = settings.screenshots_dir / name
        await self._page.screenshot(path=str(path), full_page=True)
        return str(path)

    async def is_element_visible(self, selector: str, timeout: float = 5000) -> bool:
        try:
            await self._page.wait_for_selector(
                selector, state="visible", timeout=timeout
            )
            return True
        except Exception:
            return False

    async def wait_for_any(
        self,
        selectors: list[str],
        timeout: float = 15_000,
    ) -> str | None:
        """Wait for any selector to appear. Returns the first one found."""
        tasks = [
            self._page.wait_for_selector(s, state="visible", timeout=timeout)
            for s in selectors
        ]
        done, _ = await __import__("asyncio").wait(
            [asyncio.create_task(t) for t in tasks],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            try:
                result = task.result()
                if result:
                    # Figure out which selector matched
                    for s in selectors:
                        if await self._page.locator(s).count() > 0:
                            return s
            except Exception:
                pass
        return None

    import asyncio  # noqa: E402 — needed for wait_for_any
