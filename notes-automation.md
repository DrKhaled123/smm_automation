notes-automation.md

Good, this is a pure code task. Let me build the full production codebase now — structured, clean, and expert-level.Now let me package everything for download:Here's what was built and why every decision was made:

---

## What's inside & key expert decisions

**Stack chosen: Python + Playwright (not Selenium)**
Playwright is async-native, has built-in `expect_file_chooser`, better selector engines, and `add_init_script` that runs on every page frame automatically. Selenium with undetected-chromedriver (Setup B) requires constant patching and doesn't compose well with asyncio.

---

### Bugs fixed from both setups

| Bug | Where | Fix |
|---|---|---|
| `config.2faKey` — invalid Python/JS identifier | Setup A (AdsPower) | Renamed to `fa_key` throughout |
| SmartProxy fake REST API (`/api/v1/proxy`) doesn't exist | Setup B | Replaced with correct HTTP proxy URL format: `user-session-ID:pass@gate.smartproxy.com:7000` |
| Thread-safe race condition on shared `driver` object in `AgentOrchestrator` | Setup B | Replaced threads with `asyncio.Semaphore` — no shared mutable state |
| `PowerAdsAutomation` is entirely fictional | Setup B | Removed entirely; real scheduling done via APScheduler |
| Steel session never released on error paths | Setup A | Fixed via `asynccontextmanager` with `try/finally` always calling cleanup |
| Proxy URL credential masking crashes if no `@` in URL | Setup A | `masked()` now uses `rsplit("@", 1)` with guard |
| In-memory rate limiter resets on every restart | Both | Redis-backed sliding-window counter with in-memory fallback |
| No 2FA handling for login flows | Both | `pyotp.TOTP` integration for all four platforms |

---

### Architecture at a glance

```
main.py → asyncio.Semaphore (concurrency cap)
  └── run_account_task()
        ├── ProxyManager  → SmartProxy sticky sessions (correct URL format)
        ├── browser_session() context manager
        │     ├── STEEL mode: REST → Steel CDP WS → Playwright
        │     └── ADSPOWER mode: AdsPower Local API WS → Playwright
        ├── Platform (Instagram/Facebook/Twitter/TikTok)
        │     └── utils/human.py (Bézier mouse, log-normal typing)
        └── Workflow (warming / posting / engagement)
              └── RateLimiter (Redis sliding window, per-account per-action)
```

**To get started:**
1. Copy `.env.example` → `.env` and fill credentials
2. Fill `data/accounts.json` with your accounts
3. `docker compose up steel redis -d` then `python main.py --mode warm`
