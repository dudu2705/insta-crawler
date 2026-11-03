import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .models import InstagramMedia

DEFAULT_PRIVATE_SESSION_DIRECTORY = Path("sessions/private_api")
MEDIA_TYPE_NAMES = {1: "image", 2: "video", 8: "carousel"}
HASHTAG_PATTERN = re.compile(r"#(\w+)", re.UNICODE)


def read_attribute(source: Any, attribute_name: str) -> Any:
    if isinstance(source, dict):
        return source.get(attribute_name)
    return getattr(source, attribute_name, None)


def instagrapi_media_to_model(media_object: Any, source_strategy: str = "private_api_instagrapi") -> InstagramMedia:
    shortcode = read_attribute(media_object, "code")
    caption_text = read_attribute(media_object, "caption_text") or ""
    taken_at_value = read_attribute(media_object, "taken_at")
    if isinstance(taken_at_value, datetime):
        taken_at_text = taken_at_value.isoformat()
    elif isinstance(taken_at_value, (int, float)):
        taken_at_text = datetime.fromtimestamp(taken_at_value).isoformat()
    else:
        taken_at_text = taken_at_value
    owner_object = read_attribute(media_object, "user")
    return InstagramMedia(
        shortcode=shortcode,
        url=f"https://www.instagram.com/p/{shortcode}/",
        media_type=MEDIA_TYPE_NAMES.get(read_attribute(media_object, "media_type")),
        caption=caption_text or None,
        owner_username=read_attribute(owner_object, "username") if owner_object else None,
        like_count=read_attribute(media_object, "like_count"),
        comment_count=read_attribute(media_object, "comment_count"),
        view_count=read_attribute(media_object, "play_count") or read_attribute(media_object, "view_count"),
        taken_at=taken_at_text,
        hashtags=HASHTAG_PATTERN.findall(caption_text),
        source_strategy=source_strategy,
    )


def prompt_for_challenge_code(username: str, choice: Any) -> str:
    return input(f"Instagram sent a verification code to {username} via {choice}. Enter it: ").strip()


PUBLIC_KEY_ENDPOINT = "https://i.instagram.com/api/v1/qe/sync/"
ENCRYPTION_KEY_ID_HEADER = "ig-set-password-encryption-key-id"
ENCRYPTION_PUBLIC_KEY_HEADER = "ig-set-password-encryption-pub-key"


def read_sessionid_from_browser_session(browser_session_path: Path) -> str | None:
    if not browser_session_path.exists():
        return None
    storage_state = json.loads(browser_session_path.read_text())
    for cookie in storage_state.get("cookies", []):
        if cookie.get("name") == "sessionid" and "instagram.com" in cookie.get("domain", ""):
            return cookie.get("value")
    return None


