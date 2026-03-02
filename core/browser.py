"""
core/browser.py — Unified browser session manager.

Supports two modes:
  1. STEEL MODE: Steel Browser manages the CDP session (cloud or self-hosted).
     Playwright connects via WebSocket to Steel's Chromium instance.
  2. ADSPOWER MODE: AdsPower manages the browser profile.
     Playwright connects via the Playwright WS endpoint AdsPower exposes.

Both modes yield a standard Playwright `Page` object, so all automation
code above this layer is identical regardless of mode.

Cookie persistence is handled here so sessions survive restarts.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Literal, Optional

import httpx
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from config.settings import settings
from core.adspower import AdsPowerClient, BrowserStartResult
from core.proxy import ProxyManager, ProxySession
from utils.logger import get_logger

log = get_logger(__name__)

Mode = Literal["steel", "adspower"]


# ---------------------------------------------------------------------------
# Session context manager
# ---------------------------------------------------------------------------

@asynccontextmanager
async def browser_session(
    account_id: str,
    proxy_session: ProxySession,
    mode: Mode = "steel",
    adspower: AdsPowerClient | None = None,
    adspower_profile_id: str | None = None,
    headless: bool | None = None,
    viewport: dict | None = None,
    user_agent: str | None = None,
) -> AsyncGenerator[Page, None]:
    """
    Async context manager that yields a Playwright Page.

    On exit: saves cookies, releases browser resources.
    On exception: takes a debug screenshot before re-raising.

    Usage:
        async with browser_session("acct_123", proxy_sess) as page:
            await page.goto("https://instagram.com")
            ...
    """
    _headless = headless if headless is not None else settings.headless
    _viewport = viewport or {"width": 1280, "height": 800}

    async with async_playwright() as pw:
        if mode == "steel":
            ctx, page = await _steel_session(pw, proxy_session, _headless, _viewport, user_agent)
        elif mode == "adspower":
            if not adspower or not adspower_profile_id:
                raise ValueError("adspower client and profile_id required in adspower mode")
            ctx, page, _br_result = await _adspower_session(
                pw, adspower, adspower_profile_id, _headless, _viewport
            )
        else:
            raise ValueError(f"Unknown mode: {mode}")

        # Restore cookies from disk
        await _restore_cookies(ctx, account_id)

        try:
            yield page
            # Save updated cookies on clean exit
            await _persist_cookies(ctx, account_id)
        except Exception as exc:
            # Debug screenshot — essential for diagnosing selector breaks
            screenshot_path = (
                settings.screenshots_dir / f"{account_id}_error_{_ts()}.png"
            )
            try:
                await page.screenshot(path=str(screenshot_path), full_page=True)
                log.error(
                    "browser_session_error",
                    account=account_id,
                    screenshot=str(screenshot_path),
                    error=str(exc),
                )
            except Exception:
                pass
            raise
        finally:
            if mode == "adspower" and adspower and adspower_profile_id:
                try:
                    await adspower.stop_browser(adspower_profile_id)
                except Exception:
                    pass
            try:
                await ctx.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Steel Browser backend
# ---------------------------------------------------------------------------

async def _steel_session(
    pw: Playwright,
    proxy_session: ProxySession,
    headless: bool,
    viewport: dict,
    user_agent: str | None,
) -> tuple[BrowserContext, Page]:
    """
    Create a Steel Browser session then connect Playwright to it.
    """
    session_id = await _create_steel_session(proxy_session)

    # Steel exposes a Playwright-compatible WebSocket
    ws_url = f"{settings.steel.base_url.replace('http', 'ws')}/sessions/{session_id}"
    browser: Browser = await pw.chromium.connect(ws_url)

    ctx_kwargs: dict = {
        "viewport": viewport,
        "ignore_https_errors": True,
    }
    if user_agent:
        ctx_kwargs["user_agent"] = user_agent

    ctx = await browser.new_context(**ctx_kwargs)
    page = await ctx.new_page()

    # Inject stealth overrides into every page
    await _inject_stealth(page)

    return ctx, page


async def _create_steel_session(proxy_session: ProxySession) -> str:
    """POST to Steel Browser API to create a new managed session."""
    payload = {
        "proxyUrl": proxy_session.url,
        "blockAds": True,
        "solveCaptchas": False,   # integrate 2captcha/anticaptcha separately
        "timeout": settings.steel.session_timeout_ms,
    }
    headers = {}
    if settings.steel.api_key:
        headers["x-api-key"] = settings.steel.api_key

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{settings.steel.base_url}/v1/sessions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        session_id = data["id"]
        log.info("steel_session_created", session_id=session_id)
        return session_id


# ---------------------------------------------------------------------------
# AdsPower backend
# ---------------------------------------------------------------------------

async def _adspower_session(
    pw: Playwright,
    adspower: AdsPowerClient,
    profile_id: str,
    headless: bool,
    viewport: dict,
) -> tuple[BrowserContext, Page, BrowserStartResult]:
    """Start AdsPower browser and connect Playwright to it."""
    result = await adspower.start_browser(profile_id, headless=headless)

    if not result.playwright_ws:
        raise RuntimeError(
            f"AdsPower returned no Playwright WS endpoint for profile {profile_id}. "
            "Ensure AdsPower >= 3.x is installed."
        )

    browser: Browser = await pw.chromium.connect(result.playwright_ws)
    # AdsPower already created a context with its fingerprint settings
    # — get existing contexts rather than creating a new one
    contexts = browser.contexts
    if contexts:
        ctx = contexts[0]
    else:
        ctx = await browser.new_context(viewport=viewport)

    pages = ctx.pages
    page = pages[0] if pages else await ctx.new_page()

    await _inject_stealth(page)

    return ctx, page, result


# ---------------------------------------------------------------------------
# Stealth injection
# ---------------------------------------------------------------------------

async def _inject_stealth(page: Page) -> None:
    """
    Minimal JS stealth patches applied to every new page.
    These complement AdsPower/Steel fingerprinting — not replace them.
    """
    await page.add_init_script("""
        // Remove Playwright/CDP fingerprints
        delete Object.getPrototypeOf(navigator).webdriver;
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

        // Consistent plugin count
        Object.defineProperty(navigator, 'plugins', {
            get: () => { const p = [1,2,3,4,5]; p.refresh = () => {}; return p; }
        });

        // Consistent language
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });

        // Remove CDP-specific chrome runtime flag
        window.chrome = {
            app: { isInstalled: false },
            runtime: {},
            loadTimes: function() {},
            csi: function() {},
        };

        // Realistic permissions behaviour
        const origQuery = window.navigator.permissions.query.bind(
            window.navigator.permissions
        );
        window.navigator.permissions.query = (p) =>
            p.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : origQuery(p);
    """)

    # Track mouse position for human.py helpers
    await page.add_init_script("""
        document.addEventListener('mousemove', (e) => {
            window._mouseX = e.clientX;
            window._mouseY = e.clientY;
        });
    """)


# ---------------------------------------------------------------------------
# Cookie persistence
# ---------------------------------------------------------------------------

async def _persist_cookies(ctx: BrowserContext, account_id: str) -> None:
    cookies = await ctx.cookies()
    path = _cookie_path(account_id)
    path.write_text(json.dumps(cookies, indent=2))
    log.debug("cookies_saved", account=account_id, count=len(cookies))


async def _restore_cookies(ctx: BrowserContext, account_id: str) -> None:
    path = _cookie_path(account_id)
    if not path.exists():
        return
    try:
        cookies = json.loads(path.read_text())
        await ctx.add_cookies(cookies)
        log.debug("cookies_restored", account=account_id, count=len(cookies))
    except Exception as exc:
        log.warning("cookies_restore_failed", account=account_id, error=str(exc))


def _cookie_path(account_id: str) -> Path:
    return settings.cookies_dir / f"{account_id}.json"


def _ts() -> str:
    import datetime
    return datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
