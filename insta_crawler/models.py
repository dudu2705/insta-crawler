from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class InstagramMedia:
    shortcode: str
    url: str
    media_type: str | None = None
    caption: str | None = None
    owner_username: str | None = None
    like_count: int | None = None
    comment_count: int | None = None
    view_count: int | None = None
    taken_at: str | None = None
    hashtags: list[str] = field(default_factory=list)
    source_strategy: str | None = None
    captured_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InstagramProfile:
    username: str
    full_name: str | None = None
    biography: str | None = None
    follower_count: int | None = None
    following_count: int | None = None
    post_count: int | None = None
    is_verified: bool | None = None
    is_private: bool | None = None
    user_id: str | None = None
    recent_media: list[InstagramMedia] = field(default_factory=list)
    source_strategy: str | None = None
    captured_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
