# Social Media Marketing Automation

A production-ready Python-based automation system for managing multiple social media accounts with browser automation, proxy support, and workflow management.

## Features

- **Multi-Platform Support**: Instagram, Facebook, Twitter/X, TikTok
- **Browser Backends**: Steel Browser (CDP-based) or AdsPower (profile management)
- **Proxy Management**: SmartProxy residential proxies with sticky sessions
- **Account Warming**: Progressive 28-day warm-up schedule to avoid detection
- **Rate Limiting**: Redis-backed per-account, per-action rate limiting
- **Human-like Behavior**: Realistic mouse movements, typing patterns, and delays
- **Scheduled Workflows**: Daily warming, posting, and engagement runs
- **Cookie Persistence**: Session survival across restarts

## Architecture

```
main.py → asyncio.Semaphore (concurrency cap)
  └── run_account_task()
        ├── ProxyManager  → SmartProxy sticky sessions
        ├── browser_session() context manager
        │     ├── STEEL mode: REST → Steel CDP WS → Playwright
        │     └── ADSPOWER mode: AdsPower Local API WS → Playwright
        ├── Platform (Instagram/Facebook/Twitter/TikTok)
        │     └── utils/human.py (Bézier mouse, log-normal typing)
        └── Workflow (warming / posting / engagement)
              └── RateLimiter (Redis sliding window)
```

## Quick Start

### Prerequisites

- Python 3.11+
- Docker (for Steel Browser and Redis)
- SmartProxy account (or compatible proxy service)
- Optional: AdsPower desktop app

### Installation

1. Clone the repository:
```bash
git clone https://github.com/DrKhaled123/smm_automation.git
cd smm_automation
```

2. Copy environment template:
```bash
cp .env.example .env
```

3. Edit `.env` with your credentials:
```bash
# SmartProxy credentials
SMARTPROXY_USER=your_username
SMARTPROXY_PASSWORD=your_password

# Steel Browser (self-hosted)
STEEL_BASE_URL=http://localhost:3000

# Optional: AdsPower
ADSPOWER_BASE_URL=http://127.0.0.1:50325
```

4. Install Python dependencies:
```bash
pip install -r requirements.txt
```

5. Start services:
```bash
docker compose up steel redis -d
```

6. Configure your accounts in `data/accounts.json`:
```json
[
  {
    "id": "ig_account_01",
    "platform": "instagram",
    "username": "your_username",
    "password": "your_password",
    "fa_secret": "",
    "country": "us",
    "target_users": ["competitor_page_1"],
    "feed_urls": ["https://www.instagram.com/p/POST_ID/"],
    "comment_templates": ["Great post! 🔥"],
    "post_queue": [
      {
        "image": "data/content/post1.jpg",
        "caption": "Your caption here"
      }
    ]
  }
]
```

## Usage

### Run Modes

```bash
# Account warming (recommended for new accounts)
python main.py --mode warm

# Post content from queue
python main.py --mode post

# Engagement run (likes, follows, comments)
python main.py --mode engage

# Run as scheduled daemon (all workflows)
python main.py --mode daemon
```

### Browser Selection

```bash
# Use Steel Browser (default)
python main.py --mode warm --browser steel

# Use AdsPower
python main.py --mode warm --browser adspower
```

### Custom Accounts File

```bash
python main.py --accounts /path/to/custom_accounts.json --mode post
```

## Account Warming Schedule

New accounts are warmed up over 28 days to avoid detection:

- **Week 1**: Browse only. No posting, minimal engagement.
- **Week 2**: Light engagement (likes, story views). Still no posting.
- **Week 3**: First posts. Conservative engagement.
- **Week 4+**: Normal operating mode.

The warming schedule is defined in `workflows/warming.py` and can be customized.

## Rate Limiting

Default daily limits per account (configurable via environment variables):

| Action | Limit |
|---------|--------|
| Likes | 120 |
| Follows | 60 |
| Unfollows | 60 |
| Comments | 30 |
| Posts | 3 |
| Story Views | 200 |
| DMs | 20 |

## Project Structure

```
smm_automation/
├── main.py              # Entry point and orchestrator
├── config/
│   └── settings.py      # Pydantic-based configuration
├── core/
│   ├── adspower.py      # AdsPower Local API client
│   ├── browser.py       # Browser session manager
│   └── proxy.py        # SmartProxy manager
├── platforms/
│   ├── base.py         # Abstract platform class
│   ├── instagram.py    # Instagram automation
│   └── others.py       # Facebook, Twitter, TikTok
├── utils/
│   ├── human.py        # Human-like interaction helpers
│   ├── logger.py       # Structured logging
│   └── rate_limiter.py # Redis-backed rate limiting
├── workflows/
│   └── warming.py      # Account warming workflow
├── data/
│   └── accounts.json   # Account configurations
├── cookies/           # Session persistence
├── logs/              # Application logs
└── screenshots/       # Debug screenshots
```

## Configuration

### Environment Variables

See `.env.example` for all available options:

- `SMARTPROXY_*`: SmartProxy configuration
- `STEEL_*`: Steel Browser settings
- `ADSPOWER_*`: AdsPower settings
- `REDIS_*`: Redis connection
- `LOG_LEVEL`: Logging verbosity (DEBUG, INFO, WARNING, ERROR)
- `HEADLESS`: Run browsers in headless mode
- `MAX_CONCURRENT_ACCOUNTS`: Maximum concurrent account tasks
- `LIMIT_*`: Daily action limits

## Security Notes

- Never commit `.env` or `data/accounts.json` to version control
- Use environment variables for sensitive credentials
- Consider using a secrets manager in production
- Enable 2FA on all social media accounts

## Troubleshooting

### Common Issues

**Issue**: Proxy connection failed
- **Solution**: Check SmartProxy credentials and ensure your plan is active

**Issue**: Login failed with checkpoint
- **Solution**: Manually log in via browser to complete verification, then retry

**Issue**: Rate limit exceeded
- **Solution**: Reduce `LIMIT_*` values in `.env` or increase delays

**Issue**: Browser not starting
- **Solution**: Ensure Steel Browser container is running: `docker compose ps`

## License

This project is provided as-is for educational purposes. Use responsibly and in compliance with social media platform terms of service.

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## Disclaimer

This automation tool is intended for legitimate social media management purposes. Users are responsible for ensuring their use complies with all applicable laws and platform terms of service. The authors assume no liability for misuse.
