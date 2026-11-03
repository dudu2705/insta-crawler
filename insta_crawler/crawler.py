import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .http_client import INSTAGRAM_BASE_URL, InstagramBlockedError, InstagramHttpClient, LoginWallError
from .json_tools import extract_embedded_json_blobs
from .models import InstagramMedia, InstagramProfile
from .parsers import find_media_nodes, find_profile, parse_profile_from_meta_tags
from .web_tokens import (
    WebTokens,
    extract_page_tokens,
    extract_relay_operations,
    extract_relay_provider_names,
    extract_script_urls,
    load_cached_tokens,
    save_cached_tokens,
)

DEFAULT_TOKEN_CACHE_PATH = Path(".cache/web_tokens.json")
MAXIMUM_SCRIPT_BUNDLES = 300
MINIMUM_SCRIPT_BUNDLES = 20
DEFAULT_SCRIPT_BUNDLES = 60
EMBED_CONTEXT_PATTERN = re.compile(r'"contextJSON":"((?:[^"\\]|\\.)*)"')
POST_QUERY_NAME_FRAGMENTS = (
    "PolarisLoggedOutDesktopWWWPostRoot",
    "PolarisLoggedOutDesktopWWWPost",
    "PolarisLoggedOutPost",
    "PolarisPostActionLoadPostQuery",
    "PolarisPostRootQuery",
    "PostPageQuery",
)
PROFILE_FEED_QUERY_NAME_FRAGMENTS = (
    "PolarisLoggedOutDesktopWWWProfilePostsTabContentQuery",
    "PolarisLoggedOutProfilePosts",
    "PolarisProfilePostsQuery",
    "PolarisProfilePostsTabContentQuery",
)


@dataclass
class StrategyAttempt:
    strategy_name: str
    succeeded: bool
    detail: str
    duration_seconds: float


@dataclass
class CrawlOutcome:
    target: str
    result: Any = None
    attempts: list[StrategyAttempt] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        serialized_result = self.result.to_dict() if hasattr(self.result, "to_dict") else self.result
        if isinstance(self.result, list):
            serialized_result = [item.to_dict() for item in self.result]
        return {
            "target": self.target,
            "result": serialized_result,
            "attempts": [attempt.__dict__ for attempt in self.attempts],
        }


