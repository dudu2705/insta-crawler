import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from .config import load_dotenv_file
from .crawler import DEFAULT_TOKEN_CACHE_PATH, POST_QUERY_NAME_FRAGMENTS, PROFILE_FEED_QUERY_NAME_FRAGMENTS, InstagramLoggedOutCrawler
from .http_client import INSTAGRAM_BASE_URL, InstagramHttpClient
from .session_pool import DEFAULT_SESSIONS_DIRECTORY, SessionPool, build_target_url

SHORTCODE_PATTERN = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:p|reel|reels)/([\w-]+)")


def normalize_shortcode(post_reference: str) -> str:
    url_match = SHORTCODE_PATTERN.search(post_reference)
    return url_match.group(1) if url_match else post_reference.strip("/")


def normalize_username(profile_reference: str) -> str:
    return profile_reference.rstrip("/").split("/")[-1].lstrip("@")


def build_crawler(parsed_arguments: argparse.Namespace) -> InstagramLoggedOutCrawler:
    http_client = InstagramHttpClient(proxy_url=parsed_arguments.proxy, minimum_delay_seconds=parsed_arguments.min_delay, maximum_delay_seconds=parsed_arguments.max_delay)
    return InstagramLoggedOutCrawler(http_client=http_client, token_cache_path=Path(parsed_arguments.token_cache))


def print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def slugify_name_part(name_part) -> str:
    return re.sub(r"[^\w]+", "-", str(name_part), flags=re.UNICODE).strip("-")[:40]


def save_result(payload, parsed_arguments: argparse.Namespace, *name_parts) -> Path:
    output_value = getattr(parsed_arguments, "output", None)
    if output_value:
        output_path = Path(output_value)
    else:
        file_stem = "_".join(slugify_name_part(name_part) for name_part in name_parts if name_part)
        output_path = Path("results") / f"{file_stem}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"saved to {output_path}", file=sys.stderr)
    if getattr(parsed_arguments, "print", False):
        print_json(payload)
    return output_path


def add_output_arguments(subcommand_parser: argparse.ArgumentParser) -> None:
    subcommand_parser.add_argument("--output", help="JSON file path (default: results/<command>_<target>_<timestamp>.json)")
    subcommand_parser.add_argument("--print", action="store_true", help="also print the JSON to the terminal")


def print_attempt_table(outcome_dict: dict) -> None:
    print(f"\n== {outcome_dict['target']} ==", file=sys.stderr)
    for attempt in outcome_dict["attempts"]:
        status_label = "OK  " if attempt["succeeded"] else "FAIL"
        print(f"  [{status_label}] {attempt['strategy_name']:<30} {attempt['duration_seconds']:>5}s  {attempt['detail']}", file=sys.stderr)


def command_post(parsed_arguments: argparse.Namespace) -> None:
    shortcode = normalize_shortcode(parsed_arguments.post)
    outcome_dict = build_crawler(parsed_arguments).fetch_post(shortcode).to_dict()
    print_attempt_table(outcome_dict)
    save_result(outcome_dict, parsed_arguments, "post", shortcode)


def command_profile(parsed_arguments: argparse.Namespace) -> None:
    crawler = build_crawler(parsed_arguments)
    username = normalize_username(parsed_arguments.username)
    profile_outcome = crawler.fetch_profile(username).to_dict()
    print_attempt_table(profile_outcome)
    report = {"profile": profile_outcome}
    if parsed_arguments.with_posts:
        posts_outcome = crawler.fetch_profile_posts(username, parsed_arguments.limit).to_dict()
        print_attempt_table(posts_outcome)
        report["posts"] = posts_outcome
    save_result(report, parsed_arguments, "profile", username)


