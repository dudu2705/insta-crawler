from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from insta_crawler.private_api import instagrapi_media_to_model
from insta_crawler.providers import CapabilityNotSupportedError
from insta_crawler.providers.apify import ApifyProvider
from insta_crawler.providers.hikerapi import HikerApiProvider, split_chunk_response
from insta_crawler.providers.socialcrawl import SocialCrawlProvider
from insta_crawler.session_pool import SessionPool, build_target_url

APIFY_DATASET = [
    {
        "shortCode": "DZxvMgyH8yR",
        "url": "https://www.instagram.com/p/DZxvMgyH8yR/",
        "type": "Image",
        "caption": "Lava lake #volcano",
        "hashtags": ["volcano"],
        "likesCount": 55952,
        "commentsCount": 110,
        "timestamp": "2026-06-20T10:00:04.000Z",
        "ownerUsername": "natgeo",
    },
    {"shortCode": "HIDDEN", "type": "Video", "likesCount": -1, "videoPlayCount": 500},
    {"error": "no_items"},
]

SOCIALCRAWL_ENVELOPE_PAGE_ONE = {
    "success": True,
    "credits_used": 1,
    "data": {
        "items": [
            {
                "post": {"id": "1", "url": "https://www.instagram.com/reel/REEL111/", "content": {"text": "Phở sáng #hanoi"}},
                "author": {"handle": "foodie"},
                "engagement": {"likes": 900, "comments": 30, "views": 45000},
                "published_at": "2026-10-30T01:00:00Z",
            }
        ]
    },
    "pagination": {"has_more": True, "next_cursor": "CURSOR2"},
}
SOCIALCRAWL_ENVELOPE_PAGE_TWO = {
    "success": True,
    "credits_used": 1,
    "data": {"items": [{"post": {"shortcode": "REEL222"}, "engagement": {"likes": 10}}]},
    "pagination": {"has_more": False},
}

HIKERAPI_CHUNK = [
    [
        {"code": "HK1", "media_type": 2, "like_count": 100, "comment_count": 4, "play_count": 9000, "taken_at": 1761900000, "user": {"username": "a"}, "caption": {"text": "#vietnam"}},
        {"code": "HK2", "media_type": 1, "like_count": 50, "comment_count": 1, "taken_at": 1761800000, "user": {"username": "b"}},
    ],
    "WyIzMzBmIl0=",
]


def build_provider(provider_class, monkeypatch, responses):
    monkeypatch.setenv(provider_class.api_key_environment_variable, "test-key")
    provider = provider_class()
    recorded_calls = []

    def fake_send_request(method, url, **request_options):
        recorded_calls.append((method, url, request_options))
        return responses.pop(0)

    provider.send_request = fake_send_request
    return provider, recorded_calls


def test_apify_hashtag_normalizes_and_builds_input(monkeypatch):
    provider, recorded_calls = build_provider(ApifyProvider, monkeypatch, [APIFY_DATASET])
    provider_result = provider.run("hashtag", "#volcano", 5)
    method, url, request_options = recorded_calls[0]
    assert url.endswith("/acts/apify~instagram-scraper/run-sync-get-dataset-items")
    assert request_options["json"] == {"directUrls": ["https://www.instagram.com/explore/tags/volcano/"], "resultsType": "posts", "resultsLimit": 5}
    assert request_options["headers"]["Authorization"] == "Bearer test-key"
    assert [item.shortcode for item in provider_result.items] == ["DZxvMgyH8yR", "HIDDEN"]
    assert provider_result.items[0].like_count == 55952
    assert provider_result.items[1].like_count is None
    assert provider_result.items[1].view_count == 500


def test_apify_has_no_trending(monkeypatch):
    provider, recorded_calls = build_provider(ApifyProvider, monkeypatch, [])
    with pytest.raises(CapabilityNotSupportedError):
        provider.run("trending", None, 10)


def test_socialcrawl_paginates_and_normalizes(monkeypatch):
    provider, recorded_calls = build_provider(SocialCrawlProvider, monkeypatch, [SOCIALCRAWL_ENVELOPE_PAGE_ONE, SOCIALCRAWL_ENVELOPE_PAGE_TWO])
    provider_result = provider.run("search", "phở", 40)
    assert recorded_calls[0][2]["headers"] == {"x-api-key": "test-key"}
    assert recorded_calls[1][2]["params"]["cursor"] == "CURSOR2"
    first_item = provider_result.items[0]
    assert (first_item.shortcode, first_item.owner_username, first_item.view_count) == ("REEL111", "foodie", 45000)
    assert first_item.hashtags == ["hanoi"]
    assert provider_result.items[1].shortcode == "REEL222"
    assert provider_result.cost_note.startswith("2 credits")


def test_socialcrawl_trending_deduplicates(monkeypatch):
    provider, recorded_calls = build_provider(SocialCrawlProvider, monkeypatch, [SOCIALCRAWL_ENVELOPE_PAGE_TWO, SOCIALCRAWL_ENVELOPE_PAGE_TWO])
    provider_result = provider.run("trending", None, 20)
    assert len(recorded_calls) == 2
    assert [item.shortcode for item in provider_result.items] == ["REEL222"]


def test_hikerapi_chunk_pagination(monkeypatch):
    provider, recorded_calls = build_provider(HikerApiProvider, monkeypatch, [HIKERAPI_CHUNK, [[], None]])
    provider_result = provider.run("hashtag", "vietnam", 10)
    assert recorded_calls[0][1] == "https://api.hikerapi.com/v1/hashtag/medias/top/chunk"
    assert recorded_calls[0][2]["headers"]["x-access-key"] == "test-key"
    assert recorded_calls[1][2]["params"]["max_id"] == "WyIzMzBmIl0="
    assert [item.shortcode for item in provider_result.items] == ["HK1", "HK2"]
    assert provider_result.items[0].view_count == 9000


