import re
from typing import Any

from ..models import InstagramMedia
from ..parsers import first_present
from .base import InstagramDataProvider, ProviderResult

SOCIALCRAWL_BASE_URL = "https://www.socialcrawl.dev/v1/instagram"
SHORTCODE_IN_URL_PATTERN = re.compile(r"/(?:p|reel|reels)/([\w-]+)")
HASHTAG_PATTERN = re.compile(r"#(\w+)", re.UNICODE)


def extract_shortcode(row: dict[str, Any]) -> str | None:
    direct_shortcode = first_present(row, "post.shortcode", "post.code", "shortcode", "code", "post.ext.shortcode")
    if direct_shortcode:
        return direct_shortcode
    row_url = first_present(row, "post.url", "url", "post.permalink", "permalink")
    url_match = SHORTCODE_IN_URL_PATTERN.search(row_url or "")
    return url_match.group(1) if url_match else None


def socialcrawl_row_to_model(row: dict[str, Any]) -> InstagramMedia | None:
    shortcode = extract_shortcode(row)
    if not shortcode:
        return None
    caption_text = first_present(row, "post.content.text", "post.text", "post.caption", "content.text", "text", "caption")
    hashtag_list = first_present(row, "post.content.hashtags", "post.hashtags", "hashtags")
    return InstagramMedia(
        shortcode=shortcode,
        url=first_present(row, "post.url", "url") or f"https://www.instagram.com/p/{shortcode}/",
        media_type=first_present(row, "post.content.type", "post.type", "post.media_type", "type"),
        caption=caption_text,
        owner_username=first_present(row, "author.handle", "author.username", "post.author.handle", "post.ext.author_handle"),
        like_count=first_present(row, "engagement.likes", "post.engagement.likes", "likes"),
        comment_count=first_present(row, "engagement.comments", "post.engagement.comments", "comments"),
        view_count=first_present(row, "engagement.views", "post.engagement.views", "views"),
        taken_at=first_present(row, "published_at", "post.published_at"),
        hashtags=hashtag_list if isinstance(hashtag_list, list) else HASHTAG_PATTERN.findall(caption_text or ""),
        source_strategy="provider_socialcrawl",
    )


def extract_rows(envelope: dict[str, Any]) -> list[dict[str, Any]]:
    response_data = envelope.get("data")
    if isinstance(response_data, dict):
        if isinstance(response_data.get("items"), list):
            return response_data["items"]
        if isinstance(response_data.get("post"), dict):
            return [response_data]
    if isinstance(response_data, list):
        return response_data
    return []


class SocialCrawlProvider(InstagramDataProvider):
    provider_name = "socialcrawl"
    api_key_environment_variable = "SOCIALCRAWL_API_KEY"
    supported_capabilities = ("trending", "hashtag", "hashtag_recent", "search", "profile_posts", "post")

    def call_endpoint(self, endpoint_path: str, query_parameters: dict[str, Any], capability: str, target: str | None, page_limit: int = 1) -> ProviderResult:
        provider_result = ProviderResult(provider_name=self.provider_name, capability=capability, target=target)
        total_credits_used = 0
        current_parameters = dict(query_parameters)
        for page_index in range(page_limit):
            envelope = self.send_request("GET", f"{SOCIALCRAWL_BASE_URL}{endpoint_path}", params=current_parameters, headers={"x-api-key": self.api_key})
            provider_result.raw_responses.append(envelope)
            total_credits_used += envelope.get("credits_used") or 0
            provider_result.items.extend(media for media in (socialcrawl_row_to_model(row) for row in extract_rows(envelope)) if media)
            pagination = envelope.get("pagination") or {}
            provider_result.next_cursor = pagination.get("next_cursor")
            if not pagination.get("has_more") or not provider_result.next_cursor:
                break
            current_parameters["cursor"] = provider_result.next_cursor
        provider_result.cost_note = f"{total_credits_used} credits used (~£0.006 each on the starter pack)"
        return provider_result

    def fetch_trending(self, target: str | None, limit: int) -> ProviderResult:
        provider_result = ProviderResult(provider_name=self.provider_name, capability="trending", target=None)
        seen_shortcodes: set[str] = set()
        maximum_calls = max(1, min(5, limit // 10 or 1))
        for call_index in range(maximum_calls):
            page_result = self.call_endpoint("/reels/trending", {}, "trending", None)
            provider_result.raw_responses.extend(page_result.raw_responses)
            for media in page_result.items:
                if media.shortcode not in seen_shortcodes:
                    seen_shortcodes.add(media.shortcode)
                    provider_result.items.append(media)
        provider_result.cost_note = f"{maximum_calls} calls x 5 credits (trending is a repeating global sample, deduplicated)"
        return provider_result

    def fetch_hashtag_ranked(self, hashtag_name: str, limit: int, ranking: str, capability: str) -> ProviderResult:
        provider_result = self.call_endpoint(
            "/search/hashtag",
            {"hashtag": hashtag_name.lstrip("#"), "type": ranking, "max_items": limit},
            capability,
            hashtag_name,
        )
        provider_result.items = provider_result.items[:limit]
        return provider_result

    def fetch_hashtag(self, hashtag_name: str, limit: int) -> ProviderResult:
        return self.fetch_hashtag_ranked(hashtag_name, limit, "top", "hashtag")

    def fetch_hashtag_recent(self, hashtag_name: str, limit: int) -> ProviderResult:
        return self.fetch_hashtag_ranked(hashtag_name, limit, "recent", "hashtag_recent")

    def fetch_search(self, query: str, limit: int) -> ProviderResult:
        return self.call_endpoint("/search/reels", {"query": query}, "search", query, page_limit=max(1, limit // 20))

    def fetch_profile_posts(self, username: str, limit: int) -> ProviderResult:
        return self.call_endpoint("/profile/posts", {"handle": username.lstrip("@")}, "profile_posts", username, page_limit=max(1, limit // 12))

    def fetch_post(self, shortcode: str, limit: int) -> ProviderResult:
        return self.call_endpoint("/post", {"url": f"https://www.instagram.com/p/{shortcode}/"}, "post", shortcode)