def command_diagnose(parsed_arguments: argparse.Namespace) -> None:
    crawler = build_crawler(parsed_arguments)
    username = normalize_username(parsed_arguments.username)
    diagnosis = {
        "post": crawler.fetch_post(normalize_shortcode(parsed_arguments.post), stop_on_first_success=False).to_dict(),
        "profile": crawler.fetch_profile(username, stop_on_first_success=False).to_dict(),
        "profile_posts": crawler.fetch_profile_posts(username, 12).to_dict(),
    }
    web_tokens = crawler.web_tokens
    diagnosis["web_tokens"] = {
        "doc_ids_harvested": len(web_tokens.doc_ids) if web_tokens else 0,
        "relay_providers_harvested": len(web_tokens.relay_provider_names) if web_tokens else 0,
        "lsd_token_found": bool(web_tokens and web_tokens.lsd_token),
        "post_query": web_tokens.find_doc_id(*POST_QUERY_NAME_FRAGMENTS) if web_tokens else None,
        "profile_feed_query": web_tokens.find_doc_id(*PROFILE_FEED_QUERY_NAME_FRAGMENTS) if web_tokens else None,
        "harvest_report": web_tokens.harvest_report if web_tokens else None,
    }
    for section_name in ("post", "profile", "profile_posts"):
        print_attempt_table(diagnosis[section_name])
    print(f"\nweb tokens: {json.dumps(diagnosis['web_tokens'], indent=2)}", file=sys.stderr)
    if parsed_arguments.output:
        Path(parsed_arguments.output).write_text(json.dumps(diagnosis, ensure_ascii=False, indent=2))
        print(f"full report saved to {parsed_arguments.output}", file=sys.stderr)


def command_tokens(parsed_arguments: argparse.Namespace) -> None:
    crawler = build_crawler(parsed_arguments)
    crawler.debug_html_path = Path(parsed_arguments.save_html) if parsed_arguments.save_html else None
    warmup_url = f"{INSTAGRAM_BASE_URL}/{parsed_arguments.page.lstrip('/')}"
    required_fragments = POST_QUERY_NAME_FRAGMENTS if parsed_arguments.page.startswith(("p/", "reel/")) else PROFILE_FEED_QUERY_NAME_FRAGMENTS
    web_tokens = crawler.ensure_web_tokens(warmup_url, force_refresh=True, required_fragments=required_fragments)
    instagram_operations = sorted(name for name in web_tokens.doc_ids if "LoggedOut" in name or ("Post" in name and "Mutation" not in name))
    print_json({
        "harvest_report": web_tokens.harvest_report,
        "lsd_token_found": bool(web_tokens.lsd_token),
        "csrf_token_found": bool(web_tokens.csrf_token),
        "app_id": web_tokens.app_id,
        "operations_total": len(web_tokens.doc_ids),
        "logged_out_and_post_queries": instagram_operations[:40],
        "post_query": web_tokens.find_doc_id(*POST_QUERY_NAME_FRAGMENTS),
        "profile_feed_query": web_tokens.find_doc_id(*PROFILE_FEED_QUERY_NAME_FRAGMENTS),
        "relay_providers": len(web_tokens.relay_provider_names),
        "saved_html": parsed_arguments.save_html,
    })


def command_browser(parsed_arguments: argparse.Namespace) -> None:
    from .browser_capture import capture_media_with_browser, summarize_capture
    target_reference = parsed_arguments.target
    if target_reference.startswith("http"):
        page_url = target_reference
    else:
        page_url = f"{INSTAGRAM_BASE_URL}/{normalize_username(target_reference)}/"
    media_items, capture_report = capture_media_with_browser(page_url, parsed_arguments.proxy, parsed_arguments.scrolls, not parsed_arguments.headed)
    print(f"browser: {len(media_items)} items, final url {capture_report['final_url']}, login wall {capture_report['login_wall']}", file=sys.stderr)
    save_result(json.loads(summarize_capture(media_items, capture_report)), parsed_arguments, "browser", target_reference)


def command_login_browser(parsed_arguments: argparse.Namespace) -> None:
    from .browser_capture import save_login_session
    session_pool = SessionPool(Path(parsed_arguments.sessions_dir))
    save_login_session(str(session_pool.session_path(parsed_arguments.session_name)), parsed_arguments.proxy)
    session_pool.register(parsed_arguments.session_name, parsed_arguments.proxy)
    print(f"saved session {parsed_arguments.session_name}", file=sys.stderr)