def test_hikerapi_profile_resolves_user_id(monkeypatch):
    provider, recorded_calls = build_provider(HikerApiProvider, monkeypatch, [{"pk": "787132", "username": "natgeo"}, [HIKERAPI_CHUNK[0], None]])
    provider_result = provider.run("profile_posts", "natgeo", 5)
    assert recorded_calls[1][1].endswith("/v1/user/clips/chunk")
    assert recorded_calls[1][2]["params"]["user_id"] == "787132"
    assert len(provider_result.items) == 2


def test_split_chunk_response_variants():
    assert split_chunk_response([[1, 2], "next"]) == ([1, 2], "next")
    assert split_chunk_response([{"code": "x"}]) == ([{"code": "x"}], None)
    assert split_chunk_response({"items": [1], "next_page_id": "n"}) == ([1], "n")


def test_instagrapi_media_normalization():
    fake_media = SimpleNamespace(
        code="IG1",
        media_type=2,
        caption_text="Sunset #danang",
        like_count=10,
        comment_count=2,
        play_count=3000,
        view_count=None,
        taken_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        user=SimpleNamespace(username="burner_target"),
    )
    media = instagrapi_media_to_model(fake_media)
    assert (media.shortcode, media.media_type, media.view_count, media.owner_username) == ("IG1", "video", 3000, "burner_target")
    assert media.hashtags == ["danang"]
    assert media.taken_at.startswith("2026-10-01")


def test_target_urls():
    assert build_target_url("hashtag", "#phởhànội") == "https://www.instagram.com/explore/tags/ph%E1%BB%9Fh%C3%A0n%E1%BB%99i/"
    assert build_target_url("explore", None) == "https://www.instagram.com/explore/"
    assert build_target_url("search", "ăn vặt") == "https://www.instagram.com/explore/search/keyword/?q=%C4%83n%20v%E1%BA%B7t"
    with pytest.raises(ValueError):
        build_target_url("hashtag", None)


def test_session_pool_rotation_and_cooldown(tmp_path):
    for session_name in ("burner_a", "burner_b"):
        (tmp_path / f"{session_name}.json").write_text("{}")
    session_pool = SessionPool(tmp_path)
    first_session = session_pool.pick_session()
    session_pool.record_use(first_session.session_name, hit_challenge=False)
    second_session = session_pool.pick_session()
    assert second_session.session_name != first_session.session_name
    session_pool.record_use(second_session.session_name, hit_challenge=True)
    assert session_pool.pick_session() is None
    reloaded_pool = SessionPool(tmp_path)
    challenged_state = reloaded_pool.session_states[second_session.session_name]
    assert challenged_state.challenges == 1 and challenged_state.disabled_until > 0
    assert reloaded_pool.pick_session(ignore_cooldown=True) is not None


def test_browser_merge_and_metric_probe():
    from insta_crawler.browser_capture import merge_media, probe_metric_fields
    from insta_crawler.models import InstagramMedia
    grid_copy = InstagramMedia(shortcode="R1", url="u", like_count=862, comment_count=65)
    detail_copy = InstagramMedia(shortcode="R1", url="u", view_count=40000, media_type="video")
    merged_media = merge_media(grid_copy, detail_copy)
    assert (merged_media.like_count, merged_media.comment_count, merged_media.view_count, merged_media.media_type) == (862, 65, 40000, "video")
    captured_payloads = [{"data": {"items": [
        {"code": "R1", "like_count": 862, "comment_count": 65, "fb_play_count": None, "clips_metadata": {"view_count_new": 40000}},
        {"code": "R2", "like_count": 10, "comment_count": 1, "fb_play_count": 5},
    ]}}]
    field_statistics = probe_metric_fields(captured_payloads)
    assert field_statistics["clips_metadata.view_count_new"] == {"media_nodes": 1, "non_null": 1, "example": 40000}
    assert field_statistics["fb_play_count"]["non_null"] == 1 and field_statistics["fb_play_count"]["media_nodes"] == 2


def test_logged_out_enrichment_adds_likes_keeps_views_and_stops_on_block():
    from insta_crawler.enrichment import enrich_with_logged_out_posts
    from insta_crawler.http_client import InstagramBlockedError
    from insta_crawler.models import InstagramMedia

    grid_items = [
        InstagramMedia(shortcode="G1", url="u1", view_count=3091171),
        InstagramMedia(shortcode="G2", url="u2", view_count=500),
        InstagramMedia(shortcode="G3", url="u3", view_count=7),
    ]

    class FakeLoggedOutCrawler:
        def post_from_page(self, shortcode, post_url):
            if shortcode == "G2":
                raise InstagramBlockedError("HTTP 429 rate limited")
            return InstagramMedia(shortcode=shortcode, url=post_url, like_count=862, comment_count=65, view_count=999, media_type="video")

        def post_from_embed(self, shortcode):
            return None

    enrichment_report = enrich_with_logged_out_posts(grid_items, FakeLoggedOutCrawler(), 10, log=lambda message: None)
    assert (grid_items[0].like_count, grid_items[0].comment_count, grid_items[0].view_count) == (862, 65, 3091171)
    assert grid_items[1].like_count is None
    assert enrichment_report["enriched"] == 1 and "429" in enrichment_report["stopped_reason"]
