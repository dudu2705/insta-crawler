# insta-crawler

Proof of concept for every Instagram data method we evaluated. Everything outputs the same normalized `InstagramMedia` shape (shortcode, caption, owner, likes, comments, views, taken_at, hashtags, source_strategy), so the methods can be compared directly.

| Method | Command | Login | What it can fetch |
|---|---|---|---|
| D1 Logged-out HTTP | `post`, `profile`, `diagnose` | No | Single posts, profile counts, first page of profile posts |
| D2 Logged-out browser | `browser` | No | Same as D1, via headless browser |
| D3 Logged-in browser | `login-browser`, `browser-auth`, `sessions` | Burner account | Hashtag feed, Explore, Reels feed, keyword search, profile, location |
| D4 Private mobile API | `private-api` | Burner account | Hashtag top/recent/reels, Explore reels, profile posts/reels, post, hashtag search |
| Apify | `provider apify ...` | API token | hashtag, search, profile_posts, post |
| SocialCrawl | `provider socialcrawl ...` | API key | trending, hashtag, search, profile_posts, post |
| HikerAPI | `provider hikerapi ...` | API key | hashtag, search (via best hashtag match), profile_posts (reels), post |
| All 3rd party | `compare <capability> <target>` | API keys | Same request on each provider, side by side |

## Setup

```bash
cd ~/projects/insta-crawler
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
patchright install chromium
cp .env.example .env
pytest -q
```

Fill `.env` only for the methods you want to test:

```
APIFY_TOKEN=          https://console.apify.com/settings/integrations
SOCIALCRAWL_API_KEY=  https://www.socialcrawl.dev (100 free credits)
HIKERAPI_KEY=         https://hikerapi.com (100 free requests)
IG_USERNAME=          burner account for D4 only, never your real account
IG_PASSWORD=
IG_PROXY=             optional proxy for D4
```

## D1 / D2: logged-out

```bash
python -m insta_crawler diagnose --post DdeRysim9zK --username instagram
python -m insta_crawler post "https://www.instagram.com/p/DdeRysim9zK/?hl=en"
python -m insta_crawler profile instagram --with-posts --limit 12
python -m insta_crawler browser instagram --scrolls 2
```

`diagnose` runs every logged-out strategy and prints which ones Instagram still serves. Strategies, in order: page embedded JSON → GraphQL with a harvested `doc_id` → embed page. `doc_id`s and relay flags are extracted from Instagram's own JS bundles and cached in `.cache/web_tokens.json` for 6h; delete it to force a re-harvest.

## D3: logged-in browser (session pool)

```bash
python -m insta_crawler login-browser burner_a --proxy http://user:pass@host:port
python -m insta_crawler login-browser burner_b --proxy http://user:pass@other-host:port
python -m insta_crawler sessions

python -m insta_crawler browser-auth hashtag hanoi --scrolls 3
python -m insta_crawler browser-auth explore
python -m insta_crawler browser-auth reels
python -m insta_crawler browser-auth search "ăn vặt hà nội"
python -m insta_crawler browser-auth profile someuser --session burner_a --headed
```

- `login-browser` opens a real window. You log in and complete any SMS/email check yourself; the cookies are saved to `sessions/<name>.json` together with the proxy you passed, so each account keeps its own IP.
- `browser-auth` picks the least recently used session that isn't cooling down, loads the page, scrolls, and captures the JSON the page fetches.
- Sessions can be reused every 5 minutes at most. A session that lands on a challenge or login page is cooled down for 24h and logged in `sessions/pool_state.json`; reopen it with `login-browser <name>` to resolve the check.
- Explore and Reels results are personalized per account, so two sessions return different feeds.

## D4: private mobile API (instagrapi)

```bash
python -m insta_crawler private-api hashtag hanoi --limit 27
python -m insta_crawler private-api hashtag-recent hanoi
python -m insta_crawler private-api hashtag-reels hanoi
python -m insta_crawler private-api explore-reels --limit 20
python -m insta_crawler private-api profile natgeo --limit 12
python -m insta_crawler private-api profile-reels natgeo
python -m insta_crawler private-api post DdeRysim9zK
python -m insta_crawler private-api search-hashtags "hà nội"
```

The first run logs in with `IG_USERNAME` / `IG_PASSWORD` and saves the device session to `sessions/private_api/<username>.json`; later runs reuse it instead of logging in again. If Instagram sends a verification code, the CLI asks you to type it. Requests are spaced 3–8s apart. This method has the highest ban risk.

## 3rd-party APIs

```bash
python -m insta_crawler provider socialcrawl trending --limit 30
python -m insta_crawler provider socialcrawl hashtag hanoi
python -m insta_crawler provider apify profile_posts natgeo --limit 10
python -m insta_crawler provider hikerapi post DdeRysim9zK --raw

python -m insta_crawler compare hashtag hanoi --limit 20
python -m insta_crawler compare search "phở" --providers socialcrawl,hikerapi
```

`compare` prints one line per provider (items, field coverage 0–1, seconds, cost) and writes everything to `compare.json`. Unsupported capabilities show as `not supported` (for example Apify and HikerAPI have no trending endpoint).

Add `--raw` to see the provider's original response. SocialCrawl doesn't publish its per-row schema, so its mapping checks several likely field paths. If a field comes back empty, check `--raw` and adjust `socialcrawl_row_to_model` in `insta_crawler/providers/socialcrawl.py`.

## What was verified

- 17 offline tests cover parsing for every method: old and new Instagram response shapes, doc_id harvesting, Apify/SocialCrawl/HikerAPI response handling and pagination, instagrapi media objects, session rotation and cooldown.
- Endpoint paths come from each provider's docs as of Nov 2026: Apify input schema, HikerAPI v1 reference (`x-access-key` header, `/chunk` endpoints return `[items, cursor]`), SocialCrawl docs (`x-api-key` header, `data.items` + `pagination.next_cursor`).
- instagrapi method names checked against version 3.2.2.
- Not run against live Instagram or live provider APIs from the build environment. Run `diagnose` and one `compare` locally first.

## Layout

```
insta_crawler/
  http_client.py      curl_cffi Chrome TLS session, delays, 429 / login-wall detection     (D1)
  web_tokens.py       lsd / csrf / doc_id / relay flag harvesting + cache                   (D1)
  crawler.py          logged-out strategies with attempt log                                (D1)
  browser_capture.py  headless capture, manual login, session save                         (D2, D3)
  session_pool.py     session rotation, per-session proxy, challenge cooldown, target URLs (D3)
  private_api.py      instagrapi wrapper with session persistence                           (D4)
  providers/          apify.py, socialcrawl.py, hikerapi.py, shared base                    (3rd party)
  parsers.py          shape-agnostic normalization used by every method
  cli.py              all commands
tests/
```
