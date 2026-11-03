from typing import Any, Callable

from .browser_capture import merge_media
from .crawler import InstagramLoggedOutCrawler
from .http_client import INSTAGRAM_BASE_URL, InstagramBlockedError, LoginWallError
from .models import InstagramMedia


def needs_engagement(media: InstagramMedia) -> bool:
    return media.like_count is None or media.comment_count is None


def enrich_with_logged_out_posts(
    media_items: list[InstagramMedia],
    logged_out_crawler: InstagramLoggedOutCrawler,
    enrich_limit: int,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    enrichment_report: dict[str, Any] = {"method": "logged_out_post_page", "requested": 0, "enriched": 0, "failed": 0, "failures": [], "stopped_reason": None}
    candidates = [media for media in media_items if needs_engagement(media)][:enrich_limit]
    enrichment_report["requested"] = len(candidates)
    for candidate_index, media in enumerate(candidates, start=1):
        post_url = f"{INSTAGRAM_BASE_URL}/p/{media.shortcode}/"
        detail_media = None
        try:
            detail_media = logged_out_crawler.post_from_page(media.shortcode, post_url) or logged_out_crawler.post_from_embed(media.shortcode)
        except (InstagramBlockedError, LoginWallError) as blocking_error:
            enrichment_report["stopped_reason"] = str(blocking_error)
            log(f"  enrichment stopped at {candidate_index}/{len(candidates)}: {blocking_error}")
            break
        except Exception as fetch_error:
            enrichment_report["failures"].append({"shortcode": media.shortcode, "error": f"{type(fetch_error).__name__}: {fetch_error}"[:160]})
        if detail_media:
            merge_media(media, detail_media)
            enrichment_report["enriched"] += 1
        else:
            enrichment_report["failed"] += 1
        log(f"  enrich {candidate_index}/{len(candidates)} {media.shortcode}: likes={media.like_count} comments={media.comment_count} views={media.view_count}")
    return enrichment_report