def command_sessions(parsed_arguments: argparse.Namespace) -> None:
    print_json(SessionPool(Path(parsed_arguments.sessions_dir)).summary())


def command_browser_auth(parsed_arguments: argparse.Namespace) -> None:
    from .browser_capture import capture_media_with_browser, summarize_capture
    session_pool = SessionPool(Path(parsed_arguments.sessions_dir))
    if parsed_arguments.session:
        session_state = session_pool.session_states.get(parsed_arguments.session)
    else:
        session_state = session_pool.pick_session(ignore_cooldown=parsed_arguments.ignore_cooldown)
    if session_state is None:
        sys.exit("No usable session. Run `login-browser <name>` first, or wait for cooldowns (see `sessions`).")
    target_url = build_target_url(parsed_arguments.kind, parsed_arguments.value)
    print(f"using session {session_state.session_name} -> {target_url}", file=sys.stderr)
    media_items, capture_report = capture_media_with_browser(
        target_url,
        parsed_arguments.proxy or session_state.proxy_url,
        parsed_arguments.scrolls,
        not parsed_arguments.headed,
        str(session_pool.session_path(session_state.session_name)),
        enrich_posts_in_browser=parsed_arguments.enrich_limit if parsed_arguments.enrich == "browser" else 0,
    )
    hit_challenge = capture_report["challenge"] or capture_report["login_wall"]
    session_pool.record_use(session_state.session_name, hit_challenge, f"{parsed_arguments.kind}:{len(media_items)} items" + (" CHALLENGE" if hit_challenge else ""))
    if hit_challenge:
        print(f"session {session_state.session_name} hit a challenge/login wall, cooled down for 24h. Open it with `login-browser {session_state.session_name}` to resolve.", file=sys.stderr)
    print(f"browser-auth {parsed_arguments.kind}: {len(media_items)} items from {capture_report['json_responses']} JSON responses", file=sys.stderr)
    if parsed_arguments.enrich == "browser":
        print(f"  browser enrichment: {capture_report.get('browser_enrichment')}", file=sys.stderr)
    if parsed_arguments.enrich == "logged-out" and media_items:
        from .enrichment import enrich_with_logged_out_posts
        print(f"  enriching up to {parsed_arguments.enrich_limit} posts with likes/comments from their post pages (logged-out, no account)", file=sys.stderr)
        capture_report["logged_out_enrichment"] = enrich_with_logged_out_posts(
            media_items,
            build_crawler(parsed_arguments),
            parsed_arguments.enrich_limit,
            log=lambda message: print(message, file=sys.stderr),
        )
    print(
        f"  filled -> likes: {sum(item.like_count is not None for item in media_items)}/{len(media_items)} | "
        f"comments: {sum(item.comment_count is not None for item in media_items)}/{len(media_items)} | "
        f"views: {sum(item.view_count is not None for item in media_items)}/{len(media_items)} | "
        f"videos: {sum(item.media_type == 'video' for item in media_items)}",
        file=sys.stderr,
    )
    if parsed_arguments.show_fields:
        print("  metric fields Instagram sent (non_null/seen, example):", file=sys.stderr)
        for field_path, field_statistics in list(capture_report.get("metric_fields_seen", {}).items())[:15]:
            print(f"    {field_path:<40} {field_statistics['non_null']}/{field_statistics['media_nodes']}  {field_statistics['example']}", file=sys.stderr)
    save_result(json.loads(summarize_capture(media_items, capture_report)), parsed_arguments, "browser_auth", parsed_arguments.kind, parsed_arguments.value)


