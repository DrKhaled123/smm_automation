"""
workflows/warming.py — Progressive account warming over 7–28 days.

New accounts that immediately post or mass-follow get flagged instantly.
This workflow ramps up activity gradually, mimicking organic human growth.

Schedule:
  Week 1: Browse only. No posting, minimal engagement.
  Week 2: Light engagement (likes, story views). Still no posting.
  Week 3: First posts. Conservative engagement.
  Week 4+: Normal operating mode.
"""
from __future__ import annotations

import asyncio
import json
import random
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from config.settings import settings
from utils.human import human_scroll, random_sleep, simulate_idle
from utils.logger import get_logger
from utils.rate_limiter import RateLimiter

if TYPE_CHECKING:
    from platforms.base import BasePlatform

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Warm-up schedule definition
# ---------------------------------------------------------------------------

@dataclass
class DaySchedule:
    browse_minutes: int     # Idle browsing
    story_views: int        # Stories to view
    like_probability: float # 0–1 chance to like each post seen
    comment_probability: float
    follow_count: int
    post_count: int         # 0 during warming-in, ≥1 after

    # Max actions per session (safety cap even within limits)
    max_likes: int = 0
    max_follows: int = 0
    max_comments: int = 0


# 28-day ramp: indexed by day number (1-based)
WARM_UP_SCHEDULE: dict[int, DaySchedule] = {
    # Week 1: Observe only
    1:  DaySchedule(browse_minutes=5,  story_views=3,  like_probability=0.0,  comment_probability=0.0, follow_count=0, post_count=0),
    2:  DaySchedule(browse_minutes=8,  story_views=5,  like_probability=0.0,  comment_probability=0.0, follow_count=0, post_count=0),
    3:  DaySchedule(browse_minutes=10, story_views=8,  like_probability=0.05, comment_probability=0.0, follow_count=0, post_count=0, max_likes=3),
    4:  DaySchedule(browse_minutes=12, story_views=10, like_probability=0.1,  comment_probability=0.0, follow_count=0, post_count=0, max_likes=5),
    5:  DaySchedule(browse_minutes=15, story_views=12, like_probability=0.15, comment_probability=0.0, follow_count=1, post_count=0, max_likes=8,  max_follows=1),
    6:  DaySchedule(browse_minutes=18, story_views=15, like_probability=0.2,  comment_probability=0.0, follow_count=2, post_count=0, max_likes=10, max_follows=2),
    7:  DaySchedule(browse_minutes=20, story_views=18, like_probability=0.25, comment_probability=0.0, follow_count=3, post_count=0, max_likes=15, max_follows=3),
    # Week 2: Light engagement
    8:  DaySchedule(browse_minutes=20, story_views=20, like_probability=0.3,  comment_probability=0.02, follow_count=5,  post_count=0, max_likes=20, max_follows=5,  max_comments=1),
    9:  DaySchedule(browse_minutes=25, story_views=25, like_probability=0.35, comment_probability=0.03, follow_count=7,  post_count=0, max_likes=25, max_follows=7,  max_comments=2),
    10: DaySchedule(browse_minutes=25, story_views=30, like_probability=0.4,  comment_probability=0.04, follow_count=10, post_count=0, max_likes=30, max_follows=10, max_comments=3),
    11: DaySchedule(browse_minutes=30, story_views=30, like_probability=0.4,  comment_probability=0.05, follow_count=12, post_count=0, max_likes=35, max_follows=12, max_comments=4),
    12: DaySchedule(browse_minutes=30, story_views=35, like_probability=0.45, comment_probability=0.05, follow_count=15, post_count=0, max_likes=40, max_follows=15, max_comments=5),
    13: DaySchedule(browse_minutes=35, story_views=35, like_probability=0.5,  comment_probability=0.06, follow_count=15, post_count=0, max_likes=45, max_follows=15, max_comments=5),
    14: DaySchedule(browse_minutes=35, story_views=40, like_probability=0.5,  comment_probability=0.08, follow_count=20, post_count=0, max_likes=50, max_follows=20, max_comments=6),
    # Week 3: First posts
    15: DaySchedule(browse_minutes=30, story_views=40, like_probability=0.55, comment_probability=0.08, follow_count=20, post_count=1, max_likes=55, max_follows=20, max_comments=7),
    16: DaySchedule(browse_minutes=30, story_views=45, like_probability=0.6,  comment_probability=0.1,  follow_count=25, post_count=1, max_likes=60, max_follows=25, max_comments=8),
    21: DaySchedule(browse_minutes=30, story_views=50, like_probability=0.65, comment_probability=0.12, follow_count=30, post_count=2, max_likes=80, max_follows=30, max_comments=15),
    28: DaySchedule(browse_minutes=30, story_views=60, like_probability=0.7,  comment_probability=0.15, follow_count=40, post_count=3, max_likes=100,max_follows=40, max_comments=20),
}


