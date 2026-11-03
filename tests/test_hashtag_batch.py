from datetime import datetime, timedelta, timezone

from insta_crawler.hashtag_batch import RankingNotSupportedError, provider_hashtag_fetcher, run_hashtag_batch
from insta_crawler.models import InstagramMedia
from insta_crawler.providers.hikerapi import HikerApiProvider
from insta_crawler.providers.socialcrawl import SocialCrawlProvider


def hours_ago(hour_count: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hour_count)).isoformat()


def make_media(shortcode: str, like_count: int, age_hours: float, view_count: int | None = None) -> InstagramMedia:
    return InstagramMedia(shortcode=shortcode, url=f"https://www.instagram.com/p/{shortcode}/", like_count=like_count, comment_count=0, view_count=view_count, taken_at=hours_ago(age_hours))


def test_batch_dedupes_ranks_and_records_skips_and_errors():
    fake_feeds = {
        ("balayage", "top"): [make_media("OLD_BIG", 10_000, 200), make_media("SHARED", 500, 5)],
        ("balayage", "recent"): [make_media("FRESH", 300, 1, view_count=6000)],
        ("haircolor", "top"): [make_media("SHARED", 500, 5)],
    }

    def fake_fetcher(hashtag, ranking, limit):
        if hashtag == "hairtok":
            raise RuntimeError("HTTP 500")
        if hashtag == "haircolor" and ranking == "recent":
            raise RankingNotSupportedError("no recent feed")
        return fake_feeds.get((hashtag, ranking), [])

    collection = run_hashtag_batch("fake", fake_fetcher, ["balayage", "haircolor", "hairtok"], ("top", "recent"), 30, 0, log=lambda message: None)
    shortcodes_in_order = [post["shortcode"] for post in collection["posts"]]
    assert shortcodes_in_order == ["FRESH", "SHARED", "OLD_BIG"]
    shared_post = collection["posts"][1]
    assert shared_post["hashtag_sources"] == ["balayage", "haircolor"]
    assert collection["posts"][0]["views_per_hour"] == 6000.0
    statuses = {(entry["hashtag"], entry["ranking"]): entry["status"] for entry in collection["queries"]}
    assert statuses[("haircolor", "recent")] == "skipped"
    assert statuses[("hairtok", "top")] == "error"
    assert collection["metadata"]["partial"] is True
    assert collection["summary"]["posts_per_hashtag"] == {"balayage": 3, "haircolor": 1, "hairtok": 0}


def test_socialcrawl_recent_sends_type_recent(monkeypatch):
    monkeypatch.setenv("SOCIALCRAWL_API_KEY", "test-key")
    provider = SocialCrawlProvider()
    sent_parameters = []

    def fake_send_request(method, url, **request_options):
        sent_parameters.append((url, request_options["params"]))
        return {"data": {"items": [{"post": {"shortcode": "R1"}, "engagement": {"likes": 3}, "published_at": hours_ago(2)}]}, "pagination": {"has_more": False}, "credits_used": 1}

    provider.send_request = fake_send_request
    fetcher = provider_hashtag_fetcher(provider)
    assert [media.shortcode for media in fetcher("balayage", "recent", 25)] == ["R1"]
    assert sent_parameters[0][0].endswith("/v1/instagram/search/hashtag")
    assert sent_parameters[0][1] == {"hashtag": "balayage", "type": "recent", "max_items": 25}
    fetcher("balayage", "top", 25)
    assert sent_parameters[1][1]["type"] == "top"


def test_private_api_fetcher_routes_rankings():
    from insta_crawler.hashtag_batch import private_api_hashtag_fetcher

    class FakePrivateCrawler:
        def hashtag_top(self, hashtag, limit):
            return [make_media(f"TOP_{hashtag}", 1, 1)]

        def hashtag_recent(self, hashtag, limit):
            return [make_media(f"RECENT_{hashtag}", 1, 1)]

        def hashtag_reels(self, hashtag, limit):
            return []

    fetcher = private_api_hashtag_fetcher(FakePrivateCrawler())
    assert fetcher("balayage", "top", 5)[0].shortcode == "TOP_balayage"
    assert fetcher("balayage", "recent", 5)[0].shortcode == "RECENT_balayage"
    try:
        fetcher("balayage", "explore", 5)
        raised = False
    except RankingNotSupportedError:
        raised = True
    assert raised


def test_provider_without_recent_is_skipped(monkeypatch):
    monkeypatch.setenv("HIKERAPI_KEY", "test-key")
    fetcher = provider_hashtag_fetcher(HikerApiProvider())
    try:
        fetcher("balayage", "recent", 10)
        raised = False
    except RankingNotSupportedError:
        raised = True
    assert raised
