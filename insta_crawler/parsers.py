import html
import re
from datetime import datetime, timezone
from typing import Any

from .json_tools import unwrap_logged_out_gate, walk_json
from .models import InstagramMedia, InstagramProfile

HASHTAG_PATTERN = re.compile(r"#(\w+)", re.UNICODE)
OG_DESCRIPTION_PATTERN = re.compile(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"', re.IGNORECASE)
OG_COUNTS_PATTERN = re.compile(
    r"([\d.,]+[KMB]?)\s+Followers,\s+([\d.,]+[KMB]?)\s+Following,\s+([\d.,]+[KMB]?)\s+Posts",
    re.IGNORECASE,
)
MEDIA_TYPE_NAMES = {1: "image", 2: "video", 8: "carousel"}
TYPENAME_TO_MEDIA_TYPE = {
    "GraphImage": "image",
    "GraphVideo": "video",
    "GraphSidecar": "carousel",
    "XDTGraphImage": "image",
    "XDTGraphVideo": "video",
    "XDTGraphSidecar": "carousel",
}


def first_present(source: dict, *key_paths: str) -> Any:
    for key_path in key_paths:
        current_value: Any = source
        for key_part in key_path.split("."):
            if not isinstance(current_value, dict) or key_part not in current_value:
                current_value = None
                break
            current_value = current_value[key_part]
        if current_value is not None:
            return current_value
    return None


def parse_abbreviated_count(count_text: str | None) -> int | None:
    if not count_text:
        return None
    normalized_text = count_text.replace(",", "").strip().upper()
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    suffix = normalized_text[-1]
    if suffix in multipliers:
        return int(float(normalized_text[:-1]) * multipliers[suffix])
    return int(float(normalized_text))


def timestamp_to_iso(raw_timestamp: Any) -> str | None:
    if raw_timestamp is None:
        return None
    return datetime.fromtimestamp(int(raw_timestamp), tz=timezone.utc).isoformat()


def extract_caption_text(media_node: dict) -> str | None:
    caption_value = media_node.get("caption")
    if isinstance(caption_value, dict):
        return caption_value.get("text")
    if isinstance(caption_value, str):
        return caption_value
    caption_edges = first_present(media_node, "edge_media_to_caption.edges") or []
    if caption_edges:
        return first_present(caption_edges[0], "node.text")
    return None


def looks_like_media_node(candidate: dict) -> bool:
    has_identifier = bool(candidate.get("shortcode") or candidate.get("code"))
    has_engagement = any(
        engagement_key in candidate
        for engagement_key in ("like_count", "edge_media_preview_like", "edge_liked_by", "comment_count", "edge_media_to_comment", "play_count", "video_view_count")
    )
    return has_identifier and has_engagement


def normalize_media_node(media_node: dict, source_strategy: str) -> InstagramMedia:
    shortcode = media_node.get("shortcode") or media_node.get("code")
    media_type = TYPENAME_TO_MEDIA_TYPE.get(media_node.get("__typename")) or MEDIA_TYPE_NAMES.get(media_node.get("media_type"))
    caption_text = extract_caption_text(media_node)
    return InstagramMedia(
        shortcode=shortcode,
        url=f"https://www.instagram.com/p/{shortcode}/",
        media_type=media_type,
        caption=caption_text,
        owner_username=first_present(media_node, "owner.username", "user.username"),
        like_count=first_present(media_node, "like_count", "edge_media_preview_like.count", "edge_liked_by.count"),
        comment_count=first_present(media_node, "comment_count", "edge_media_to_comment.count", "edge_media_to_parent_comment.count"),
        view_count=first_present(media_node, "play_count", "ig_play_count", "video_play_count", "view_count", "video_view_count"),
        taken_at=timestamp_to_iso(first_present(media_node, "taken_at", "taken_at_timestamp")),
        hashtags=HASHTAG_PATTERN.findall(caption_text or ""),
        source_strategy=source_strategy,
    )


def find_media_nodes(payload: Any, source_strategy: str) -> list[InstagramMedia]:
    collected_media: dict[str, InstagramMedia] = {}
    for json_object in walk_json(unwrap_logged_out_gate(payload)):
        if not looks_like_media_node(json_object):
            continue
        normalized_media = normalize_media_node(json_object, source_strategy)
        existing_media = collected_media.get(normalized_media.shortcode)
        if existing_media is None or count_filled_fields(normalized_media) > count_filled_fields(existing_media):
            collected_media[normalized_media.shortcode] = normalized_media
    return list(collected_media.values())


def count_filled_fields(media: InstagramMedia) -> int:
    return sum(field_value not in (None, [], "") for field_value in media.to_dict().values())


def looks_like_user_node(candidate: dict, username: str) -> bool:
    if (candidate.get("username") or "").lower() != username.lower():
        return False
    return any(count_key in candidate for count_key in ("follower_count", "edge_followed_by", "media_count", "edge_owner_to_timeline_media"))


def find_profile(payload: Any, username: str, source_strategy: str) -> InstagramProfile | None:
    for json_object in walk_json(unwrap_logged_out_gate(payload)):
        if not looks_like_user_node(json_object, username):
            continue
        timeline_edges = first_present(json_object, "edge_owner_to_timeline_media.edges") or []
        recent_media = [normalize_media_node(edge["node"], source_strategy) for edge in timeline_edges if isinstance(edge.get("node"), dict)]
        return InstagramProfile(
            username=json_object["username"],
            full_name=json_object.get("full_name"),
            biography=json_object.get("biography"),
            follower_count=first_present(json_object, "follower_count", "edge_followed_by.count"),
            following_count=first_present(json_object, "following_count", "edge_follow.count"),
            post_count=first_present(json_object, "media_count", "edge_owner_to_timeline_media.count"),
            is_verified=json_object.get("is_verified"),
            is_private=json_object.get("is_private"),
            user_id=str(json_object.get("id") or json_object.get("pk") or "") or None,
            recent_media=recent_media,
            source_strategy=source_strategy,
        )
    return None


def parse_profile_from_meta_tags(html_text: str, username: str) -> InstagramProfile | None:
    description_match = OG_DESCRIPTION_PATTERN.search(html_text)
    if not description_match:
        return None
    description_text = html.unescape(description_match.group(1))
    counts_match = OG_COUNTS_PATTERN.search(description_text)
    if not counts_match:
        return None
    follower_text, following_text, post_text = counts_match.groups()
    return InstagramProfile(
        username=username,
        follower_count=parse_abbreviated_count(follower_text),
        following_count=parse_abbreviated_count(following_text),
        post_count=parse_abbreviated_count(post_text),
        source_strategy="profile_meta_tags",
    )
