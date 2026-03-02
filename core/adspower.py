"""
core/adspower.py — AdsPower Local API client (correct real endpoints).

AdsPower exposes a local HTTP API on 127.0.0.1:50325.
All endpoints documented at: https://localapi-doc-en.adspower.com/
This client covers profile CRUD, browser lifecycle, and group management.

Key fixes vs Setup A/B:
  - `config.2faKey` is invalid JS/Python → renamed to `fa_key`
  - All profile parameters match the real AdsPower schema
  - Browser start returns structured data including ws.puppeteer (Playwright endpoint)
  - Proper error handling: AdsPower returns code=0 for success, nonzero for errors
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings
from utils.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

@dataclass
class BrowserStartResult:
    profile_id: str
    playwright_ws: str       # ws://... endpoint for Playwright
    puppeteer_ws: str        # ws://... endpoint for Puppeteer  
    debug_port: int
    webdriver_path: str      # chromedriver path if needed


@dataclass
class ProfileInfo:
    profile_id: str
    name: str
    group_id: str
    domain_name: str
    username: str
    serial_number: str
    created_at: str
    last_open: str


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class AdsPowerClient:
    """
    Async AdsPower Local API client.

    AdsPower must be running on the host machine.
    The API is local-only (loopback) — no remote access.
    """

    def __init__(self) -> None:
        self._base = settings.adspower.base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=30.0,
            headers={"Content-Type": "application/json"},
        )

    # ------------------------------------------------------------------
    # Profile management
    # ------------------------------------------------------------------

    async def list_profiles(
        self,
        page: int = 1,
        page_size: int = 100,
        group_id: str = "0",
    ) -> list[ProfileInfo]:
        data = await self._get(
            "/api/v1/user/list",
            params={"page": page, "page_size": page_size, "group_id": group_id},
        )
        profiles = []
        for item in data.get("list", []):
            profiles.append(ProfileInfo(
                profile_id=item["user_id"],
                name=item["name"],
                group_id=item.get("group_id", "0"),
                domain_name=item.get("domain_name", ""),
                username=item.get("username", ""),
                serial_number=item.get("serial_number", ""),
                created_at=item.get("created_time", ""),
                last_open=item.get("last_open_time", ""),
            ))
        return profiles

    async def find_profile_by_name(self, name: str) -> ProfileInfo | None:
        """Search through paginated profiles for one matching `name`."""
        page = 1
        while True:
            profiles = await self.list_profiles(page=page, page_size=100)
            if not profiles:
                return None
            for p in profiles:
                if p.name == name:
                    return p
            if len(profiles) < 100:
                return None
            page += 1

    async def create_profile(
        self,
        *,
        name: str,
        platform_url: str,          # e.g. "instagram.com"
        username: str = "",
        password: str = "",
        fa_key: str = "",            # 2FA TOTP secret (not "2faKey" — invalid identifier)
        proxy_host: str,
        proxy_port: int,
        proxy_user: str,
        proxy_password: str,
        proxy_type: str = "http",   # "http" | "socks5"
        os: str = "win",            # "win" | "mac" | "android" | "ios"
        browser_version: str = "",  # "" = use AdsPower default
        resolution: str = "1920x1080",
        language: list[str] | None = None,
        timezone: str = "America/New_York",
        group_id: str = "0",
    ) -> str:
        """Create a new browser profile. Returns the new profile_id."""
        payload: dict[str, Any] = {
            "name": name,
            "group_id": group_id,
            "domain_name": platform_url,
            "username": username,
            "password": password,
            "fakey": fa_key,
            "user_proxy_config": {
                "proxy_soft": "other",
                "proxy_type": proxy_type,
                "proxy_host": proxy_host,
                "proxy_port": str(proxy_port),
                "proxy_user": proxy_user,
                "proxy_password": proxy_password,
            },
            "fingerprint_config": {
                "os": os,
                "browser": "chrome",
                "browser_version": browser_version,
                "resolution": resolution,
                "language": language or ["en-US", "en"],
                "timezone": timezone,
                "webrtc": "proxy",      # Prevent IP leak via WebRTC
                "location": "ask",      # "ask" = prompt; "allow" = use proxy geo
                "canvas": "1",          # Randomize canvas fingerprint
                "webgl": "3",           # Randomize WebGL
                "audio": "1",           # Randomize audio fingerprint
                "fonts": "1",           # Randomize font list
                "do_not_track": 0,
            },
        }

        data = await self._post("/api/v1/user/create", json=payload)
        profile_id = data["id"]
        log.info("adspower_profile_created", profile_id=profile_id, name=name)
        return profile_id

    async def delete_profile(self, profile_id: str) -> None:
        await self._post("/api/v1/user/delete", json={"user_ids": [profile_id]})
        log.info("adspower_profile_deleted", profile_id=profile_id)

    async def update_proxy(
        self,
        profile_id: str,
        proxy_host: str,
        proxy_port: int,
        proxy_user: str,
        proxy_password: str,
    ) -> None:
        """Update proxy for an existing profile (e.g., after IP rotation)."""
        payload = {
            "user_id": profile_id,
            "user_proxy_config": {
                "proxy_soft": "other",
                "proxy_type": "http",
                "proxy_host": proxy_host,
                "proxy_port": str(proxy_port),
                "proxy_user": proxy_user,
                "proxy_password": proxy_password,
            },
        }
        await self._post("/api/v1/user/update", json=payload)

    # ------------------------------------------------------------------
    # Browser lifecycle
    # ------------------------------------------------------------------

    async def start_browser(
        self,
        profile_id: str,
        headless: bool = False,
        open_tabs: int = 1,
    ) -> BrowserStartResult:
        """
        Start the browser for a profile.
        Returns WebSocket endpoints for Playwright/Puppeteer.
        """
        data = await self._get(
            "/api/v1/browser/start",
            params={
                "user_id": profile_id,
                "headless": "1" if headless else "0",
                "open_tabs": str(open_tabs),
                # Stealth flags — disable automation indicators
                "launch_args": ",".join([
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--no-sandbox",
                ]),
            },
        )

        ws_data = data.get("ws", {})
        return BrowserStartResult(
            profile_id=profile_id,
            playwright_ws=ws_data.get("playwright", ""),
            puppeteer_ws=ws_data.get("puppeteer", ""),
            debug_port=int(data.get("debug_port", 0)),
            webdriver_path=data.get("webdriver", ""),
        )

    async def stop_browser(self, profile_id: str) -> None:
        await self._get("/api/v1/browser/stop", params={"user_id": profile_id})
        log.info("adspower_browser_stopped", profile_id=profile_id)

    async def is_browser_active(self, profile_id: str) -> bool:
        """Check if browser is currently running for this profile."""
        try:
            data = await self._get(
                "/api/v1/browser/active",
                params={"user_id": profile_id},
            )
            return data.get("status") == "Active"
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Group management
    # ------------------------------------------------------------------

    async def list_groups(self) -> list[dict]:
        data = await self._get("/api/v1/group/list", params={"page": 1, "page_size": 100})
        return data.get("list", [])

    async def create_group(self, name: str) -> str:
        """Create a profile group. Returns group_id."""
        data = await self._post("/api/v1/group/create", json={"group_name": name})
        return data["group_id"]

    # ------------------------------------------------------------------
    # Internal HTTP helpers
    # ------------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _get(self, path: str, params: dict | None = None) -> dict:
        resp = await self._client.get(path, params=params)
        return self._unwrap(resp)

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _post(self, path: str, json: dict | None = None) -> dict:
        resp = await self._client.post(path, json=json)
        return self._unwrap(resp)

    @staticmethod
    def _unwrap(resp: httpx.Response) -> dict:
        """
        AdsPower always returns JSON with shape:
            {"code": 0, "msg": "ok", "data": {...}}
        code=0 means success; anything else is an error.
        """
        resp.raise_for_status()
        body = resp.json()
        code = body.get("code", -1)
        if code != 0:
            msg = body.get("msg", "Unknown AdsPower error")
            raise RuntimeError(f"AdsPower API error (code={code}): {msg}")
        return body.get("data", {})

    async def close(self) -> None:
        await self._client.aclose()
