import json
import random
import re
from pathlib import Path
from typing import Any

from .json_tools import extract_embedded_json_blobs, walk_json
from .models import InstagramMedia
from .parsers import find_media_nodes, looks_like_media_node

CAPTURED_URL_FRAGMENTS = ("/graphql/query", "/api/graphql", "/api/v1/")
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
CHALLENGE_URL_FRAGMENTS = ("/challenge/", "/accounts/suspended", "/accounts/disabled", "/checkpoint/")


def load_playwright():
    try:
        from patchright.sync_api import sync_playwright
        return sync_playwright, "patchright"
    except ImportError:
        from playwright.sync_api import sync_playwright
        return sync_playwright, "playwright"


def launch_chromium(playwright_instance, launch_options: dict[str, Any]):
    try:
        return playwright_instance.chromium.launch(**launch_options)
    except Exception as launch_error:
        if "Executable doesn't exist" not in str(launch_error):
            raise
        try:
            return playwright_instance.chromium.launch(channel="chrome", **launch_options)
        except Exception:
            raise RuntimeError("No browser found. Run `patchright install chromium`, or install Google Chrome.") from launch_error


def capture_media_with_browser(
    page_url: str,
    proxy_url: str | None = None,
    scroll_rounds: int = 2,
    headless: bool = True,
    storage_state_path: str | None = None,
    enrich_posts_in_browser: int = 0,
) -> tuple[list[InstagramMedia], dict[str, Any]]:
    sync_playwright, engine_name = load_playwright()
    captured_payloads: list[Any] = []
    capture_report: dict[str, Any] = {
        "engine": engine_name,
        "logged_in_session": storage_state_path,
        "json_responses": 0,
        "final_url": None,
        "login_wall": False,
        "challenge": False,
    }

    def handle_response(response) -> None:
        if not any(fragment in response.url for fragment in CAPTURED_URL_FRAGMENTS):
            return
        try:
            captured_payloads.append(response.json())
            capture_report["json_responses"] += 1
        except Exception:
            return

    def block_heavy_resources(route) -> None:
        if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
            route.abort()
        else:
            route.continue_()

    with sync_playwright() as playwright_instance:
        launch_options: dict[str, Any] = {"headless": headless}
        if proxy_url:
            launch_options["proxy"] = {"server": proxy_url}
        browser = launch_chromium(playwright_instance, launch_options)
        context_options: dict[str, Any] = {"locale": "en-US", "viewport": {"width": 1366, "height": 900}}
        if storage_state_path:
            context_options["storage_state"] = storage_state_path
        browser_context = browser.new_context(**context_options)
        browser_context.route("**/*", block_heavy_resources)
        page = browser_context.new_page()
        page.on("response", handle_response)
        page.goto(page_url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(4_000)
        for scroll_index in range(scroll_rounds):
            page.mouse.wheel(0, 2_500)
            page.wait_for_timeout(2_000)
        capture_report["final_url"] = page.url
        capture_report["login_wall"] = "/accounts/login" in page.url
        capture_report["challenge"] = any(fragment in page.url for fragment in CHALLENGE_URL_FRAGMENTS)
        captured_payloads.extend(extract_embedded_json_blobs(page.content()))
        if enrich_posts_in_browser and not capture_report["challenge"] and not capture_report["login_wall"]:
            grid_shortcodes = list(dict.fromkeys(
                media.shortcode for captured_payload in captured_payloads for media in find_media_nodes(captured_payload, "grid")
            ))[:enrich_posts_in_browser]
            capture_report["browser_enrichment"] = {"requested": len(grid_shortcodes), "visited": 0, "stopped_reason": None}
            for shortcode in grid_shortcodes:
                page.goto(f"https://www.instagram.com/p/{shortcode}/", wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(random.randint(2_500, 4_500))
                if "/accounts/login" in page.url or any(fragment in page.url for fragment in CHALLENGE_URL_FRAGMENTS):
                    capture_report["challenge"] = True
                    capture_report["browser_enrichment"]["stopped_reason"] = f"challenge or login wall at {page.url}"
                    break
                captured_payloads.extend(extract_embedded_json_blobs(page.content()))
                capture_report["browser_enrichment"]["visited"] += 1
        if storage_state_path and not capture_report["challenge"] and not capture_report["login_wall"]:
            browser_context.storage_state(path=storage_state_path)
        browser.close()

    collected_media: dict[str, InstagramMedia] = {}
    source_label = f"browser_{engine_name}_{'logged_in' if storage_state_path else 'logged_out'}"
    for captured_payload in captured_payloads:
        for media in find_media_nodes(captured_payload, source_label):
            existing_media = collected_media.get(media.shortcode)
            collected_media[media.shortcode] = merge_media(existing_media, media) if existing_media else media
    capture_report["payloads_inspected"] = len(captured_payloads)
    capture_report["metric_fields_seen"] = probe_metric_fields(captured_payloads)
    return list(collected_media.values()), capture_report


METRIC_FIELD_PATTERN = re.compile(r"count|play|view", re.IGNORECASE)


def merge_media(primary_media: InstagramMedia, secondary_media: InstagramMedia) -> InstagramMedia:
    for field_name, field_value in vars(secondary_media).items():
        if getattr(primary_media, field_name) in (None, "", []) and field_value not in (None, "", []):
            setattr(primary_media, field_name, field_value)
    return primary_media


def probe_metric_fields(captured_payloads: list[Any]) -> dict[str, dict[str, Any]]:
    field_statistics: dict[str, dict[str, Any]] = {}

    def record(field_path: str, field_value: Any) -> None:
        if isinstance(field_value, (dict, list)):
            return
        statistics = field_statistics.setdefault(field_path, {"media_nodes": 0, "non_null": 0, "example": None})
        statistics["media_nodes"] += 1
        if field_value is not None:
            statistics["non_null"] += 1
            if statistics["example"] is None:
                statistics["example"] = field_value

    for captured_payload in captured_payloads:
        for json_object in walk_json(captured_payload):
            if not looks_like_media_node(json_object):
                continue
            for field_name, field_value in json_object.items():
                if METRIC_FIELD_PATTERN.search(field_name):
                    record(field_name, field_value)
                if isinstance(field_value, dict):
                    for child_name, child_value in field_value.items():
                        if METRIC_FIELD_PATTERN.search(child_name):
                            record(f"{field_name}.{child_name}", child_value)
    return dict(sorted(field_statistics.items(), key=lambda field_entry: -field_entry[1]["non_null"]))


def save_login_session(storage_state_path: str, proxy_url: str | None = None) -> None:
    sync_playwright, engine_name = load_playwright()
    Path(storage_state_path).parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright_instance:
        launch_options: dict[str, Any] = {"headless": False}
        if proxy_url:
            launch_options["proxy"] = {"server": proxy_url}
        browser = launch_chromium(playwright_instance, launch_options)
        browser_context = browser.new_context(locale="en-US", viewport={"width": 1366, "height": 900})
        page = browser_context.new_page()
        page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
        input("Log in manually in the browser window (finish any verification), then press Enter here... ")
        browser_context.storage_state(path=storage_state_path)
        browser.close()


def summarize_capture(media_items: list[InstagramMedia], capture_report: dict[str, Any]) -> str:
    return json.dumps({"report": capture_report, "media": [media.to_dict() for media in media_items]}, ensure_ascii=False, indent=2)