PRIVATE_API_ACTIONS = {
    "hashtag": ("hashtag_top", True),
    "hashtag-recent": ("hashtag_recent", True),
    "hashtag-reels": ("hashtag_reels", True),
    "explore-reels": ("explore_reels", False),
    "explore": ("explore", False),
    "profile": ("profile_posts", True),
    "profile-reels": ("profile_reels", True),
    "post": ("post", True),
    "search-hashtags": ("search_hashtags", True),
}


def command_private_api(parsed_arguments: argparse.Namespace) -> None:
    from .private_api import PrivateApiCrawler
    method_name, needs_value = PRIVATE_API_ACTIONS[parsed_arguments.action]
    if needs_value and not parsed_arguments.value:
        sys.exit(f"{parsed_arguments.action} needs a value")
    try:
        private_crawler = PrivateApiCrawler(
            parsed_arguments.ig_username,
            parsed_arguments.ig_password,
            parsed_arguments.proxy or os.environ.get("IG_PROXY"),
            sessionid=parsed_arguments.sessionid,
            browser_session_path=Path(parsed_arguments.browser_session) if parsed_arguments.browser_session else None,
        )
        private_crawler.ensure_login()
    except RuntimeError as login_error:
        sys.exit(f"login failed: {login_error}")
    print(f"logged in as {private_crawler.client.username}", file=sys.stderr)
    crawler_method = getattr(private_crawler, method_name)
    method_arguments: list = []
    if needs_value:
        method_arguments.append(normalize_shortcode(parsed_arguments.value) if parsed_arguments.action == "post" else parsed_arguments.value)
    if method_name not in ("post", "search_hashtags", "explore"):
        method_arguments.append(parsed_arguments.limit)
    started_at = time.time()
    try:
        method_result = crawler_method(*method_arguments)
    except Exception as request_error:
        if type(request_error).__module__.startswith("instagrapi"):
            login_mode = "sessionid (browser cookie)" if private_crawler.sessionid and not private_crawler.password else "password"
            hint = " Instagram rejects many browser sessionids on mobile endpoints; use a password login, or the browser-auth method with this session." if "sessionid" in login_mode and "LoginRequired" in type(request_error).__name__ else ""
            sys.exit(f"{parsed_arguments.action} failed ({login_mode} login): {type(request_error).__name__}: {str(request_error)[:200]}.{hint}")
        raise
    serialized_items = [item.to_dict() if hasattr(item, "to_dict") else item for item in method_result]
    print(f"private api {parsed_arguments.action}: {len(serialized_items)} items in {round(time.time() - started_at, 1)}s", file=sys.stderr)
    save_result(
        {"method": "private_api_instagrapi", "action": parsed_arguments.action, "target": parsed_arguments.value, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "items": serialized_items},
        parsed_arguments,
        "private_api",
        parsed_arguments.action,
        parsed_arguments.value,
    )


def build_provider(provider_name: str):
    from .providers import PROVIDER_CLASSES
    return PROVIDER_CLASSES[provider_name]()


def normalize_provider_target(capability: str, target_value: str | None) -> str | None:
    if target_value and capability == "post":
        return normalize_shortcode(target_value)
    if target_value and capability == "profile_posts":
        return normalize_username(target_value)
    return target_value


def command_provider(parsed_arguments: argparse.Namespace) -> None:
    provider_result = build_provider(parsed_arguments.provider_name).run(
        parsed_arguments.capability,
        normalize_provider_target(parsed_arguments.capability, parsed_arguments.target),
        parsed_arguments.limit,
    )
    print(f"{provider_result.provider_name} {provider_result.capability}: {len(provider_result.items)} items, {provider_result.duration_seconds}s, {provider_result.cost_note}", file=sys.stderr)
    save_result(provider_result.to_dict(include_raw=parsed_arguments.raw), parsed_arguments, provider_result.provider_name, provider_result.capability, provider_result.target)


def count_filled_media_fields(item_dict: dict) -> float:
    checked_fields = ("caption", "owner_username", "like_count", "comment_count", "view_count", "taken_at")
    return sum(item_dict.get(field_name) not in (None, "", []) for field_name in checked_fields) / len(checked_fields)


