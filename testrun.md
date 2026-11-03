# Test run commands

## Setup

Requires Python 3.12 (macOS: `brew install python@3.12`).

```bash
cd ~/projects/insta-crawler
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
patchright install chromium
cp .env.example .env
pytest -q
```

Fill in `.env` only for the methods you want to run:

| Method | `.env` keys |
|---|---|
| SocialCrawl | `SOCIALCRAWL_API_KEY` |
| HikerAPI | `HIKERAPI_KEY` |
| Apify | `APIFY_TOKEN` |
| D4 instagrapi | `IG_USERNAME`, `IG_PASSWORD` (burner account) |
| D3 logged-in browser | none (log in through the browser window) |

Next time, only run:

```bash
cd ~/projects/insta-crawler
source .venv/bin/activate
```

## 1. SocialCrawl

```bash
python -m insta_crawler provider socialcrawl hashtag balayage --limit 5
python -m insta_crawler provider socialcrawl hashtag_recent balayage --limit 5
python -m insta_crawler hashtag-batch --method socialcrawl --limit 20
```

## 2. HikerAPI

```bash
python -m insta_crawler provider hikerapi hashtag balayage --limit 5
python -m insta_crawler hashtag-batch --method hikerapi --limit 20
```

## 3. Apify

```bash
python -m insta_crawler provider apify hashtag balayage --limit 5
python -m insta_crawler hashtag-batch --method apify --limit 20
```

## 4. D4 instagrapi

```bash
python -m insta_crawler private-api hashtag balayage --limit 5
python -m insta_crawler private-api hashtag-recent balayage --limit 5
python -m insta_crawler hashtag-batch --method instagrapi --limit 10
```

## 5. D3 logged-in browser

```bash
python -m insta_crawler login-browser burner_a
python -m insta_crawler browser-auth hashtag balayage --session burner_a --headed
python -m insta_crawler browser-auth hashtag balayage --session burner_a --enrich browser --enrich-limit 10 --headed
```

Results are saved to `results/`.
