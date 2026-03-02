"""
platforms/instagram.py — Instagram automation via Playwright.

Selectors are based on Instagram's web app as of 2024-Q1.
Instagram frequently updates its DOM — if actions break, update selectors here.
All actions use human.py helpers (realistic mouse, typing, delays).
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pyotp
from playwright.async_api import TimeoutError as PWTimeout

from platforms.base import (
    AccountStats,
    BasePlatform,
    EngagementResult,
    LoginStatus,
    PostResult,
)
from utils.human import (
    human_click,
    human_scroll,
    human_type,
    random_sleep,
    reading_pause,
    simulate_idle,
    think_pause,
)
from utils.logger import get_logger

if TYPE_CHECKING:
    from playwright.async_api import Page

log = get_logger(__name__)


class Instagram(BasePlatform):
    platform_name = "instagram"
    base_url = "https://www.instagram.com"

    # ------------------------------------------------------------------
    # Selectors (single place to update when IG changes its DOM)
    # ------------------------------------------------------------------
    _S = {
        # Login
        "username_input":   'input[name="username"]',
        "password_input":   'input[name="password"]',
        "login_submit":     'button[type="submit"]',
        "otp_input":        'input[name="verificationCode"]',
        # Login success indicators
        "home_icon":        'svg[aria-label="Home"]',
        "nav_home":         'a[href="/"]',
        # Post
        "create_button":    'svg[aria-label="New post"]',
        "file_input":       'input[type="file"]',
        "next_button":      'div[role="button"]:has-text("Next")',
        "caption_input":    'div[aria-label="Write a caption..."]',
        "share_button":     'div[role="button"]:has-text("Share")',
        # Engagement
        "like_button":      'svg[aria-label="Like"]',
        "unlike_button":    'svg[aria-label="Unlike"]',
        "comment_input":    'textarea[aria-label="Add a comment…"]',
        "post_comment":     'div[role="button"]:has-text("Post")',
        "follow_button":    'button:has-text("Follow")',
        "following_button": 'button:has-text("Following")',
        # Checkpoint
        "checkpoint":       ':text("suspicious"), :text("verify"), :text("Verify")',
        "banned":           ':text("disabled"), :text("suspended")',
    }

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def check_login_status(self) -> LoginStatus:
        await self.navigate(self.base_url)
        await random_sleep(2000, 4000)

        if await self.is_element_visible(self._S["home_icon"], timeout=8000):
            return LoginStatus.LOGGED_IN
        if await self.is_element_visible(self._S["checkpoint"], timeout=3000):
            return LoginStatus.CHECKPOINT
        if await self.is_element_visible(self._S["banned"], timeout=3000):
            return LoginStatus.BANNED
        if await self.is_element_visible(self._S["username_input"], timeout=5000):
            return LoginStatus.LOGGED_OUT
        return LoginStatus.UNKNOWN

    async def login(
        self, username: str, password: str, fa_secret: str = ""
    ) -> LoginStatus:
        log.info("instagram_login_start", account=self._account_id)

        await self.navigate(f"{self.base_url}/accounts/login/")
        await random_sleep(2000, 4000)

        # Fill username
        await human_type(self._page, self._S["username_input"], username)
        await random_sleep(500, 1200)

        # Fill password
        await human_type(self._page, self._S["password_input"], password)
        await think_pause()

        # Submit
        submit = self._page.locator(self._S["login_submit"])
        await human_click(self._page, submit)

        # Wait for outcome
        outcome = await self.wait_for_any(
            [
                self._S["home_icon"],
                self._S["otp_input"],
                self._S["checkpoint"],
                self._S["banned"],
                self._S["username_input"],  # still on login = failed
            ],
            timeout=20_000,
        )

        if outcome == self._S["otp_input"] and fa_secret:
            return await self._handle_2fa(fa_secret)

        if outcome in (self._S["home_icon"], self._S["nav_home"]):
            log.info("instagram_login_success", account=self._account_id)
            await self._dismiss_save_login_prompt()
            return LoginStatus.LOGGED_IN

        if outcome == self._S["checkpoint"]:
            log.warning("instagram_checkpoint", account=self._account_id)
            return LoginStatus.CHECKPOINT

        if outcome == self._S["banned"]:
            log.error("instagram_banned", account=self._account_id)
            return LoginStatus.BANNED

        log.error("instagram_login_failed", account=self._account_id)
        return LoginStatus.LOGGED_OUT

    async def _handle_2fa(self, fa_secret: str) -> LoginStatus:
        totp = pyotp.TOTP(fa_secret)
        code = totp.now()
        await human_type(self._page, self._S["otp_input"], code)
        confirm = self._page.locator('button:has-text("Confirm")')
        await human_click(self._page, confirm)
        await random_sleep(3000, 5000)

        if await self.is_element_visible(self._S["home_icon"], timeout=10_000):
            return LoginStatus.LOGGED_IN
        return LoginStatus.CHECKPOINT

    async def _dismiss_save_login_prompt(self) -> None:
        """Dismiss 'Save login info?' and 'Turn on notifications' dialogs."""
        for selector in [
            'button:has-text("Not Now")',
            'button:has-text("Not now")',
        ]:
            if await self.is_element_visible(selector, timeout=5000):
                await human_click(self._page, self._page.locator(selector).first)
                await random_sleep(1000, 2000)

    # ------------------------------------------------------------------
    # Posting
    # ------------------------------------------------------------------

    async def post_text(self, text: str) -> PostResult:
        """Instagram doesn't support pure text posts — use a blank image workaround."""
        log.warning("instagram_text_only_post_not_supported", account=self._account_id)
        return PostResult(
            success=False,
            platform=self.platform_name,
            error="Instagram requires an image/video for posts. Use post_image() instead.",
        )

    async def post_image(self, image_path: str, caption: str = "") -> PostResult:
        log.info("instagram_post_image_start", account=self._account_id)

        if not Path(image_path).exists():
            return PostResult(success=False, platform=self.platform_name,
                              error=f"Image not found: {image_path}")

        try:
            # Click create (+ icon)
            create = self._page.locator(self._S["create_button"]).first
            await human_click(self._page, create)
            await random_sleep(1500, 3000)

            # Upload file
            async with self._page.expect_file_chooser() as fc_info:
                upload_area = self._page.locator('label[for="sidecarFileInput"], input[type="file"]').first
                await upload_area.click()
            file_chooser = await fc_info.value
            await file_chooser.set_files(image_path)
            await random_sleep(2000, 4000)

            # Click through: Crop → Filter → Caption
            for step in range(2):
                next_btn = self._page.locator(self._S["next_button"]).last
                await human_click(self._page, next_btn)
                await random_sleep(1500, 3000)

            # Enter caption with human typing
            if caption:
                cap_box = self._page.locator(self._S["caption_input"]).first
                await cap_box.click()
                await reading_pause(len(caption.split()))
                await human_type(self._page, self._S["caption_input"], caption)
                await random_sleep(1000, 2500)

            # Share
            share_btn = self._page.locator(self._S["share_button"]).last
            await human_click(self._page, share_btn)

            # Wait for success indicator (post page or feed)
            await self._page.wait_for_selector(
                'article[role="presentation"], div[class*="_aear"]',
                timeout=30_000,
            )

            screenshot = await self.screenshot("post_success")
            log.info("instagram_post_success", account=self._account_id)
            return PostResult(success=True, platform=self.platform_name,
                              screenshot=screenshot)

        except Exception as exc:
            screenshot = await self.screenshot("post_error")
            return PostResult(success=False, platform=self.platform_name,
                              error=str(exc), screenshot=screenshot)

    async def post_story(self, media_path: str) -> PostResult:
        """Post a story (image or video ≤60 seconds)."""
        try:
            await self.navigate(f"{self.base_url}/stories/create/")
            await random_sleep(2000, 3500)

            async with self._page.expect_file_chooser() as fc_info:
                await self._page.locator('input[type="file"]').first.click()
            file_chooser = await fc_info.value
            await file_chooser.set_files(media_path)
            await random_sleep(2000, 4000)

            # Share to story
            share_btn = self._page.locator('button:has-text("Share to story"), button:has-text("Add to story")')
            await human_click(self._page, share_btn.first)
            await random_sleep(2000, 4000)

            return PostResult(success=True, platform=self.platform_name)
        except Exception as exc:
            return PostResult(success=False, platform=self.platform_name, error=str(exc))

    # ------------------------------------------------------------------
    # Engagement
    # ------------------------------------------------------------------

    async def like_post(self, post_url: str) -> EngagementResult:
        try:
            await self.navigate(post_url)
            await random_sleep(2000, 4000)
            await human_scroll(self._page, "down", 100)

            like_btn = self._page.locator(self._S["like_button"]).first
            if not await like_btn.is_visible():
                return EngagementResult("like", post_url, False, "Like button not found")

            already_liked = await self.is_element_visible(self._S["unlike_button"], timeout=2000)
            if already_liked:
                return EngagementResult("like", post_url, True, "Already liked")

            await human_click(self._page, like_btn)
            await random_sleep(500, 1500)
            return EngagementResult("like", post_url, True)
        except Exception as exc:
            return EngagementResult("like", post_url, False, str(exc))

    async def follow_user(self, username: str) -> EngagementResult:
        try:
            await self.navigate(f"{self.base_url}/{username}/")
            await random_sleep(2000, 4500)

            # Already following?
            if await self.is_element_visible(self._S["following_button"], timeout=3000):
                return EngagementResult("follow", username, True, "Already following")

            follow_btn = self._page.locator(self._S["follow_button"]).first
            await human_click(self._page, follow_btn)
            await random_sleep(1000, 2500)
            return EngagementResult("follow", username, True)
        except Exception as exc:
            return EngagementResult("follow", username, False, str(exc))

    async def unfollow_user(self, username: str) -> EngagementResult:
        try:
            await self.navigate(f"{self.base_url}/{username}/")
            await random_sleep(2000, 4000)
            following_btn = self._page.locator(self._S["following_button"]).first
            await human_click(self._page, following_btn)
            await random_sleep(500, 1500)
            # Confirm unfollow in dialog
            unfollow_confirm = self._page.locator('button:has-text("Unfollow")').last
            await human_click(self._page, unfollow_confirm)
            await random_sleep(1000, 2000)
            return EngagementResult("unfollow", username, True)
        except Exception as exc:
            return EngagementResult("unfollow", username, False, str(exc))

    async def comment_on_post(self, post_url: str, comment: str) -> EngagementResult:
        try:
            await self.navigate(post_url)
            await random_sleep(2000, 4000)
            await reading_pause(20)

            comment_box = self._page.locator(self._S["comment_input"]).first
            await comment_box.click()
            await human_type(self._page, self._S["comment_input"], comment)
            await random_sleep(800, 1800)

            post_btn = self._page.locator(self._S["post_comment"]).first
            await human_click(self._page, post_btn)
            await random_sleep(1500, 3000)
            return EngagementResult("comment", post_url, True)
        except Exception as exc:
            return EngagementResult("comment", post_url, False, str(exc))

    async def view_story(self, username: str) -> EngagementResult:
        """View all stories for a user (natural swipe-through behavior)."""
        try:
            await self.navigate(f"{self.base_url}/stories/{username}/")
            await random_sleep(2000, 4000)

            viewed = 0
            while True:
                await random_sleep(3000, 8000)  # Reading time
                next_btn = self._page.locator('[aria-label="Next"], button[class*="next"]')
                if not await next_btn.is_visible():
                    break
                await human_click(self._page, next_btn.first)
                viewed += 1
                if viewed > 15:
                    break

            return EngagementResult("story_view", username, True)
        except Exception as exc:
            return EngagementResult("story_view", username, False, str(exc))

    # ------------------------------------------------------------------
    # Account stats
    # ------------------------------------------------------------------

    async def get_account_stats(self) -> AccountStats:
        try:
            await self.navigate(f"{self.base_url}/{self._account_id}/")
            await random_sleep(2000, 4000)

            stats_text = await self._page.locator('ul[class*="_aa_8"]').all_text_contents()
            # Parse "1,234 posts", "5.6K followers", etc.
            numbers = re.findall(r"[\d,.]+K?M?", " ".join(stats_text))
            parsed = [_parse_ig_number(n) for n in numbers[:3]]

            return AccountStats(
                posts=parsed[0] if len(parsed) > 0 else 0,
                followers=parsed[1] if len(parsed) > 1 else 0,
                following=parsed[2] if len(parsed) > 2 else 0,
            )
        except Exception as exc:
            log.warning("instagram_stats_failed", error=str(exc))
            return AccountStats()


def _parse_ig_number(text: str) -> int:
    """Convert '12.5K' / '1,234' / '2M' to int."""
    text = text.replace(",", "").strip()
    if text.endswith("K"):
        return int(float(text[:-1]) * 1_000)
    if text.endswith("M"):
        return int(float(text[:-1]) * 1_000_000)
    try:
        return int(float(text))
    except ValueError:
        return 0