def _get_schedule(day: int) -> DaySchedule:
    """Return the schedule for a given day, falling back to nearest defined day."""
    if day in WARM_UP_SCHEDULE:
        return WARM_UP_SCHEDULE[day]
    # Use the nearest lower defined day
    candidates = [d for d in sorted(WARM_UP_SCHEDULE) if d <= day]
    if candidates:
        return WARM_UP_SCHEDULE[candidates[-1]]
    return WARM_UP_SCHEDULE[1]


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

@dataclass
class WarmingState:
    account_id: str
    start_date: str             # ISO date
    current_day: int = 1
    completed: bool = False
    total_likes: int = 0
    total_follows: int = 0
    total_posts: int = 0
    notes: list[str] = None

    def __post_init__(self):
        if self.notes is None:
            self.notes = []

    @property
    def elapsed_days(self) -> int:
        start = date.fromisoformat(self.start_date)
        return (date.today() - start).days + 1


def load_warming_state(account_id: str) -> WarmingState:
    path = settings.data_dir / f"warming_{account_id}.json"
    if path.exists():
        data = json.loads(path.read_text())
        return WarmingState(**data)
    return WarmingState(account_id=account_id, start_date=date.today().isoformat())


def save_warming_state(state: WarmingState) -> None:
    path = settings.data_dir / f"warming_{state.account_id}.json"
    path.write_text(json.dumps(asdict(state), indent=2))


# ---------------------------------------------------------------------------
# Warming workflow
# ---------------------------------------------------------------------------

