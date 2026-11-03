from datetime import datetime
from typing import Any

from ..models import InstagramMedia
from .base import InstagramDataProvider, ProviderResult

APIFY_ACTOR_ID = "apify~instagram-scraper"
APIFY_RUN_SYNC_URL = f"https://api.apify.com/v2/acts/{APIFY_ACTOR_ID}/run-sync-get-dataset-items"
APIFY_TYPE_NAMES = {"Image": "image", "Video": "video", "Sidecar": "carousel"}


def apify_item_to_model(apify_item: dict[str, Any]) -> InstagramMedia | None:
    shortcode = apify_item.get("shortCode")
    if not shortcode:
        return None
    raw_like_count = apify_item.get("likesCount")
    return InstagramMedia(
        shortcode=shortcode,
        url=apify_item.get("url") or f"https://www.instagram.com/p/{shortcode}/",
        media_type=APIFY_TYPE_NAMES.get(apify_item.get("type"), apify_item.get("type")),
        caption=apify_item.get("caption"),
        owner_username=apify_item.get("ownerUsername"),
        like_count=raw_like_count if isinstance(raw_like_count, int) and raw_like_count >= 0 else None,
        comment_count=apify_item.get("commentsCount"),
        view_count=apify_item.get("videoPlayCount") or apify_item.get("videoViewCount"),
        taken_at=apify_item.get("timestamp"),
        hashtags=apify_item.get("hashtags") or [],
        source_strategy="provider_apify",
    )


class ApifyProvider(InstagramDataProvider):
    provider_name = "apify"
    api_key_environment_variable = "APIFY_TOKEN"
    supported_capabilities = ("hashtag", "search", "profile_posts", "post")

    def run_actor(self, actor_input: dict[str, Any], capability: str, target: str | None) -> ProviderResult:
        dataset_items = self.send_request(
            "POST",
            APIFY_RUN_SYNC_URL,
            json=actor_input,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        normalized_items = [media for media in (apify_item_to_model(item) for item in dataset_items) if media]
        return ProviderResult(
            provider_name=self.provider_name,
            capability=capability,
            target=target,
            items=normalized_items,
            raw_responses=[dataset_items],
            cost_note=f"pay per result: {len(dataset_items)} results (~$1.50-2.70 per 1k), billed {datetime.now().date()}",
        )

    def fetch_hashtag(self, hashtag_name: str, limit: int) -> ProviderResult:
        hashtag_url = f"https://www.instagram.com/explore/tags/{hashtag_name.lstrip('#')}/"
        return self.run_actor({"directUrls": [hashtag_url], "resultsType": "posts", "resultsLimit": limit}, "hashtag", hashtag_name)

    def fetch_search(self, query: str, limit: int) -> ProviderResult:
        return self.run_actor({"search": query, "searchType": "hashtag", "searchLimit": 1, "resultsType": "posts", "resultsLimit": limit}, "search", query)

    def fetch_profile_posts(self, username: str, limit: int) -> ProviderResult:
        profile_url = f"https://www.instagram.com/{username.lstrip('@')}/"
        return self.run_actor({"directUrls": [profile_url], "resultsType": "posts", "resultsLimit": limit}, "profile_posts", username)

    def fetch_post(self, shortcode: str, limit: int) -> ProviderResult:
        post_url = f"https://www.instagram.com/p/{shortcode}/"
        return self.run_actor({"directUrls": [post_url], "resultsType": "posts", "resultsLimit": 1}, "post", shortcode)