def command_compare(parsed_arguments: argparse.Namespace) -> None:
    from .providers import PROVIDER_CLASSES, CapabilityNotSupportedError
    provider_names = parsed_arguments.providers.split(",") if parsed_arguments.providers else list(PROVIDER_CLASSES)
    target_value = normalize_provider_target(parsed_arguments.capability, parsed_arguments.target)
    comparison_rows = []
    for provider_name in provider_names:
        try:
            provider_result = build_provider(provider_name).run(parsed_arguments.capability, target_value, parsed_arguments.limit)
            result_dict = provider_result.to_dict(include_raw=parsed_arguments.raw)
            item_dicts = result_dict["items"]
            comparison_rows.append({
                "provider": provider_name,
                "status": "ok",
                "items": len(item_dicts),
                "field_coverage": round(sum(map(count_filled_media_fields, item_dicts)) / len(item_dicts), 2) if item_dicts else 0,
                "seconds": provider_result.duration_seconds,
                "cost": provider_result.cost_note,
                "result": result_dict,
            })
        except CapabilityNotSupportedError as unsupported_error:
            comparison_rows.append({"provider": provider_name, "status": "not supported", "detail": str(unsupported_error)})
        except Exception as provider_error:
            comparison_rows.append({"provider": provider_name, "status": "error", "detail": f"{type(provider_error).__name__}: {provider_error}"[:300]})
    print(f"\n== compare {parsed_arguments.capability} {target_value or ''} ==", file=sys.stderr)
    for comparison_row in comparison_rows:
        if comparison_row["status"] == "ok":
            print(f"  {comparison_row['provider']:<12} ok    items={comparison_row['items']:<4} coverage={comparison_row['field_coverage']:<5} {comparison_row['seconds']}s  {comparison_row['cost']}", file=sys.stderr)
        else:
            print(f"  {comparison_row['provider']:<12} {comparison_row['status']:<14} {comparison_row['detail']}", file=sys.stderr)
    Path(parsed_arguments.output).write_text(json.dumps(comparison_rows, ensure_ascii=False, indent=2))
    print(f"full results saved to {parsed_arguments.output}", file=sys.stderr)


DEFAULT_BATCH_HASHTAGS = "balayage,haircolor,copperhair,curlyhair,hairtok,bondrepair"
BATCH_METHODS = ("socialcrawl", "hikerapi", "apify", "instagrapi")


def build_hashtag_fetcher(method_name: str):
    from .hashtag_batch import private_api_hashtag_fetcher, provider_hashtag_fetcher
    if method_name == "instagrapi":
        from .private_api import PrivateApiCrawler
        private_crawler = PrivateApiCrawler(proxy_url=os.environ.get("IG_PROXY"))
        private_crawler.ensure_login()
        return private_api_hashtag_fetcher(private_crawler)
    return provider_hashtag_fetcher(build_provider(method_name))


