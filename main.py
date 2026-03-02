"""
main.py — Entry point and top-level orchestrator.

Coordinates:
  - Account loading and profile management
  - Proxy session assignment
  - Browser session creation
  - Workflow dispatch (warming, posting, engagement)
  - Concurrency limiting (semaphore-based, not thread-based)
  - Scheduling via APScheduler

Run modes:
  python main.py --mode warm        # Account warming for all configured accounts
  python main.py --mode post        # Execute today's content calendar
  python main.py --mode engage      # Mass engagement run
  python main.py --mode daemon      # Scheduled daemon (all workflows)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config.settings import settings
from core.adspower import AdsPowerClient
from core.browser import browser_session
from core.proxy import ProxyManager
from platforms.instagram import Instagram
from platforms.others import Facebook, TikTok, Twitter
from utils.logger import configure_logging, get_logger
from utils.rate_limiter import RateLimiter
from workflows.warming import AccountWarmer

log = get_logger(__name__)

# Platform registry — maps platform name → class
PLATFORM_MAP = {
    "instagram": Instagram,
    "facebook": Facebook,
    "twitter": Twitter,
    "tiktok": TikTok,
}


# ---------------------------------------------------------------------------
# Account model
# ---------------------------------------------------------------------------

def load_accounts(path: str = "data/accounts.json") -> list[dict[str, Any]]:
    """Load account definitions from JSON file."""
    p = Path(path)
    if not p.exists():
        log.error("accounts_file_not_found", path=str(p))
        sys.exit(1)
    return json.loads(p.read_text())


# ---------------------------------------------------------------------------
# Core: single-account session runner
# ---------------------------------------------------------------------------

async def run_account_task(
    account: dict[str, Any],
    proxy_manager: ProxyManager,
    rl: RateLimiter,
    task: str,
    semaphore: asyncio.Semaphore,
    adspower: AdsPowerClient | None = None,
    mode: str = "steel",
) -> dict:
    """
    Run a single account through one task cycle.
    Acquires semaphore so max_concurrent_accounts is respected.
    """
    account_id = account["id"]
    platform_name = account["platform"]
    country = account.get("country", "us")

    async with semaphore:
        log.info("account_task_start", account=account_id, task=task, platform=platform_name)

        # Get or create proxy session for this account
        proxy = await proxy_manager.get_for_account(account_id, country=country)
        health_ok = await proxy_manager.health_check(proxy)
        if not health_ok:
            proxy = await proxy_manager.get_for_account(account_id, country=country, force_new=True)

        # Resolve AdsPower profile_id if in adspower mode
        adspower_profile_id: str | None = None
        if mode == "adspower" and adspower:
            profile = await adspower.find_profile_by_name(f"{platform_name}_{account_id}")
            if profile:
                adspower_profile_id = profile.profile_id
            else:
                # Create profile on-the-fly
                proxy_host, proxy_port = proxy.url.split("@")[1].rsplit(":", 1)
                proxy_user = settings.smartproxy.user + f"-session-{proxy.session_id}"
                adspower_profile_id = await adspower.create_profile(
                    name=f"{platform_name}_{account_id}",
                    platform_url=f"{platform_name}.com",
                    username=account.get("username", ""),
                    password=account.get("password", ""),
                    proxy_host=proxy_host,
                    proxy_port=int(proxy_port),
                    proxy_user=proxy_user,
                    proxy_password=settings.smartproxy.password,
                )

        try:
            async with browser_session(
                account_id=account_id,
                proxy_session=proxy,
                mode=mode,
                adspower=adspower,
                adspower_profile_id=adspower_profile_id,
            ) as page:
                PlatformClass = PLATFORM_MAP.get(platform_name)
                if not PlatformClass:
                    return {"account": account_id, "error": f"Unknown platform: {platform_name}"}

                platform = PlatformClass(page=page, account_id=account_id)

                # Ensure we are logged in
                status = await platform.check_login_status()
                if status.value != "logged_in":
                    status = await platform.login(
                        username=account["username"],
                        password=account["password"],
                        fa_secret=account.get("fa_secret", ""),
                    )

                if status.value != "logged_in":
                    return {
                        "account": account_id,
                        "error": f"Login failed: {status.value}",
                    }

                # Dispatch to the requested task
                if task == "warm":
                    warmer = AccountWarmer(platform, account_id, rl)
                    result = await warmer.run_daily_session(
                        feed_urls=account.get("feed_urls", []),
                        target_usernames=account.get("target_users", []),
                        comment_templates=account.get("comment_templates"),
                        post_queue=account.get("post_queue", []),
                    )
                elif task == "post":
                    posts = account.get("post_queue", [])
                    result = {"posted": 0, "errors": []}
                    for post_data in posts[:settings.limits.posts]:
                        if not await rl.can_perform(account_id, "post"):
                            result["errors"].append("Daily post limit reached")
                            break
                        if post_data.get("image"):
                            r = await platform.post_image(
                                post_data["image"],
                                caption=post_data.get("caption", ""),
                            )
                        else:
                            r = await platform.post_text(post_data.get("text", ""))
                        if r.success:
                            await rl.record(account_id, "post")
                            result["posted"] += 1
                        else:
                            result["errors"].append(r.error)

                elif task == "engage":
                    result = {"likes": 0, "follows": 0, "comments": 0}
                    targets = account.get("target_users", [])
                    feed_urls = account.get("feed_urls", [])
                    for url in feed_urls[:settings.limits.likes]:
                        if not await rl.can_perform(account_id, "like"):
                            break
                        r = await platform.like_post(url)
                        if r.success:
                            await rl.record(account_id, "like")
                            result["likes"] += 1
                        await asyncio.sleep(10 + asyncio.get_event_loop().time() % 30)

                    for user in targets[:settings.limits.follows]:
                        if not await rl.can_perform(account_id, "follow"):
                            break
                        r = await platform.follow_user(user)
                        if r.success:
                            await rl.record(account_id, "follow")
                            result["follows"] += 1
                        await asyncio.sleep(20 + asyncio.get_event_loop().time() % 60)
                else:
                    result = {"error": f"Unknown task: {task}"}

                return {"account": account_id, "task": task, "result": result}

        except Exception as exc:
            log.exception("account_task_failed", account=account_id, task=task)
            return {"account": account_id, "error": str(exc)}


# ---------------------------------------------------------------------------
# Top-level runners
# ---------------------------------------------------------------------------

async def run_all(task: str, accounts: list[dict], mode: str = "steel") -> list[dict]:
    """Run `task` across all accounts with controlled concurrency."""
    proxy_manager = ProxyManager()
    rl = RateLimiter()  # In-memory fallback if Redis not configured
    semaphore = asyncio.Semaphore(settings.max_concurrent_accounts)

    adspower = AdsPowerClient() if mode == "adspower" else None

    coros = [
        run_account_task(
            account=acc,
            proxy_manager=proxy_manager,
            rl=rl,
            task=task,
            semaphore=semaphore,
            adspower=adspower,
            mode=mode,
        )
        for acc in accounts
    ]

    results = await asyncio.gather(*coros, return_exceptions=True)

    if adspower:
        await adspower.close()

    # Normalize exceptions into result dicts
    output = []
    for acc, res in zip(accounts, results):
        if isinstance(res, Exception):
            output.append({"account": acc["id"], "error": str(res)})
        else:
            output.append(res)

    return output


async def daemon_main(accounts: list[dict], mode: str) -> None:
    """Run as a scheduled daemon — all three workflow types on schedule."""
    scheduler = AsyncIOScheduler()

    # Warming: every day at 09:00 local time
    scheduler.add_job(
        lambda: asyncio.create_task(run_all("warm", accounts, mode)),
        CronTrigger(hour=9, minute=0),
        id="warm",
        name="Account warming",
    )

    # Posting: every day at 10:30 and 17:00
    for hour, minute in [(10, 30), (17, 0)]:
        scheduler.add_job(
            lambda: asyncio.create_task(run_all("post", accounts, mode)),
            CronTrigger(hour=hour, minute=minute),
            id=f"post_{hour}",
            name=f"Content posting {hour}:{'%02d' % minute}",
        )

    # Engagement: every day at 12:00 and 20:00
    for hour in [12, 20]:
        scheduler.add_job(
            lambda: asyncio.create_task(run_all("engage", accounts, mode)),
            CronTrigger(hour=hour, minute=0),
            id=f"engage_{hour}",
            name=f"Engagement run {hour}:00",
        )

    # Proxy rotation check every 20 minutes
    proxy_manager = ProxyManager()
    scheduler.add_job(
        lambda: asyncio.create_task(proxy_manager.rotate_expiring_sessions()),
        "interval",
        minutes=20,
        id="proxy_rotation",
    )

    scheduler.start()
    log.info("daemon_started", jobs=len(scheduler.get_jobs()))

    try:
        # Keep the event loop alive
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        log.info("daemon_stopped")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Social Media Automation Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --mode warm
  python main.py --mode post
  python main.py --mode engage
  python main.py --mode daemon
  python main.py --mode warm --browser adspower
  python main.py --accounts data/accounts.json --mode post
        """,
    )
    parser.add_argument(
        "--mode",
        choices=["warm", "post", "engage", "daemon"],
        default="warm",
        help="Workflow to run",
    )
    parser.add_argument(
        "--browser",
        choices=["steel", "adspower"],
        default="steel",
        help="Browser backend to use",
    )
    parser.add_argument(
        "--accounts",
        default="data/accounts.json",
        help="Path to accounts JSON file",
    )
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    configure_logging(settings.log_level, settings.logs_dir)

    log.info("startup", mode=args.mode, browser=args.browser)

    accounts = load_accounts(args.accounts)
    log.info("accounts_loaded", count=len(accounts))

    if args.mode == "daemon":
        await daemon_main(accounts, args.browser)
    else:
        results = await run_all(args.mode, accounts, args.browser)
        # Pretty-print summary
        ok = sum(1 for r in results if "error" not in r)
        fail = len(results) - ok
        log.info("run_complete", ok=ok, failed=fail)
        for r in results:
            print(json.dumps(r, indent=2))


if __name__ == "__main__":
    asyncio.run(async_main())
