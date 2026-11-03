import json

from insta_crawler.crawler import InstagramLoggedOutCrawler
from insta_crawler.http_client import FetchResult
from insta_crawler.json_tools import extract_embedded_json_blobs
from insta_crawler.parsers import find_media_nodes, find_profile, parse_abbreviated_count, parse_profile_from_meta_tags
from insta_crawler.web_tokens import extract_page_tokens, extract_relay_operations, extract_relay_provider_names, extract_script_urls

NEW_FORMAT_POST = {
    "data": {
        "xdt_api__v1__media__shortcode__web_info": {
            "items": [
                {
                    "code": "DdeRysim9zK",
                    "media_type": 2,
                    "like_count": 1520,
                    "comment_count": 41,
                    "play_count": 98000,
                    "taken_at": 1761900000,
                    "user": {"username": "someone"},
                    "caption": {"text": "Hà Nội mùa thu #hanoi #autumn"},
                }
            ]
        }
    }
}

LEGACY_GATED_POST = {
    "if_not_gated_logged_out": {
        "xdt_shortcode_media": {
            "__typename": "XDTGraphVideo",
            "shortcode": "ABC123",
            "edge_media_preview_like": {"count": 77},
            "edge_media_to_parent_comment": {"count": 5},
            "video_view_count": 3000,
            "taken_at_timestamp": 1761800000,
            "owner": {"username": "legacy_user"},
            "edge_media_to_caption": {"edges": [{"node": {"text": "old shape #retro"}}]},
        }
    }
}

WEB_PROFILE_INFO = {
    "data": {
        "user": {
            "id": "25025320",
            "username": "instagram",
            "full_name": "Instagram",
            "biography": "Discover what's new",
            "is_verified": True,
            "is_private": False,
            "edge_followed_by": {"count": 686584231},
            "edge_follow": {"count": 200},
            "edge_owner_to_timeline_media": {
                "count": 8000,
                "edges": [{"node": {"shortcode": "P1", "edge_liked_by": {"count": 10}, "edge_media_to_comment": {"count": 2}, "__typename": "GraphImage"}}],
            },
        }
    }
}


def test_new_format_post_is_normalized():
    media_items = find_media_nodes(NEW_FORMAT_POST, "test")
    assert len(media_items) == 1
    media = media_items[0]
    assert media.shortcode == "DdeRysim9zK"
    assert media.media_type == "video"
    assert (media.like_count, media.comment_count, media.view_count) == (1520, 41, 98000)
    assert media.owner_username == "someone"
    assert media.hashtags == ["hanoi", "autumn"]
    assert media.taken_at.startswith("2025-10-31")


def test_logged_out_gate_and_legacy_fields():
    media = find_media_nodes(LEGACY_GATED_POST, "test")[0]
    assert media.shortcode == "ABC123"
    assert media.media_type == "video"
    assert (media.like_count, media.comment_count, media.view_count) == (77, 5, 3000)
    assert media.caption == "old shape #retro"


def test_profile_from_web_profile_info():
    profile = find_profile(WEB_PROFILE_INFO, "Instagram", "test")
    assert profile.follower_count == 686584231
    assert profile.post_count == 8000
    assert profile.user_id == "25025320"
    assert profile.recent_media[0].shortcode == "P1"


def test_profile_from_meta_tags():
    page_html = '<meta property="og:description" content="1.2M Followers, 350 Following, 4,210 Posts - See Instagram photos" />'
    profile = parse_profile_from_meta_tags(page_html, "someone")
    assert (profile.follower_count, profile.following_count, profile.post_count) == (1_200_000, 350, 4210)
    assert parse_abbreviated_count("686M") == 686_000_000


def test_embedded_json_blobs_from_html():
    page_html = f'<html><script type="application/json" data-sjs>{json.dumps(NEW_FORMAT_POST)}</script><script type="application/json">broken</script></html>'
    blobs = extract_embedded_json_blobs(page_html)
    assert len(blobs) == 1
    assert find_media_nodes(blobs[0], "test")[0].shortcode == "DdeRysim9zK"


def test_token_and_doc_id_harvesting():
    page_html = (
        '<script src="https://static.cdninstagram.com/rsrc.php/v4/yA/r/abc.js?_nc_x=1&amp;y=2"></script>'
        '["LSD",[],{"token":"AVq_lsd_token"},323]"csrf_token":"csrf123""APP_ID":"936619743392459"'
    )
    bundle_javascript = (
        '__d("PolarisPostActionLoadPostQueryQuery_instagramRelayOperation",[],(function(a,b,c,d,e,f){e.exports="9510064595728286"}),null);'
        '__d("PolarisProfilePostsQuery_instagramRelayOperation",[],(function(a,b,c,d,e,f){e.exports="7950326061742207"}),null);'
        'x.__relay_internal__pv__PolarisIsLoggedInrelayprovider;y.__relay_internal__pv__PolarisSEOIsCrawlerrelayprovider'
    )
    web_tokens = extract_page_tokens(page_html)
    assert web_tokens.lsd_token == "AVq_lsd_token"
    assert web_tokens.csrf_token == "csrf123"
    assert extract_script_urls(page_html) == ["https://static.cdninstagram.com/rsrc.php/v4/yA/r/abc.js?_nc_x=1&y=2"]
    web_tokens.doc_ids = extract_relay_operations(bundle_javascript)
    web_tokens.relay_provider_names = extract_relay_provider_names(bundle_javascript)
    assert web_tokens.find_doc_id("PolarisPostActionLoadPostQuery") == ("PolarisPostActionLoadPostQueryQuery", "9510064595728286")
    assert web_tokens.find_doc_id("PolarisProfilePostsQuery")[1] == "7950326061742207"
    assert web_tokens.relay_provider_variables() == {
        "__relay_internal__pv__PolarisIsLoggedInrelayprovider": False,
        "__relay_internal__pv__PolarisSEOIsCrawlerrelayprovider": False,
    }