def command_hashtag_batch(parsed_arguments: argparse.Namespace) -> None:
    from .hashtag_batch import run_hashtag_batch
    hashtags = [hashtag.strip().lstrip("#") for hashtag in parsed_arguments.hashtags.split(",") if hashtag.strip()]
    rankings = tuple(ranking.strip() for ranking in parsed_arguments.rankings.split(","))
    try:
        hashtag_fetcher = build_hashtag_fetcher(parsed_arguments.method)
    except (RuntimeError, AssertionError) as setup_error:
        sys.exit(str(setup_error))
    print(f"\n== hashtag-batch via {parsed_arguments.method}: {len(hashtags)} hashtags x {rankings} ==", file=sys.stderr)
    collection = run_hashtag_batch(
        parsed_arguments.method,
        hashtag_fetcher,
        hashtags,
        rankings,
        parsed_arguments.limit,
        parsed_arguments.pause,
        log=lambda message: print(message, file=sys.stderr),
    )
    output_path = Path(parsed_arguments.output or f"results/hashtags_{parsed_arguments.method}_{time.strftime('%Y%m%d_%H%M')}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(collection, ensure_ascii=False, indent=2))
    summary = collection["summary"]
    print(
        f"\n{summary['total_posts']} unique posts | likes {summary['total_likes']} | comments {summary['total_comments']} | "
        f"with views {summary['posts_with_views']} | with timestamp {summary['posts_with_timestamp']} | "
        f"partial={collection['metadata']['partial']} | {collection['metadata']['duration_seconds']}s",
        file=sys.stderr,
    )
    for rank_index, top_post in enumerate(summary["top_posts"][:5], start=1):
        print(f"  {rank_index}. {top_post['engagement_per_hour']:>10}/h  @{top_post['owner']}  {top_post['url']}  {top_post['hashtags']}", file=sys.stderr)
    print(f"saved to {output_path}", file=sys.stderr)


def build_argument_parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(prog="insta-crawler", description="Instagram crawler proof of concept: DIY logged-out, DIY logged-in, 3rd-party APIs")
    argument_parser.add_argument("--proxy", help="http://user:pass@host:port (residential recommended)")
    argument_parser.add_argument("--min-delay", type=float, default=2.0)
    argument_parser.add_argument("--max-delay", type=float, default=5.0)
    argument_parser.add_argument("--token-cache", default=str(DEFAULT_TOKEN_CACHE_PATH))
    subcommand_parsers = argument_parser.add_subparsers(dest="command", required=True)

    post_parser = subcommand_parsers.add_parser("post", help="Fetch one post by URL or shortcode")
    post_parser.add_argument("post")
    add_output_arguments(post_parser)
    post_parser.set_defaults(handler=command_post)

    profile_parser = subcommand_parsers.add_parser("profile", help="Fetch a public profile")
    profile_parser.add_argument("username")
    profile_parser.add_argument("--with-posts", action="store_true")
    profile_parser.add_argument("--limit", type=int, default=12)
    add_output_arguments(profile_parser)
    profile_parser.set_defaults(handler=command_profile)

    diagnose_parser = subcommand_parsers.add_parser("diagnose", help="Run every strategy and report which still work")
    diagnose_parser.add_argument("--post", default="DdeRysim9zK")
    diagnose_parser.add_argument("--username", default="instagram")
    diagnose_parser.add_argument("--output", default="diagnosis.json")
    diagnose_parser.set_defaults(handler=command_diagnose)

    tokens_parser = subcommand_parsers.add_parser("tokens", help="Force a doc_id harvest and show what was found")
    tokens_parser.add_argument("--page", default="instagram/", help="page to warm up on, e.g. instagram/ or p/DdeRysim9zK/")
    tokens_parser.add_argument("--save-html", default=".cache/warmup.html")
    tokens_parser.set_defaults(handler=command_tokens)

    browser_parser = subcommand_parsers.add_parser("browser", help="Logged-out headless browser capture (fallback)")
    browser_parser.add_argument("target", help="username or full Instagram URL")
    browser_parser.add_argument("--scrolls", type=int, default=2)
    browser_parser.add_argument("--headed", action="store_true")
    add_output_arguments(browser_parser)
    browser_parser.set_defaults(handler=command_browser)

    login_parser = subcommand_parsers.add_parser("login-browser", help="[DIY logged-in] Open a browser, log in manually, save the session")
    login_parser.add_argument("session_name")
    login_parser.add_argument("--sessions-dir", default=str(DEFAULT_SESSIONS_DIRECTORY))
    login_parser.set_defaults(handler=command_login_browser)

    sessions_parser = subcommand_parsers.add_parser("sessions", help="[DIY logged-in] Show session pool status")
    sessions_parser.add_argument("--sessions-dir", default=str(DEFAULT_SESSIONS_DIRECTORY))
    sessions_parser.set_defaults(handler=command_sessions)

    browser_auth_parser = subcommand_parsers.add_parser("browser-auth", help="[DIY logged-in] Crawl with a saved session")
    browser_auth_parser.add_argument("kind", choices=["hashtag", "explore", "reels", "search", "profile", "location"])
    browser_auth_parser.add_argument("value", nargs="?")
    browser_auth_parser.add_argument("--session", help="session name; default picks the least recently used available one")
    browser_auth_parser.add_argument("--sessions-dir", default=str(DEFAULT_SESSIONS_DIRECTORY))
    browser_auth_parser.add_argument("--scrolls", type=int, default=3)
    browser_auth_parser.add_argument("--headed", action="store_true")
    browser_auth_parser.add_argument("--ignore-cooldown", action="store_true")
    browser_auth_parser.add_argument("--enrich", choices=["logged-out", "browser", "none"], default="logged-out", help="how to add likes/comments the grid doesn't include")
    browser_auth_parser.add_argument("--enrich-limit", type=int, default=24, help="max posts to open for likes/comments")
    browser_auth_parser.add_argument("--show-fields", action="store_true", help="print every count/play/view field Instagram sent")
    add_output_arguments(browser_auth_parser)
    browser_auth_parser.set_defaults(handler=command_browser_auth)

    private_parser = subcommand_parsers.add_parser("private-api", help="[DIY logged-in] instagrapi mobile private API")
    private_parser.add_argument("action", choices=sorted(PRIVATE_API_ACTIONS))
    private_parser.add_argument("value", nargs="?")
    private_parser.add_argument("--limit", type=int, default=20)
    private_parser.add_argument("--ig-username")
    private_parser.add_argument("--ig-password")
    private_parser.add_argument("--sessionid", help="Instagram sessionid cookie instead of password")
    private_parser.add_argument("--browser-session", help="reuse the sessionid from a login-browser session file, e.g. sessions/burner_a.json")
    add_output_arguments(private_parser)
    private_parser.set_defaults(handler=command_private_api)

    provider_parser = subcommand_parsers.add_parser("provider", help="[3rd party] Call one provider")
    provider_parser.add_argument("provider_name", choices=["apify", "socialcrawl", "hikerapi"])
    provider_parser.add_argument("capability", choices=["trending", "hashtag", "hashtag_recent", "search", "profile_posts", "post"])
    provider_parser.add_argument("target", nargs="?")
    provider_parser.add_argument("--limit", type=int, default=20)
    provider_parser.add_argument("--raw", action="store_true", help="include raw provider responses")
    add_output_arguments(provider_parser)
    provider_parser.set_defaults(handler=command_provider)

    compare_parser = subcommand_parsers.add_parser("compare", help="[3rd party] Same request across providers, side by side")
    compare_parser.add_argument("capability", choices=["trending", "hashtag", "hashtag_recent", "search", "profile_posts", "post"])
    compare_parser.add_argument("target", nargs="?")
    compare_parser.add_argument("--providers", help="comma list, default all")
    compare_parser.add_argument("--limit", type=int, default=20)
    compare_parser.add_argument("--raw", action="store_true")
    compare_parser.add_argument("--output", default="compare.json")
    compare_parser.set_defaults(handler=command_compare)

    batch_parser = subcommand_parsers.add_parser("hashtag-batch", help="For each hashtag, fetch top (+ recent where supported), dedupe, rank by engagement/hour")
    batch_parser.add_argument("--method", choices=BATCH_METHODS, required=True)
    batch_parser.add_argument("--hashtags", default=DEFAULT_BATCH_HASHTAGS)
    batch_parser.add_argument("--rankings", default="top,recent")
    batch_parser.add_argument("--limit", type=int, default=30, help="posts per hashtag per ranking")
    batch_parser.add_argument("--pause", type=float, default=2.0, help="seconds between queries")
    batch_parser.add_argument("--output")
    batch_parser.set_defaults(handler=command_hashtag_batch)
    return argument_parser


def main() -> None:
    load_dotenv_file()
    parsed_arguments = build_argument_parser().parse_args()
    parsed_arguments.handler(parsed_arguments)


if __name__ == "__main__":
    main()
