"""
platforms/twitter.py — Twitter/X automation.
platforms/facebook.py — Facebook automation.
platforms/tiktok.py   — TikTok automation.

All three in one file for conciseness; split into separate files in production.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pyotp

from platforms.base import (
    AccountStats, BasePlatform, EngagementResult, LoginStatus, PostResult,
)
from utils.human import (
    human_click, human_scroll, human_type,
    random_sleep, reading_pause, think_pause,
)
from utils.logger import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

log = get_logger(__name__)


# ===========================================================================
# Twitter / X
# ===========================================================================

class Twitter(BasePlatform):
    platform_name = "twitter"
    base_url = "https://twitter.com"

    _S = {
        "username_input":  'input[autocomplete="username"]',
        "password_input":  'input[type="password"]',
        "next_button":     'button:has-text("Next")',
        "login_button":    '[data-testid="LoginForm_Login_Button"]',
        "home_link":       '[data-testid="AppTabBar_Home_Link"]',
        "otp_input":       'input[data-testid="ocfEnterTextTextInput"]',
        "tweet_box":       '[data-testid="tweetTextarea_0"]',
        "tweet_button":    '[data-testid="tweetButtonInline"], [data-testid="tweetButton"]',
        "file_input":      'input[data-testid="fileInput"]',
        "like_button":     '[data-testid="like"]',
        "unlike_button":   '[data-testid="unlike"]',
        "follow_button":   '[data-testid="placementTracking"] [role="button"]:has-text("Follow")',
        "comment_box":     '[data-testid="tweetTextarea_0"]',
        "reply_button":    '[data-testid="reply"]',
    }

    async def check_login_status(self) -> LoginStatus:
        await self.navigate(self.base_url)
        await random_sleep(2000, 4000)
        if await self.is_element_visible(self._S["home_link"], timeout=8000):
            return LoginStatus.LOGGED_IN
        if await self.is_element_visible(self._S["username_input"], timeout=5000):
            return LoginStatus.LOGGED_OUT
        return LoginStatus.UNKNOWN

    async def login(self, username: str, password: str, fa_secret: str = "") -> LoginStatus:
        await self.navigate("https://twitter.com/i/flow/login")
        await random_sleep(2000, 4000)

        await human_type(self._page, self._S["username_input"], username)
        next_btn = self._page.locator(self._S["next_button"]).first
        await human_click(self._page, next_btn)
        await random_sleep(1500, 3000)

        # Twitter may insert an extra "Enter phone/email" step
        if await self.is_element_visible('input[data-testid="ocfEnterTextTextInput"]', timeout=4000):
            await human_type(self._page, 'input[data-testid="ocfEnterTextTextInput"]', username)
            await human_click(self._page, self._page.locator(self._S["next_button"]).first)
            await random_sleep(1500, 3000)

        await human_type(self._page, self._S["password_input"], password)
        await think_pause()
        await human_click(self._page, self._page.locator(self._S["login_button"]).first)
        await random_sleep(3000, 6000)

        if await self.is_element_visible(self._S["otp_input"], timeout=5000) and fa_secret:
            code = pyotp.TOTP(fa_secret).now()
            await human_type(self._page, self._S["otp_input"], code)
            await human_click(self._page, self._page.locator(self._S["next_button"]).first)
            await random_sleep(3000, 5000)

        if await self.is_element_visible(self._S["home_link"], timeout=10_000):
            log.info("twitter_login_success", account=self._account_id)
            return LoginStatus.LOGGED_IN

        log.error("twitter_login_failed", account=self._account_id)
        return LoginStatus.LOGGED_OUT

    async def post_text(self, text: str) -> PostResult:
        try:
            await self.navigate(f"{self.base_url}/compose/tweet")
            await random_sleep(2000, 3500)
            await human_type(self._page, self._S["tweet_box"], text)
            await random_sleep(1000, 2000)
            tweet_btn = self._page.locator(self._S["tweet_button"]).last
            await human_click(self._page, tweet_btn)
            await self._page.wait_for_selector('[data-testid="toast"]', timeout=15_000)
            return PostResult(success=True, platform=self.platform_name)
        except Exception as exc:
            return PostResult(success=False, platform=self.platform_name, error=str(exc))

    async def post_image(self, image_path: str, caption: str = "") -> PostResult:
        try:
            await self.navigate(f"{self.base_url}/compose/tweet")
            await random_sleep(2000, 3500)

            async with self._page.expect_file_chooser() as fc_info:
                await self._page.locator(self._S["file_input"]).click()
            file_chooser = await fc_info.value
            await file_chooser.set_files(image_path)
            await random_sleep(2000, 4000)

            if caption:
                await human_type(self._page, self._S["tweet_box"], caption)
                await random_sleep(500, 1500)

            tweet_btn = self._page.locator(self._S["tweet_button"]).last
            await human_click(self._page, tweet_btn)
            await self._page.wait_for_selector('[data-testid="toast"]', timeout=15_000)
            return PostResult(success=True, platform=self.platform_name)
        except Exception as exc:
            return PostResult(success=False, platform=self.platform_name, error=str(exc))

    async def like_post(self, post_url: str) -> EngagementResult:
        try:
            await self.navigate(post_url)
            await random_sleep(2000, 4000)
            like_btn = self._page.locator(self._S["like_button"]).first
            await human_click(self._page, like_btn)
            await random_sleep(500, 1500)
            return EngagementResult("like", post_url, True)
        except Exception as exc:
            return EngagementResult("like", post_url, False, str(exc))

    async def follow_user(self, username: str) -> EngagementResult:
        try:
            await self.navigate(f"{self.base_url}/{username}")
            await random_sleep(2000, 4000)
            follow_btn = self._page.locator(self._S["follow_button"]).first
            await human_click(self._page, follow_btn)
            await random_sleep(1000, 2500)
            return EngagementResult("follow", username, True)
        except Exception as exc:
            return EngagementResult("follow", username, False, str(exc))

    async def comment_on_post(self, post_url: str, comment: str) -> EngagementResult:
        try:
            await self.navigate(post_url)
            await random_sleep(2000, 4000)
            reply_btn = self._page.locator(self._S["reply_button"]).first
            await human_click(self._page, reply_btn)
            await random_sleep(1000, 2000)
            await human_type(self._page, self._S["comment_box"], comment)
            tweet_btn = self._page.locator(self._S["tweet_button"]).last
            await human_click(self._page, tweet_btn)
            await random_sleep(1500, 3000)
            return EngagementResult("comment", post_url, True)
        except Exception as exc:
            return EngagementResult("comment", post_url, False, str(exc))

    async def get_account_stats(self) -> AccountStats:
        try:
            await self.navigate(f"{self.base_url}/{self._account_id}")
            await random_sleep(2000, 4000)
            followers = self._page.locator('a[href$="/followers"] span').first
            following = self._page.locator('a[href$="/following"] span').first
            return AccountStats(
                followers=_parse_number(await followers.text_content() or "0"),
                following=_parse_number(await following.text_content() or "0"),
            )
        except Exception:
            return AccountStats()


# ===========================================================================
# Facebook
# ===========================================================================

class Facebook(BasePlatform):
    platform_name = "facebook"
    base_url = "https://www.facebook.com"

    _S = {
        "email_input":     "#email",
        "password_input":  "#pass",
        "login_button":    '[name="login"]',
        "home_indicator":  '[aria-label="Facebook"]',
        "create_post_box": '[aria-label="Create a post"], [placeholder*="What"], [placeholder*="what"]',
        "post_textarea":   'div[role="textbox"][contenteditable="true"]',
        "post_button":     '[aria-label="Post"], button:has-text("Post")',
        "photo_video_btn": '[aria-label="Photo/video"]',
        "file_input":      'input[type="file"][accept*="image"]',
        "like_button":     '[aria-label="Like"], [data-testid="like-reaction-count"]',
        "follow_button":   'div[aria-label="Follow"], button:has-text("Follow")',
        "comment_input":   'div[aria-label="Write a comment…"]',
        "comment_submit":  '[aria-label="Comment"]',
        "checkpoint":      ':text("checkpoint"), :text("suspicious activity")',
    }

    async def check_login_status(self) -> LoginStatus:
        await self.navigate(self.base_url)
        await random_sleep(2000, 4000)
        if await self.is_element_visible(self._S["home_indicator"], timeout=8000):
            return LoginStatus.LOGGED_IN
        if await self.is_element_visible(self._S["checkpoint"], timeout=3000):
            return LoginStatus.CHECKPOINT
        if await self.is_element_visible(self._S["email_input"], timeout=5000):
            return LoginStatus.LOGGED_OUT
        return LoginStatus.UNKNOWN

    async def login(self, username: str, password: str, fa_secret: str = "") -> LoginStatus:
        await self.navigate(f"{self.base_url}/login/")
        await random_sleep(2000, 4000)
        await human_type(self._page, self._S["email_input"], username)
        await random_sleep(500, 1000)
        await human_type(self._page, self._S["password_input"], password)
        await think_pause()
        await human_click(self._page, self._page.locator(self._S["login_button"]).first)
        await random_sleep(3000, 6000)

        if await self.is_element_visible(self._S["checkpoint"], timeout=5000):
            return LoginStatus.CHECKPOINT
        if await self.is_element_visible(self._S["home_indicator"], timeout=10_000):
            return LoginStatus.LOGGED_IN
        return LoginStatus.LOGGED_OUT

    async def post_text(self, text: str) -> PostResult:
        try:
            await self.navigate(self.base_url)
            await random_sleep(2000, 4000)
            create_box = self._page.locator(self._S["create_post_box"]).first
            await human_click(self._page, create_box)
            await random_sleep(1500, 3000)
            await human_type(self._page, self._S["post_textarea"], text)
            await random_sleep(1000, 2500)
            post_btn = self._page.locator(self._S["post_button"]).last
            await human_click(self._page, post_btn)
            await random_sleep(3000, 5000)
            return PostResult(success=True, platform=self.platform_name)
        except Exception as exc:
            return PostResult(success=False, platform=self.platform_name, error=str(exc))

    async def post_image(self, image_path: str, caption: str = "") -> PostResult:
        try:
            await self.navigate(self.base_url)
            await random_sleep(2000, 4000)
            create_box = self._page.locator(self._S["create_post_box"]).first
            await human_click(self._page, create_box)
            await random_sleep(1500, 3000)

            photo_btn = self._page.locator(self._S["photo_video_btn"]).first
            await human_click(self._page, photo_btn)
            await random_sleep(1000, 2000)

            async with self._page.expect_file_chooser() as fc_info:
                await self._page.locator(self._S["file_input"]).click()
            fc = await fc_info.value
            await fc.set_files(image_path)
            await random_sleep(2000, 4000)

            if caption:
                await human_type(self._page, self._S["post_textarea"], caption)
                await random_sleep(1000, 2000)

            post_btn = self._page.locator(self._S["post_button"]).last
            await human_click(self._page, post_btn)
            await random_sleep(3000, 5000)
            return PostResult(success=True, platform=self.platform_name)
        except Exception as exc:
            return PostResult(success=False, platform=self.platform_name, error=str(exc))

    async def like_post(self, post_url: str) -> EngagementResult:
        try:
            await self.navigate(post_url)
            await random_sleep(2000, 4500)
            like_btn = self._page.locator(self._S["like_button"]).first
            await human_click(self._page, like_btn)
            await random_sleep(800, 2000)
            return EngagementResult("like", post_url, True)
        except Exception as exc:
            return EngagementResult("like", post_url, False, str(exc))

    async def follow_user(self, username: str) -> EngagementResult:
        try:
            await self.navigate(f"{self.base_url}/{username}")
            await random_sleep(2000, 4500)
            follow_btn = self._page.locator(self._S["follow_button"]).first
            await human_click(self._page, follow_btn)
            await random_sleep(1000, 2500)
            return EngagementResult("follow", username, True)
        except Exception as exc:
            return EngagementResult("follow", username, False, str(exc))

    async def comment_on_post(self, post_url: str, comment: str) -> EngagementResult:
        try:
            await self.navigate(post_url)
            await random_sleep(2000, 4000)
            await reading_pause(25)
            comment_box = self._page.locator(self._S["comment_input"]).first
            await human_click(self._page, comment_box)
            await human_type(self._page, self._S["comment_input"], comment)
            await self._page.keyboard.press("Enter")
            await random_sleep(1500, 3000)
            return EngagementResult("comment", post_url, True)
        except Exception as exc:
            return EngagementResult("comment", post_url, False, str(exc))

    async def get_account_stats(self) -> AccountStats:
        return AccountStats()  # FB restricts programmatic access; parse manually if needed


# ===========================================================================
# TikTok
# ===========================================================================

class TikTok(BasePlatform):
    platform_name = "tiktok"
    base_url = "https://www.tiktok.com"

    _S = {
        "login_button":    'a[href="/login"], button:has-text("Log in")',
        "email_tab":       ':text("Use phone / email / username")',
        "email_input":     'input[name="username"]',
        "password_input":  'input[placeholder="Password"]',
        "submit_button":   'button[type="submit"], button:has-text("Log in"):last-child',
        "home_indicator":  '[data-e2e="nav-profile"], [data-e2e="profile-icon"]',
        "upload_btn":      '[data-e2e="upload-btn"], a[href*="/upload"]',
        "caption_input":   '[data-text="true"], div[contenteditable="true"]',
        "post_button":     'button:has-text("Post")',
        "like_button":     '[data-e2e="like-icon"], [data-e2e="browse-like-icon"]',
        "comment_input":   '[data-e2e="comment-input"]',
        "comment_send":    '[data-e2e="comment-post"]',
        "follow_button":   '[data-e2e="follow-button"]:not([data-e2e="unfollow-button"])',
    }

    async def check_login_status(self) -> LoginStatus:
        await self.navigate(self.base_url)
        await random_sleep(2000, 4000)
        if await self.is_element_visible(self._S["home_indicator"], timeout=8000):
            return LoginStatus.LOGGED_IN
        if await self.is_element_visible(self._S["login_button"], timeout=5000):
            return LoginStatus.LOGGED_OUT
        return LoginStatus.UNKNOWN

    async def login(self, username: str, password: str, fa_secret: str = "") -> LoginStatus:
        await self.navigate(f"{self.base_url}/login")
        await random_sleep(2000, 4500)

        # Choose email/username login method
        email_tab = self._page.locator(self._S["email_tab"]).first
        if await email_tab.is_visible():
            await human_click(self._page, email_tab)
            await random_sleep(1000, 2000)

        await human_type(self._page, self._S["email_input"], username)
        await random_sleep(500, 1200)
        await human_type(self._page, self._S["password_input"], password)
        await think_pause()
        await human_click(self._page, self._page.locator(self._S["submit_button"]).last)
        await random_sleep(4000, 7000)

        if await self.is_element_visible(self._S["home_indicator"], timeout=12_000):
            log.info("tiktok_login_success", account=self._account_id)
            return LoginStatus.LOGGED_IN

        log.error("tiktok_login_failed", account=self._account_id)
        return LoginStatus.LOGGED_OUT

    async def post_text(self, text: str) -> PostResult:
        """TikTok is video-first; text-only captions aren't standalone posts."""
        return PostResult(
            success=False, platform=self.platform_name,
            error="TikTok does not support text-only posts. Use post_image() with a video file."
        )

    async def post_image(self, image_path: str, caption: str = "") -> PostResult:
        """Works for both image and video uploads."""
        try:
            await self.navigate(f"{self.base_url}/upload")
            await random_sleep(2000, 4000)

            async with self._page.expect_file_chooser() as fc_info:
                await self._page.locator('input[type="file"]').first.click()
            fc = await fc_info.value
            await fc.set_files(image_path)
            await random_sleep(4000, 8000)  # Encoding takes time

            if caption:
                cap_box = self._page.locator(self._S["caption_input"]).first
                await cap_box.click()
                await human_type(self._page, self._S["caption_input"], caption)
                await random_sleep(1000, 2000)

            post_btn = self._page.locator(self._S["post_button"]).last
            await human_click(self._page, post_btn)
            await random_sleep(3000, 6000)
            return PostResult(success=True, platform=self.platform_name)
        except Exception as exc:
            return PostResult(success=False, platform=self.platform_name, error=str(exc))

    async def like_post(self, post_url: str) -> EngagementResult:
        try:
            await self.navigate(post_url)
            await random_sleep(3000, 6000)
            like_btn = self._page.locator(self._S["like_button"]).first
            await human_click(self._page, like_btn)
            await random_sleep(500, 1500)
            return EngagementResult("like", post_url, True)
        except Exception as exc:
            return EngagementResult("like", post_url, False, str(exc))

    async def follow_user(self, username: str) -> EngagementResult:
        try:
            await self.navigate(f"{self.base_url}/@{username}")
            await random_sleep(2000, 5000)
            follow_btn = self._page.locator(self._S["follow_button"]).first
            await human_click(self._page, follow_btn)
            await random_sleep(1000, 2500)
            return EngagementResult("follow", username, True)
        except Exception as exc:
            return EngagementResult("follow", username, False, str(exc))

    async def comment_on_post(self, post_url: str, comment: str) -> EngagementResult:
        try:
            await self.navigate(post_url)
            await random_sleep(3000, 6000)
            await reading_pause(15)
            comment_input = self._page.locator(self._S["comment_input"]).first
            await human_click(self._page, comment_input)
            await human_type(self._page, self._S["comment_input"], comment)
            await human_click(self._page, self._page.locator(self._S["comment_send"]).first)
            await random_sleep(1500, 3000)
            return EngagementResult("comment", post_url, True)
        except Exception as exc:
            return EngagementResult("comment", post_url, False, str(exc))

    async def get_account_stats(self) -> AccountStats:
        try:
            await self.navigate(f"{self.base_url}/@{self._account_id}")
            await random_sleep(2000, 4000)
            followers = await self._page.locator('[data-e2e="followers-count"]').first.text_content()
            following = await self._page.locator('[data-e2e="following-count"]').first.text_content()
            likes = await self._page.locator('[data-e2e="likes-count"]').first.text_content()
            return AccountStats(
                followers=_parse_number(followers or "0"),
                following=_parse_number(following or "0"),
                raw={"likes": _parse_number(likes or "0")},
            )
        except Exception:
            return AccountStats()


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _parse_number(text: str) -> int:
    """Parse '1.2K', '3.4M', '12,345' → int."""
    text = re.sub(r"[,\s]", "", text.strip())
    if text.endswith("K"):
        return int(float(text[:-1]) * 1_000)
    if text.endswith("M"):
        return int(float(text[:-1]) * 1_000_000)
    try:
        return int(float(text))
    except ValueError:
        return 0
