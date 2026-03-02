"""
utils/human.py — Simulate realistic human interaction patterns.
Every method here is async-safe and Playwright-compatible.
"""
from __future__ import annotations

import asyncio
import math
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page, Locator


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------

async def random_sleep(min_ms: float = 500, max_ms: float = 2000) -> None:
    """Async sleep for a random duration within [min_ms, max_ms]."""
    await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)


async def think_pause() -> None:
    """Simulate a human 'thinking' pause (1–4 seconds)."""
    await random_sleep(1000, 4000)


async def reading_pause(word_count: int) -> None:
    """Pause proportional to how long a human would read `word_count` words."""
    # Average reading speed ~200 wpm = ~3.3 words/sec
    base_seconds = word_count / 3.3
    jitter = random.uniform(0.7, 1.4)
    await asyncio.sleep(base_seconds * jitter)


# ---------------------------------------------------------------------------
# Typing
# ---------------------------------------------------------------------------

async def human_type(page: "Page", selector: str, text: str) -> None:
    """
    Type text character-by-character with realistic variable speed.
    Occasionally pauses mid-word as a real typist would.
    """
    locator = page.locator(selector).first
    await locator.click()
    await random_sleep(200, 600)

    for i, char in enumerate(text):
        # Occasional 'thinking' pause mid-sentence
        if char == " " and random.random() < 0.04:
            await random_sleep(400, 1200)

        delay = _typing_delay(char, i, len(text))
        await page.keyboard.type(char, delay=delay)

    await random_sleep(100, 300)


def _typing_delay(char: str, position: int, total: int) -> float:
    """Return per-keystroke delay in ms using a log-normal distribution."""
    base = random.lognormvariate(mu=4.2, sigma=0.5)  # ~67ms median
    # Slightly slower at start and end of text
    edge_factor = 1.0
    if position < 3 or position > total - 3:
        edge_factor = 1.3
    # Punctuation is slightly slower
    if char in ".,!?;:'\"":
        edge_factor *= 1.2
    return max(30.0, min(base * edge_factor, 350.0))


# ---------------------------------------------------------------------------
# Mouse movement
# ---------------------------------------------------------------------------

async def move_to(page: "Page", x: float, y: float) -> None:
    """Move mouse along a Bézier curve — not a straight line."""
    current = await _get_mouse_pos(page)
    points = _bezier_path(current, (x, y), steps=random.randint(20, 40))
    for px, py in points:
        await page.mouse.move(px, py)
        await asyncio.sleep(random.uniform(0.005, 0.015))


async def human_click(page: "Page", locator: "Locator") -> None:
    """Move to element naturally then click with a small random offset."""
    box = await locator.bounding_box()
    if not box:
        await locator.click()
        return

    # Target slightly off-center
    target_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)

    await move_to(page, target_x, target_y)
    await random_sleep(50, 200)
    await page.mouse.click(target_x, target_y)
    await random_sleep(100, 400)


async def human_scroll(page: "Page", direction: str = "down",
                       pixels: int | None = None) -> None:
    """Scroll in small, irregular increments like a real user."""
    if pixels is None:
        pixels = random.randint(300, 900)

    sign = 1 if direction == "down" else -1
    remaining = pixels

    while remaining > 0:
        chunk = random.randint(80, 200)
        chunk = min(chunk, remaining)
        await page.mouse.wheel(0, sign * chunk)
        remaining -= chunk
        await random_sleep(100, 500)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _get_mouse_pos(page: "Page") -> tuple[float, float]:
    pos = await page.evaluate("() => ({x: window._mouseX || 0, y: window._mouseY || 0})")
    return pos.get("x", 0), pos.get("y", 0)


def _bezier_path(start: tuple[float, float], end: tuple[float, float],
                 steps: int = 30) -> list[tuple[float, float]]:
    """Generate a quadratic Bézier curve from start to end."""
    sx, sy = start
    ex, ey = end

    # Random control point for the curve
    cx = (sx + ex) / 2 + random.uniform(-100, 100)
    cy = (sy + ey) / 2 + random.uniform(-100, 100)

    path = []
    for i in range(steps + 1):
        t = i / steps
        # Quadratic Bézier formula
        x = (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t ** 2 * ex
        y = (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t ** 2 * ey
        # Add tiny noise to each point
        x += random.gauss(0, 1.5)
        y += random.gauss(0, 1.5)
        path.append((x, y))
    return path


# ---------------------------------------------------------------------------
# Session behavior
# ---------------------------------------------------------------------------

async def simulate_idle(page: "Page", seconds: float = 5.0) -> None:
    """
    Simulate an idle period — random small mouse movements, occasional scroll.
    Prevents bot detection from detecting a perfectly static browser.
    """
    end = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < end:
        action = random.choices(
            ["idle", "micro_move", "scroll"],
            weights=[0.6, 0.3, 0.1],
        )[0]

        if action == "micro_move":
            vp = page.viewport_size or {"width": 1280, "height": 720}
            await page.mouse.move(
                random.uniform(100, vp["width"] - 100),
                random.uniform(100, vp["height"] - 100),
            )
        elif action == "scroll":
            direction = random.choice(["down", "up"])
            await human_scroll(page, direction, random.randint(50, 200))

        await random_sleep(500, 3000)