class InstagramLoggedOutCrawler:
    def __init__(self, http_client: InstagramHttpClient | None = None, token_cache_path: Path = DEFAULT_TOKEN_CACHE_PATH):
        self.http_client = http_client or InstagramHttpClient()
        self.token_cache_path = token_cache_path
        self.web_tokens: WebTokens | None = None
        self.debug_html_path: Path | None = None

    def run_strategies(self, target: str, strategies: list[tuple[str, Callable[[], Any]]], stop_on_first_success: bool = True) -> CrawlOutcome:
        outcome = CrawlOutcome(target=target)
        for strategy_name, strategy_function in strategies:
            started_at = time.time()
            try:
                strategy_result = strategy_function()
                succeeded = bool(strategy_result)
                detail = "ok" if succeeded else "no data found in response"
            except (InstagramBlockedError, LoginWallError) as blocking_error:
                strategy_result, succeeded, detail = None, False, str(blocking_error)
            except Exception as unexpected_error:
                strategy_result, succeeded, detail = None, False, f"{type(unexpected_error).__name__}: {unexpected_error}"
            outcome.attempts.append(StrategyAttempt(strategy_name, succeeded, detail, round(time.time() - started_at, 2)))
            if succeeded and outcome.result is None:
                outcome.result = strategy_result
                if stop_on_first_success:
                    break
        return outcome

    def ensure_web_tokens(self, warmup_url: str, force_refresh: bool = False, required_fragments: tuple[str, ...] = ()) -> WebTokens:
        def has_required(candidate_tokens: WebTokens | None) -> bool:
            return bool(candidate_tokens) and (not required_fragments or candidate_tokens.find_doc_id(*required_fragments) is not None)

        if self.web_tokens and self.web_tokens.is_fresh() and has_required(self.web_tokens) and not force_refresh:
            return self.web_tokens
        known_tokens = None if force_refresh else (self.web_tokens or load_cached_tokens(self.token_cache_path))
        warmup_page = self.http_client.get_page(warmup_url)
        page_tokens = extract_page_tokens(warmup_page.text)
        if known_tokens and known_tokens.is_fresh() and has_required(known_tokens):
            known_tokens.lsd_token = page_tokens.lsd_token or known_tokens.lsd_token
            known_tokens.csrf_token = page_tokens.csrf_token or known_tokens.csrf_token
            self.web_tokens = known_tokens
            return known_tokens
        if known_tokens:
            page_tokens.doc_ids = {**known_tokens.doc_ids, **page_tokens.doc_ids}
        relay_provider_names = set(extract_relay_provider_names(warmup_page.text)) | set(known_tokens.relay_provider_names if known_tokens else [])
        script_urls = extract_script_urls(warmup_page.text)
        harvest_report = {
            "warmup_url": warmup_url,
            "warmup_status": warmup_page.status_code,
            "warmup_html_bytes": len(warmup_page.text),
            "operations_from_page": len(page_tokens.doc_ids),
            "script_urls_found": len(script_urls),
            "required_fragments": list(required_fragments),
            "required_found": has_required(page_tokens),
            "bundles_fetched": 0,
            "bundle_failures": {},
            "operations_from_bundles": 0,
        }
        for script_url in script_urls[:MAXIMUM_SCRIPT_BUNDLES]:
            if required_fragments and harvest_report["bundles_fetched"] >= MINIMUM_SCRIPT_BUNDLES and has_required(page_tokens):
                break
            if not required_fragments and harvest_report["bundles_fetched"] >= DEFAULT_SCRIPT_BUNDLES:
                break
            try:
                script_response = self.http_client.get_static_asset(script_url)
            except Exception as bundle_error:
                failure_label = type(bundle_error).__name__
                harvest_report["bundle_failures"][failure_label] = harvest_report["bundle_failures"].get(failure_label, 0) + 1
                continue
            if script_response.status_code != 200:
                failure_label = f"HTTP {script_response.status_code}"
                harvest_report["bundle_failures"][failure_label] = harvest_report["bundle_failures"].get(failure_label, 0) + 1
                continue
            harvest_report["bundles_fetched"] += 1
            bundle_operations = extract_relay_operations(script_response.text)
            harvest_report["operations_from_bundles"] += len(bundle_operations)
            page_tokens.doc_ids.update(bundle_operations)
            relay_provider_names.update(extract_relay_provider_names(script_response.text))
        page_tokens.relay_provider_names = sorted(relay_provider_names)
        page_tokens.harvested_at = time.time()
        harvest_report["required_found"] = has_required(page_tokens)
        page_tokens.harvest_report = harvest_report
        if self.debug_html_path:
            self.debug_html_path.parent.mkdir(parents=True, exist_ok=True)
            self.debug_html_path.write_text(warmup_page.text)
        if page_tokens.doc_ids:
            save_cached_tokens(self.token_cache_path, page_tokens)
        self.web_tokens = page_tokens
        return page_tokens

    def fetch_post(self, shortcode: str, stop_on_first_success: bool = True) -> CrawlOutcome:
        post_url = f"{INSTAGRAM_BASE_URL}/p/{shortcode}/"
        strategies = [
            ("post_page_embedded_json", lambda: self.post_from_page(shortcode, post_url)),
            ("post_graphql_doc_id", lambda: self.post_from_graphql(shortcode, post_url)),
            ("post_embed_page", lambda: self.post_from_embed(shortcode)),
        ]
        return self.run_strategies(shortcode, strategies, stop_on_first_success)

    def post_from_page(self, shortcode: str, post_url: str) -> InstagramMedia | None:
        page_response = self.http_client.get_page(post_url)
        for json_blob in extract_embedded_json_blobs(page_response.text):
            for media in find_media_nodes(json_blob, "post_page_embedded_json"):
                if media.shortcode == shortcode:
                    return media
        return None

    def run_graphql_candidates(
        self,
        web_tokens: WebTokens,
        operation_name: str,
        doc_id: str,
        referer_url: str,
        variable_candidates: list[tuple[str, dict]],
        source_strategy: str,
    ) -> list[InstagramMedia]:
        candidate_errors = []
        for candidate_label, candidate_variables in variable_candidates:
            query_variables = {**candidate_variables, **web_tokens.relay_provider_variables()}
            graphql_response = self.http_client.post_graphql(
                {"doc_id": doc_id, "variables": json.dumps(query_variables), "lsd": web_tokens.lsd_token or "", "fb_api_req_friendly_name": operation_name},
                self.http_client.api_headers(web_tokens.app_id, referer_url, web_tokens.csrf_token, web_tokens.lsd_token),
            )
            try:
                response_payload = graphql_response.json()
            except json.JSONDecodeError:
                candidate_errors.append(f"{candidate_label}: HTTP {graphql_response.status_code} non-JSON {graphql_response.text[:80]!r}")
                continue
            found_media = find_media_nodes(response_payload, source_strategy)
            if found_media:
                return found_media
            error_messages = [error.get("message", "") for error in response_payload.get("errors", []) if isinstance(error, dict)]
            candidate_errors.append(f"{candidate_label}: {'; '.join(error_messages)[:160] or 'no media in response ' + json.dumps(response_payload)[:120]}")
        raise RuntimeError(f"{operation_name} ({doc_id}) -> " + " | ".join(candidate_errors))

    def post_from_graphql(self, shortcode: str, post_url: str) -> InstagramMedia | None:
        web_tokens = self.ensure_web_tokens(post_url, required_fragments=POST_QUERY_NAME_FRAGMENTS)
        operation_match = web_tokens.find_doc_id(*POST_QUERY_NAME_FRAGMENTS)
        if not operation_match:
            post_like_operations = sorted(name for name in web_tokens.doc_ids if "Post" in name and "Mutation" not in name)
            raise RuntimeError(f"No post query doc_id harvested ({len(web_tokens.doc_ids)} operations). Post-related queries seen: {post_like_operations[:12]}")
        operation_name, doc_id = operation_match
        variable_candidates = [
            ("shortcode", {"shortcode": shortcode}),
            ("shortcode_full", {"shortcode": shortcode, "fetch_tagged_user_count": None, "hoisted_comment_id": None, "hoisted_reply_id": None}),
        ]
        for media in self.run_graphql_candidates(web_tokens, operation_name, doc_id, post_url, variable_candidates, "post_graphql_doc_id"):
            if media.shortcode == shortcode:
                return media
        return None

    def post_from_embed(self, shortcode: str) -> InstagramMedia | None:
        embed_response = self.http_client.get_page(f"{INSTAGRAM_BASE_URL}/p/{shortcode}/embed/captioned/")
        candidate_payloads: list[Any] = extract_embedded_json_blobs(embed_response.text)
        context_match = EMBED_CONTEXT_PATTERN.search(embed_response.text)
        if context_match:
            candidate_payloads.append(json.loads(json.loads(f'"{context_match.group(1)}"')))
        for candidate_payload in candidate_payloads:
            for media in find_media_nodes(candidate_payload, "post_embed_page"):
                if media.shortcode == shortcode:
                    return media
        return None

    def fetch_profile(self, username: str, stop_on_first_success: bool = True) -> CrawlOutcome:
        profile_url = f"{INSTAGRAM_BASE_URL}/{username}/"
        strategies = [
            ("profile_web_api", lambda: self.profile_from_web_api(username, profile_url)),
            ("profile_page_embedded_json", lambda: self.profile_from_page(username, profile_url)),
            ("profile_meta_tags", lambda: self.profile_from_meta_tags(username, profile_url)),
        ]
        return self.run_strategies(username, strategies, stop_on_first_success)

    def profile_from_web_api(self, username: str, profile_url: str) -> InstagramProfile | None:
        web_tokens = self.web_tokens or extract_page_tokens(self.http_client.get_page(profile_url).text)
        api_response = self.http_client.get_api(
            f"{INSTAGRAM_BASE_URL}/api/v1/users/web_profile_info/?username={username}",
            self.http_client.api_headers(web_tokens.app_id, profile_url, web_tokens.csrf_token),
        )
        if api_response.status_code != 200:
            raise RuntimeError(f"HTTP {api_response.status_code}: {api_response.text[:160]}")
        return find_profile(api_response.json(), username, "profile_web_api")

    def profile_from_page(self, username: str, profile_url: str) -> InstagramProfile | None:
        page_response = self.http_client.get_page(profile_url)
        for json_blob in extract_embedded_json_blobs(page_response.text):
            found_profile = find_profile(json_blob, username, "profile_page_embedded_json")
            if found_profile:
                return found_profile
        return None

    def profile_from_meta_tags(self, username: str, profile_url: str) -> InstagramProfile | None:
        page_response = self.http_client.get_page(profile_url)
        return parse_profile_from_meta_tags(page_response.text, username)

    def fetch_profile_posts(self, username: str, post_limit: int = 12) -> CrawlOutcome:
        profile_url = f"{INSTAGRAM_BASE_URL}/{username}/"
        strategies = [
            ("profile_feed_graphql_doc_id", lambda: self.profile_posts_from_graphql(username, profile_url, post_limit)),
            ("profile_page_embedded_json", lambda: self.profile_posts_from_page(username, profile_url)),
        ]
        return self.run_strategies(username, strategies)

    def profile_posts_from_graphql(self, username: str, profile_url: str, post_limit: int) -> list[InstagramMedia]:
        web_tokens = self.ensure_web_tokens(profile_url, required_fragments=PROFILE_FEED_QUERY_NAME_FRAGMENTS)
        operation_match = web_tokens.find_doc_id(*PROFILE_FEED_QUERY_NAME_FRAGMENTS)
        if not operation_match:
            raise RuntimeError(f"No profile feed doc_id harvested ({len(web_tokens.doc_ids)} operations found)")
        operation_name, doc_id = operation_match
        profile = self.profile_from_page(username, profile_url)
        user_id = profile.user_id if profile else None
        feed_data = {"count": post_limit, "include_relationship_info": False, "latest_besties_reel_media": False, "latest_reel_media": False}
        variable_candidates = [("data_username", {"data": feed_data, "username": username})]
        if user_id:
            variable_candidates += [
                ("id_first", {"id": user_id, "first": post_limit}),
                ("user_id_first", {"user_id": user_id, "first": post_limit}),
                ("data_id", {"data": feed_data, "id": user_id}),
            ]
        variable_candidates.append(("username_first", {"username": username, "first": post_limit}))
        found_media = self.run_graphql_candidates(web_tokens, operation_name, doc_id, profile_url, variable_candidates, "profile_feed_graphql_doc_id")
        return found_media[:post_limit]

    def profile_posts_from_page(self, username: str, profile_url: str) -> list[InstagramMedia]:
        page_response = self.http_client.get_page(profile_url)
        collected_media: dict[str, InstagramMedia] = {}
        for json_blob in extract_embedded_json_blobs(page_response.text):
            for media in find_media_nodes(json_blob, "profile_page_embedded_json"):
                collected_media.setdefault(media.shortcode, media)
        return list(collected_media.values())
