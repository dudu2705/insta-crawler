import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .models import InstagramMedia, utc_now_iso

RANKINGS = ("top", "recent")


class RankingNotSupportedError(RuntimeError):
    pass


HashtagFetcher = Callable[[str, str, int], list[InstagramMedia]]


@dataclass
class BatchItem:
    media: InstagramMedia
    hashtag_sources: list[str] = field(default_factory=list)
    ranking_sources: list[str] = field(default_factory=list)
    age_hours: float | None = None
    engagement: int = 0
    engagement_per_hour: float = 0.0
    views_per_hour: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.media.to_dict(),
            "hashtag_sources": self.hashtag_sources,
            "ranking_sources": self.ranking_sources,
            "age_hours": self.age_hours,
            "engagement": self.engagement,
            "engagement_per_hour": self.engagement_per_hour,
            "views_per_hour": self.views_per_hour,
        }


def parse_iso_timestamp(timestamp_text: str | None) -> datetime | None:
    if not timestamp_text:
        return None
    try:
        parsed_timestamp = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed_timestamp if parsed_timestamp.tzinfo else parsed_timestamp.replace(tzinfo=timezone.utc)


def score_item(batch_item: BatchItem, reference_time: datetime) -> None:
    media = batch_item.media
    batch_item.engagement = (media.like_count or 0) + (media.comment_count or 0)
    published_at = parse_iso_timestamp(media.taken_at)
    if published_at is None:
        return
    batch_item.age_hours = round(max((reference_time - published_at).total_seconds() / 3600, 0), 2)
    effective_age_hours = max(batch_item.age_hours, 1.0)
    batch_item.engagement_per_hour = round(batch_item.engagement / effective_age_hours, 2)
    if media.view_count is not None:
        batch_item.views_per_hour = round(media.view_count / effective_age_hours, 2)


def build_summary(batch_items: list[BatchItem], hashtags: list[str]) -> dict[str, Any]:
    per_hashtag_counts = {hashtag: sum(hashtag in item.hashtag_sources for item in batch_items) for hashtag in hashtags}
    return {
        "total_posts": len(batch_items),
        "posts_per_hashtag": per_hashtag_counts,
        "total_likes": sum(item.media.like_count or 0 for item in batch_items),
        "total_comments": sum(item.media.comment_count or 0 for item in batch_items),
        "total_views": sum(item.media.view_count or 0 for item in batch_items),
        "posts_with_views": sum(item.media.view_count is not None for item in batch_items),
        "posts_with_timestamp": sum(item.age_hours is not None for item in batch_items),
        "top_posts": [
            {
                "url": item.media.url,
                "owner": item.media.owner_username,
                "engagement_per_hour": item.engagement_per_hour,
                "hashtags": item.hashtag_sources,
            }
            for item in batch_items[:10]
        ],
    }


def run_hashtag_batch(
    method_name: str,
    hashtag_fetcher: HashtagFetcher,
    hashtags: list[str],
    rankings: tuple[str, ...] = RANKINGS,
    limit_per_query: int = 30,
    pause_seconds: float = 2.0,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    collected_items: dict[str, BatchItem] = {}
    query_log: list[dict[str, Any]] = []
    started_at = time.time()
    for hashtag in hashtags:
        for ranking in rankings:
            query_started_at = time.time()
            try:
                media_items = hashtag_fetcher(hashtag, ranking, limit_per_query)
            except RankingNotSupportedError as unsupported_error:
                query_log.append({"hashtag": hashtag, "ranking": ranking, "status": "skipped", "detail": str(unsupported_error)})
                log(f"  #{hashtag:<16} {ranking:<7} skipped  ({unsupported_error})")
                continue
            except Exception as fetch_error:
                query_log.append({"hashtag": hashtag, "ranking": ranking, "status": "error", "detail": f"{type(fetch_error).__name__}: {fetch_error}"[:300]})
                log(f"  #{hashtag:<16} {ranking:<7} ERROR    {type(fetch_error).__name__}: {str(fetch_error)[:120]}")
                continue
            new_count = 0
            for media in media_items:
                batch_item = collected_items.get(media.shortcode)
                if batch_item is None:
                    batch_item = BatchItem(media=media)
                    collected_items[media.shortcode] = batch_item
                    new_count += 1
                if hashtag not in batch_item.hashtag_sources:
                    batch_item.hashtag_sources.append(hashtag)
                if ranking not in batch_item.ranking_sources:
                    batch_item.ranking_sources.append(ranking)
            query_log.append({"hashtag": hashtag, "ranking": ranking, "status": "ok", "results": len(media_items), "new": new_count, "seconds": round(time.time() - query_started_at, 2)})
            log(f"  #{hashtag:<16} {ranking:<7} {len(media_items):>4} results ({new_count} new)")
            if pause_seconds:
                time.sleep(pause_seconds)
    reference_time = datetime.now(timezone.utc)
    for batch_item in collected_items.values():
        score_item(batch_item, reference_time)
    sorted_items = sorted(collected_items.values(), key=lambda item: (item.engagement_per_hour, item.engagement), reverse=True)
    failed_queries = [entry for entry in query_log if entry["status"] == "error"]
    return {
        "metadata": {
            "method": method_name,
            "hashtags": hashtags,
            "rankings": list(rankings),
            "limit_per_query": limit_per_query,
            "fetched_at": utc_now_iso(),
            "duration_seconds": round(time.time() - started_at, 1),
            "partial": bool(failed_queries),
        },
        "queries": query_log,
        "summary": build_summary(sorted_items, hashtags),
        "posts": [item.to_dict() for item in sorted_items],
    }


def private_api_hashtag_fetcher(private_crawler) -> HashtagFetcher:
    ranking_methods = {"top": private_crawler.hashtag_top, "recent": private_crawler.hashtag_recent, "reels": private_crawler.hashtag_reels}

    def fetch(hashtag: str, ranking: str, limit: int) -> list[InstagramMedia]:
        if ranking not in ranking_methods:
            raise RankingNotSupportedError(f"instagrapi has no {ranking} hashtag feed")
        return ranking_methods[ranking](hashtag, limit)
    return fetch


def provider_hashtag_fetcher(provider) -> HashtagFetcher:
    def fetch(hashtag: str, ranking: str, limit: int) -> list[InstagramMedia]:
        capability = "hashtag" if ranking == "top" else f"hashtag_{ranking}"
        if capability not in provider.supported_capabilities:
            raise RankingNotSupportedError(f"{provider.provider_name} has no {ranking} hashtag feed")
        return provider.run(capability, hashtag, limit).items
    return fetch
