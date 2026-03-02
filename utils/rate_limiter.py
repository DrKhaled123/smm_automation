"""
utils/rate_limiter.py — Redis-backed, per-account, per-action rate limiting.

Uses a sliding window counter so limits are accurate across concurrent coroutines
and across process restarts (Redis survives restarts, in-memory dicts do not).

Falls back gracefully to in-memory if Redis is unavailable.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Optional

from utils.logger import get_logger

log = get_logger(__name__)


class RateLimiter:
    """
    Async-safe rate limiter.

    Usage:
        limiter = RateLimiter(redis_client)
        if await limiter.can_perform("account_123", "like"):
            # do like
            await limiter.record("account_123", "like")
        else:
            wait = await limiter.seconds_until_allowed("account_123", "like")
            await asyncio.sleep(wait)
    """

    # Action → max allowed per 24-hour window
    DEFAULT_LIMITS: dict[str, int] = {
        "like": 120,
        "follow": 60,
        "unfollow": 60,
        "comment": 30,
        "post": 3,
        "story_view": 200,
        "dm": 20,
        "login": 5,
    }
    WINDOW = 86_400  # 24 hours in seconds

    def __init__(self, redis=None, custom_limits: dict[str, int] | None = None):
        self._redis = redis
        self._limits = {**self.DEFAULT_LIMITS, **(custom_limits or {})}
        # Fallback in-memory store: account → action → [timestamps]
        self._memory: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def can_perform(self, account_id: str, action: str) -> bool:
        limit = self._limits.get(action, 50)
        count = await self._get_count(account_id, action)
        return count < limit

    async def record(self, account_id: str, action: str) -> None:
        """Record that `action` was performed by `account_id` right now."""
        now = time.time()
        if self._redis:
            key = self._key(account_id, action)
            try:
                pipe = self._redis.pipeline()
                pipe.zadd(key, {str(now): now})           # add timestamp
                pipe.zremrangebyscore(key, 0, now - self.WINDOW)  # prune old
                pipe.expire(key, self.WINDOW + 60)
                await pipe.execute()
                return
            except Exception as exc:
                log.warning("redis_record_failed", error=str(exc), fallback="memory")

        async with self._lock:
            bucket = self._memory[account_id][action]
            bucket.append(now)
            # Prune expired
            cutoff = now - self.WINDOW
            self._memory[account_id][action] = [t for t in bucket if t > cutoff]

    async def seconds_until_allowed(self, account_id: str, action: str) -> float:
        """How many seconds until the account can perform this action again."""
        limit = self._limits.get(action, 50)
        if self._redis:
            key = self._key(account_id, action)
            try:
                now = time.time()
                # Get the oldest timestamp still in the window
                oldest = await self._redis.zrange(key, 0, 0, withscores=True)
                if oldest and await self._get_count(account_id, action) >= limit:
                    _, oldest_ts = oldest[0]
                    return max(0.0, oldest_ts + self.WINDOW - now)
            except Exception:
                pass

        async with self._lock:
            timestamps = self._memory[account_id][action]
            now = time.time()
            valid = sorted(t for t in timestamps if t > now - self.WINDOW)
            if len(valid) >= limit:
                return max(0.0, valid[0] + self.WINDOW - now)
        return 0.0

    async def get_usage(self, account_id: str) -> dict[str, int]:
        """Return current 24h usage for all tracked actions for `account_id`."""
        result = {}
        for action in self._limits:
            result[action] = await self._get_count(account_id, action)
        return result

    async def reset(self, account_id: str, action: str | None = None) -> None:
        """Reset counters — useful for testing or after account recovery."""
        if action:
            actions = [action]
        else:
            actions = list(self._limits.keys())

        if self._redis:
            keys = [self._key(account_id, a) for a in actions]
            try:
                await self._redis.delete(*keys)
            except Exception:
                pass

        async with self._lock:
            for a in actions:
                self._memory[account_id].pop(a, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_count(self, account_id: str, action: str) -> int:
        if self._redis:
            key = self._key(account_id, action)
            try:
                now = time.time()
                return await self._redis.zcount(key, now - self.WINDOW, "+inf")
            except Exception as exc:
                log.warning("redis_count_failed", error=str(exc), fallback="memory")

        async with self._lock:
            now = time.time()
            cutoff = now - self.WINDOW
            return sum(1 for t in self._memory[account_id][action] if t > cutoff)

    @staticmethod
    def _key(account_id: str, action: str) -> str:
        return f"smm:ratelimit:{account_id}:{action}"