class PrivateApiCrawler:
    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        proxy_url: str | None = None,
        session_directory: Path = DEFAULT_PRIVATE_SESSION_DIRECTORY,
        minimum_delay_seconds: float = 3.0,
        maximum_delay_seconds: float = 8.0,
        sessionid: str | None = None,
        browser_session_path: Path | None = None,
    ):
        from instagrapi import Client
        self.username = username or os.environ.get("IG_USERNAME")
        self.password = password or os.environ.get("IG_PASSWORD")
        browser_session_value = os.environ.get("IG_BROWSER_SESSION")
        if browser_session_path is None and browser_session_value:
            browser_session_path = Path(browser_session_value)
        self.sessionid = sessionid or os.environ.get("IG_SESSIONID") or (read_sessionid_from_browser_session(browser_session_path) if browser_session_path else None)
        if not self.sessionid and not (self.username and self.password):
            raise RuntimeError(
                "Set IG_USERNAME + IG_PASSWORD, or IG_SESSIONID, or IG_BROWSER_SESSION=sessions/<name>.json (from login-browser) in .env. Burner account only."
            )
        session_key = self.username or (self.sessionid.split("%3A")[0].split(":")[0] if self.sessionid else "unknown")
        self.session_path = session_directory / f"{session_key}.json"
        self.client = Client()
        self.client.delay_range = [minimum_delay_seconds, maximum_delay_seconds]
        self.client.challenge_code_handler = prompt_for_challenge_code
        self.client.password_publickeys = self.resilient_password_publickeys
        if proxy_url:
            self.client.set_proxy(proxy_url)
        self.logged_in = False
        self.public_key_attempts: list[str] = []

    def resilient_password_publickeys(self) -> tuple[int, str]:
        self.public_key_attempts = []

        def read_key(response) -> tuple[int, str] | None:
            if response is None:
                return None
            key_id = response.headers.get(ENCRYPTION_KEY_ID_HEADER)
            public_key = response.headers.get(ENCRYPTION_PUBLIC_KEY_HEADER)
            if key_id and public_key:
                return int(key_id), public_key
            return None

        try:
            public_response = self.client.public.get(PUBLIC_KEY_ENDPOINT)
            found_key = read_key(public_response)
            if found_key:
                return found_key
            self.public_key_attempts.append(f"GET qe/sync: HTTP {public_response.status_code}")
        except Exception as public_error:
            self.public_key_attempts.append(f"GET qe/sync: {type(public_error).__name__}")
        app_style_calls = (
            ("POST qe/sync (app style)", lambda: self.client.sync_device_features(login=True)),
            ("POST launcher/sync (app style)", lambda: self.client.sync_launcher(login=True)),
        )
        for call_label, app_call in app_style_calls:
            call_error = None
            try:
                app_call()
            except Exception as raised_error:
                call_error = raised_error
            last_response = getattr(self.client, "last_response", None)
            found_key = read_key(last_response)
            if found_key:
                return found_key
            status_text = f"HTTP {last_response.status_code}" if last_response is not None else "no response"
            error_text = f" {type(call_error).__name__}: {str(call_error)[:80]}" if call_error else ""
            self.public_key_attempts.append(f"{call_label}: {status_text}{error_text}")
        raise RuntimeError("Instagram returned no password encryption key on any known route: " + " | ".join(self.public_key_attempts))

    def describe_public_key_failure(self) -> str:
        try:
            probe_response = self.client.public.get(PUBLIC_KEY_ENDPOINT)
        except Exception as probe_error:
            return f"probe of {PUBLIC_KEY_ENDPOINT} failed: {type(probe_error).__name__}: {probe_error}"
        interesting_headers = {name: value for name, value in probe_response.headers.items() if name.lower().startswith(("ig-", "x-ig", "content-type", "location"))}
        return f"{PUBLIC_KEY_ENDPOINT} -> HTTP {probe_response.status_code}, headers {interesting_headers}, body {probe_response.text[:200]!r}"

    def password_login(self) -> None:
        try:
            self.client.login(self.username, self.password)
        except TypeError as type_error:
            if "NoneType" not in str(type_error):
                raise
            raise RuntimeError(
                "Instagram did not return the password encryption key, so instagrapi cannot send the password. "
                f"What Instagram returned: {self.describe_public_key_failure()}."
            ) from type_error

    def login(self) -> None:
        from instagrapi.exceptions import LoginRequired
        if self.session_path.exists():
            self.client.load_settings(self.session_path)
            try:
                if self.sessionid and not self.password:
                    self.client.login_by_sessionid(self.sessionid)
                else:
                    self.password_login()
                self.client.get_timeline_feed()
            except LoginRequired:
                stored_uuids = self.client.get_settings().get("uuids", {})
                self.client.set_settings({})
                self.client.set_uuids(stored_uuids)
                self.fresh_login()
        else:
            self.fresh_login()
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.client.dump_settings(self.session_path)
        self.logged_in = True

    def fresh_login(self) -> None:
        if self.sessionid:
            self.client.login_by_sessionid(self.sessionid)
        else:
            self.password_login()

    def ensure_login(self) -> None:
        if not self.logged_in:
            self.login()

    def pause(self) -> None:
        time.sleep(random.uniform(*self.client.delay_range))

    def normalize(self, media_objects: Iterable[Any], source_strategy: str) -> list[InstagramMedia]:
        return [instagrapi_media_to_model(media_object, source_strategy) for media_object in media_objects if read_attribute(media_object, "code")]

    def hashtag_top(self, hashtag_name: str, amount: int = 27) -> list[InstagramMedia]:
        self.ensure_login()
        return self.normalize(self.client.hashtag_medias_top(hashtag_name.lstrip("#"), amount=amount), "private_api_hashtag_top")

    def hashtag_recent(self, hashtag_name: str, amount: int = 27) -> list[InstagramMedia]:
        self.ensure_login()
        return self.normalize(self.client.hashtag_medias_recent(hashtag_name.lstrip("#"), amount=amount), "private_api_hashtag_recent")

    def hashtag_reels(self, hashtag_name: str, amount: int = 27) -> list[InstagramMedia]:
        self.ensure_login()
        return self.normalize(self.client.hashtag_medias_reels_v1(hashtag_name.lstrip("#"), amount=amount), "private_api_hashtag_reels")

    def explore_reels(self, amount: int = 20) -> list[InstagramMedia]:
        self.ensure_login()
        return self.normalize(self.client.explore_reels(amount=amount), "private_api_explore_reels")

    def profile_posts(self, username: str, amount: int = 12) -> list[InstagramMedia]:
        self.ensure_login()
        user_id = self.client.user_id_from_username(username.lstrip("@"))
        self.pause()
        return self.normalize(self.client.user_medias(user_id, amount=amount), "private_api_profile_posts")

    def profile_reels(self, username: str, amount: int = 12) -> list[InstagramMedia]:
        self.ensure_login()
        user_id = self.client.user_id_from_username(username.lstrip("@"))
        self.pause()
        return self.normalize(self.client.user_clips(user_id, amount=amount), "private_api_profile_reels")

    def post(self, shortcode: str) -> list[InstagramMedia]:
        self.ensure_login()
        media_pk = self.client.media_pk_from_code(shortcode)
        return self.normalize([self.client.media_info(media_pk)], "private_api_post")

    def search_hashtags(self, query: str) -> list[dict]:
        self.ensure_login()
        return [{"name": read_attribute(hashtag, "name"), "media_count": read_attribute(hashtag, "media_count")} for hashtag in self.client.search_hashtags(query)]

    def explore(self) -> list[InstagramMedia]:
        from .parsers import find_media_nodes
        self.ensure_login()
        return find_media_nodes(self.client.explore_page(), "private_api_explore")