def test_harvest_handles_escaped_urls_inline_queries_and_spaced_modules():
    page_html = (
        '{"bootloader":{"r":"https:\\/\\/static.cdninstagram.com\\/rsrc.php\\/v4iX\\/y1\\/r\\/Zm9v.js?_nc_x=Ij3Wp8lg5Kz"}}'
        '<link rel="preload" href="https://static.cdninstagram.com/rsrc.php/v4/yQ/r/bar.js" as="script"/>'
        '{"__bbox":{"define":[],"require":[["RelayPrefetchedStreamCache","next",[],["adp_PolarisPostRootQueryRelayPreloader",'
        '{"queryName":"PolarisPostRootQuery","variables":{"shortcode":"x"},"queryID":"24368985919464652"}]]]}}'
    )
    spaced_bundle = '__d( "PolarisProfilePostsQuery_instagramRelayOperation" , [] , function (a, b, c) { e.exports = "8759034877476257" } , null);'
    assert extract_script_urls(page_html) == [
        "https://static.cdninstagram.com/rsrc.php/v4/yQ/r/bar.js",
        "https://static.cdninstagram.com/rsrc.php/v4iX/y1/r/Zm9v.js?_nc_x=Ij3Wp8lg5Kz",
    ]
    web_tokens = extract_page_tokens(page_html)
    assert web_tokens.find_doc_id("PolarisPostRootQuery") == ("PolarisPostRootQuery", "24368985919464652")
    assert extract_relay_operations(spaced_bundle) == {"PolarisProfilePostsQuery": "8759034877476257"}


class FakeHttpClient:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages

    def get_page(self, url):
        if url not in self.pages:
            raise RuntimeError(f"unexpected url {url}")
        return FetchResult(url=url, status_code=200, text=self.pages[url], final_url=url)


class FakeGraphqlClient(FakeHttpClient):
    def __init__(self, pages, graphql_responses):
        super().__init__(pages)
        self.graphql_responses = graphql_responses
        self.sent_variables = []

    def get_static_asset(self, url):
        return FetchResult(url=url, status_code=200, text=self.pages[url], final_url=url)

    def api_headers(self, *arguments, **keyword_arguments):
        return {}

    def post_graphql(self, form_values, header_values):
        self.sent_variables.append(json.loads(form_values["variables"]))
        return FetchResult(url="graphql", status_code=200, text=json.dumps(self.graphql_responses.pop(0)), final_url="graphql")


def test_profile_feed_tries_variable_candidates_until_media(tmp_path):
    profile_url = "https://www.instagram.com/instagram/"
    bundle_url = "https://static.cdninstagram.com/rsrc.php/v4/yA/r/feed.js"
    profile_page = (
        f'<script src="{bundle_url}"></script>["LSD",[],{{"token":"lsd1"}},1]'
        f'<script type="application/json">{json.dumps(WEB_PROFILE_INFO)}</script>'
    )
    bundle_javascript = '__d("PolarisLoggedOutDesktopWWWProfilePostsTabContentQuery_instagramRelayOperation",[],(function(a,b,c,d,e,f){e.exports="29483846714553007"}),null);'
    fake_client = FakeGraphqlClient(
        {profile_url: profile_page, bundle_url: bundle_javascript},
        [{"errors": [{"message": "Missing required variable id"}]}, NEW_FORMAT_POST],
    )
    crawler = InstagramLoggedOutCrawler(http_client=fake_client, token_cache_path=tmp_path / "tokens.json")
    media_items = crawler.profile_posts_from_graphql("instagram", profile_url, 12)
    assert [media.shortcode for media in media_items] == ["DdeRysim9zK"]
    assert "username" in fake_client.sent_variables[0]
    assert fake_client.sent_variables[1] == {"id": "25025320", "first": 12}


def test_post_strategy_fallback_records_attempts(tmp_path):
    embed_url = "https://www.instagram.com/p/DdeRysim9zK/embed/captioned/"
    post_url = "https://www.instagram.com/p/DdeRysim9zK/"
    fake_client = FakeHttpClient({
        post_url: "<html>login popup, no data</html>",
        embed_url: f'<script type="application/json">{json.dumps(NEW_FORMAT_POST)}</script>',
    })
    crawler = InstagramLoggedOutCrawler(http_client=fake_client, token_cache_path=tmp_path / "tokens.json")
    outcome = crawler.fetch_post("DdeRysim9zK").to_dict()
    attempt_names = [(attempt["strategy_name"], attempt["succeeded"]) for attempt in outcome["attempts"]]
    assert attempt_names == [("post_page_embedded_json", False), ("post_graphql_doc_id", False), ("post_embed_page", True)]
    assert outcome["result"]["like_count"] == 1520