class AccountWarmer:
    """
    Executes daily warming sessions for a given account.

    Usage:
        warmer = AccountWarmer(platform, account_id, rate_limiter)
        await warmer.run_daily_session(feed_urls, target_users, comment_templates)
    """

    def __init__(
        self,
        platform: "BasePlatform",
        account_id: str,
        rate_limiter: RateLimiter,
    ) -> None:
        self._platform = platform
        self._account_id = account_id
        self._rl = rate_limiter
        self._state = load_warming_state(account_id)

    @property
    def is_complete(self) -> bool:
        return self._state.elapsed_days > 28

    async def run_daily_session(
        self,
        feed_urls: list[str] | None = None,
        target_usernames: list[str] | None = None,
        comment_templates: list[str] | None = None,
        post_queue: list[dict] | None = None,
    ) -> dict:
        """
        Execute one day's worth of warming activities.
        Returns a summary dict.
        """
        day = self._state.elapsed_days
        schedule = _get_schedule(day)

        log.info(
            "warming_day_start",
            account=self._account_id,
            day=day,
            schedule=asdict(schedule),
        )

        summary = {
            "account": self._account_id,
            "day": day,
            "date": date.today().isoformat(),
            "likes": 0,
            "follows": 0,
            "comments": 0,
            "posts": 0,
            "stories_viewed": 0,
        }

        # 1. Browse feed
        await self._browse_feed(schedule.browse_minutes)

        # 2. View stories
        if target_usernames:
            story_targets = random.sample(
                target_usernames,
                min(schedule.story_views, len(target_usernames))
            )
            for username in story_targets:
                if hasattr(self._platform, "view_story"):
                    result = await self._platform.view_story(username)
                    if result.success:
                        summary["stories_viewed"] += 1
                await random_sleep(5000, 20_000)

        # 3. Like posts from feed
        likes_done = 0
        if feed_urls and schedule.max_likes > 0:
            random.shuffle(feed_urls)
            for url in feed_urls:
                if likes_done >= schedule.max_likes:
                    break
                if random.random() > schedule.like_probability:
                    continue
                if not await self._rl.can_perform(self._account_id, "like"):
                    break
                result = await self._platform.like_post(url)
                if result.success:
                    await self._rl.record(self._account_id, "like")
                    likes_done += 1
                    summary["likes"] += 1
                    self._state.total_likes += 1
                await random_sleep(10_000, 45_000)

        # 4. Follow users
        follows_done = 0
        if target_usernames and schedule.max_follows > 0:
            follow_targets = random.sample(
                target_usernames,
                min(schedule.follow_count, len(target_usernames))
            )
            for username in follow_targets:
                if follows_done >= schedule.max_follows:
                    break
                if not await self._rl.can_perform(self._account_id, "follow"):
                    break
                result = await self._platform.follow_user(username)
                if result.success:
                    await self._rl.record(self._account_id, "follow")
                    follows_done += 1
                    summary["follows"] += 1
                    self._state.total_follows += 1
                await random_sleep(20_000, 60_000)

        # 5. Comments (rare in early days)
        comments_done = 0
        templates = comment_templates or ["Great post!", "Love this 🔥", "Amazing!"]
        if feed_urls and schedule.max_comments > 0:
            for url in random.sample(feed_urls, min(5, len(feed_urls))):
                if comments_done >= schedule.max_comments:
                    break
                if random.random() > schedule.comment_probability:
                    continue
                if not await self._rl.can_perform(self._account_id, "comment"):
                    break
                comment = random.choice(templates)
                result = await self._platform.comment_on_post(url, comment)
                if result.success:
                    await self._rl.record(self._account_id, "comment")
                    comments_done += 1
                    summary["comments"] += 1
                await random_sleep(60_000, 180_000)

        # 6. Post content (week 3+)
        if schedule.post_count > 0 and post_queue:
            posts_to_do = post_queue[:schedule.post_count]
            for post_data in posts_to_do:
                if not await self._rl.can_perform(self._account_id, "post"):
                    break
                if post_data.get("image"):
                    result = await self._platform.post_image(
                        post_data["image"],
                        caption=post_data.get("caption", ""),
                    )
                else:
                    result = await self._platform.post_text(post_data.get("text", ""))

                if result.success:
                    await self._rl.record(self._account_id, "post")
                    summary["posts"] += 1
                    self._state.total_posts += 1
                await random_sleep(300_000, 600_000)  # 5-10 min between posts

        # Idle to simulate a natural end-of-session
        await simulate_idle(self._platform._page, seconds=random.uniform(30, 90))

        # Persist state
        save_warming_state(self._state)
        log.info("warming_day_complete", account=self._account_id, summary=summary)
        return summary

    async def _browse_feed(self, minutes: int) -> None:
        """Scroll through the feed for the given number of minutes."""
        from playwright.async_api import Page
        page: Page = self._platform._page
        end_time = asyncio.get_event_loop().time() + minutes * 60

        try:
            await self._platform.navigate(self._platform.base_url)
            await random_sleep(2000, 4000)
        except Exception:
            return

        while asyncio.get_event_loop().time() < end_time:
            scroll_px = random.randint(300, 800)
            await human_scroll(page, "down", scroll_px)

            # Pause to "read" content
            pause = random.uniform(3, 15)
            await asyncio.sleep(pause)

            # Occasionally scroll back up slightly
            if random.random() < 0.15:
                await human_scroll(page, "up", random.randint(100, 300))
                await asyncio.sleep(random.uniform(2, 5))
