"""
settings.py — Validated configuration via Pydantic Settings.
All values come from environment variables / .env file.
Fail-fast on startup if required values are missing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class SmartProxySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SMARTPROXY_")

    user: str = Field(..., description="SmartProxy username")
    password: str = Field(..., description="SmartProxy password")

    # Gate endpoints (SmartProxy's real hostnames)
    gate_host: str = "gate.smartproxy.com"
    gate_port: int = 7000          # Rotating residential
    sticky_port_start: int = 10000 # Sticky residential range start
    sticky_port_end: int = 19999

    # Session config
    session_duration_minutes: int = Field(25, ge=1, le=30)
    proxy_type: Literal["residential", "mobile", "datacenter"] = "residential"

    def rotating_url(self, country: str = "us") -> str:
        """Standard rotating residential proxy URL."""
        return (
            f"http://{self.user}-country-{country}:{self.password}"
            f"@{self.gate_host}:{self.gate_port}"
        )

    def sticky_url(self, session_id: str | int, country: str = "us") -> str:
        """Sticky session proxy URL — same IP for up to 30 min."""
        return (
            f"http://{self.user}-session-{session_id}-country-{country}"
            f":{self.password}@{self.gate_host}:{self.gate_port}"
        )

    def masked(self, url: str) -> str:
        """Return proxy URL with credentials redacted for safe logging."""
        if "@" in url:
            creds, host = url.rsplit("@", 1)
            scheme = creds.split("://")[0]
            return f"{scheme}://***:***@{host}"
        return url


class SteelBrowserSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="STEEL_")

    # Self-hosted Steel Browser (docker)
    base_url: str = "http://localhost:3000"
    api_key: str | None = None  # Only required for Steel Cloud

    session_timeout_ms: int = 1_800_000  # 30 min
    concurrency: int = Field(5, ge=1, le=20)


class AdsPowerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ADSPOWER_")

    # AdsPower Local API — always runs on loopback
    base_url: str = "http://127.0.0.1:50325"
    api_key: str | None = None  # Required only on Team/Enterprise plans


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_")

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str | None = None

    @property
    def url(self) -> str:
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class DailyLimits(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LIMIT_")

    # Per-account, per-day ceilings (stay well below platform thresholds)
    likes: int = 120
    follows: int = 60
    unfollows: int = 60
    comments: int = 30
    posts: int = 3
    story_views: int = 200
    dms: int = 20

    # Active hours (local account timezone)
    active_start_hour: int = Field(8, ge=0, le=23)
    active_end_hour: int = Field(22, ge=0, le=23)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Sub-settings (loaded via their own env-prefix)
    smartproxy: SmartProxySettings = Field(default_factory=SmartProxySettings)
    steel: SteelBrowserSettings = Field(default_factory=SteelBrowserSettings)
    adspower: AdsPowerSettings = Field(default_factory=AdsPowerSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    limits: DailyLimits = Field(default_factory=DailyLimits)

    # General
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    headless: bool = True
    max_concurrent_accounts: int = Field(5, ge=1, le=50)

    # Directories
    cookies_dir: Path = BASE_DIR / "cookies"
    screenshots_dir: Path = BASE_DIR / "screenshots"
    logs_dir: Path = BASE_DIR / "logs"
    data_dir: Path = BASE_DIR / "data"

    @model_validator(mode="after")
    def ensure_directories(self) -> Settings:
        for d in (self.cookies_dir, self.screenshots_dir, self.logs_dir, self.data_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self

    @field_validator("max_concurrent_accounts")
    @classmethod
    def cap_concurrency(cls, v: int) -> int:
        if v > 10:
            import warnings
            warnings.warn(
                "Running >10 concurrent accounts risks platform detection. "
                "Proceed only if you have sufficient unique proxies.",
                stacklevel=2,
            )
        return v


# Module-level singleton — import this everywhere
settings = Settings()
