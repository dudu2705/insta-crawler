import random
import time
from dataclasses import dataclass

from curl_cffi import requests

INSTAGRAM_BASE_URL = "https://www.instagram.com"
BROWSER_IMPERSONATION = "chrome"
NAVIGATION_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


class InstagramBlockedError(RuntimeError):
    pass


class LoginWallError(RuntimeError):
    pass


@dataclass
class FetchResult:
    url: str
    status_code: int
    text: str
    final_url: str

    def json(self):
        import json
        return json.loads(self.text)


class InstagramHttpClient:
    def __init__(self, proxy_url: str | None = None, minimum_delay_seconds: float = 2.0, maximum_delay_seconds: float = 5.0, request_timeout_seconds: int = 30):
        proxy_settings = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        self.session = requests.Session(impersonate=BROWSER_IMPERSONATION, proxies=proxy_settings)
        self.minimum_delay_seconds = minimum_delay_seconds
        self.maximum_delay_seconds = maximum_delay_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.last_request_time = 0.0

    def wait_politely(self) -> None:
        elapsed_seconds = time.time() - self.last_request_time
        target_delay_seconds = random.uniform(self.minimum_delay_seconds, self.maximum_delay_seconds)
        if elapsed_seconds < target_delay_seconds:
            time.sleep(target_delay_seconds - elapsed_seconds)
        self.last_request_time = time.time()

    def build_result(self, response) -> FetchResult:
        result = FetchResult(url=str(response.request.url) if response.request else "", status_code=response.status_code, text=response.text, final_url=str(response.url))
        if response.status_code == 429:
            raise InstagramBlockedError("HTTP 429 rate limited: slow down or switch to a residential proxy")
        if "/accounts/login" in result.final_url:
            raise LoginWallError(f"Redirected to login wall: {result.final_url}")
        return result

    def get_page(self, url: str) -> FetchResult:
        self.wait_politely()
        response = self.session.get(url, headers=NAVIGATION_HEADERS, timeout=self.request_timeout_seconds, allow_redirects=True)
        return self.build_result(response)

    def get_static_asset(self, url: str) -> FetchResult:
        asset_headers = {
            "Accept": "*/*",
            "Referer": f"{INSTAGRAM_BASE_URL}/",
            "Origin": INSTAGRAM_BASE_URL,
            "Sec-Fetch-Dest": "script",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        }
        response = self.session.get(url, headers=asset_headers, timeout=self.request_timeout_seconds)
        return FetchResult(url=url, status_code=response.status_code, text=response.text, final_url=str(response.url))

    def api_headers(self, app_id: str, referer_url: str, csrf_token: str | None = None, lsd_token: str | None = None) -> dict[str, str]:
        header_values = {
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "X-IG-App-ID": app_id,
            "X-Requested-With": "XMLHttpRequest",
            "X-ASBD-ID": "129477",
            "Origin": INSTAGRAM_BASE_URL,
            "Referer": referer_url,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        cookie_csrf_token = csrf_token or self.session.cookies.get("csrftoken")
        if cookie_csrf_token:
            header_values["X-CSRFToken"] = cookie_csrf_token
        if lsd_token:
            header_values["X-FB-LSD"] = lsd_token
        return header_values

    def get_api(self, url: str, header_values: dict[str, str]) -> FetchResult:
        self.wait_politely()
        response = self.session.get(url, headers=header_values, timeout=self.request_timeout_seconds)
        return self.build_result(response)

    def post_graphql(self, form_values: dict[str, str], header_values: dict[str, str]) -> FetchResult:
        self.wait_politely()
        graphql_headers = {**header_values, "Content-Type": "application/x-www-form-urlencoded"}
        if "fb_api_req_friendly_name" in form_values:
            graphql_headers["X-FB-Friendly-Name"] = form_values["fb_api_req_friendly_name"]
        response = self.session.post(f"{INSTAGRAM_BASE_URL}/graphql/query", data=form_values, headers=graphql_headers, timeout=self.request_timeout_seconds)
        return self.build_result(response)
