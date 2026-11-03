import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

RELAY_OPERATION_PATTERN = re.compile(
    r"""__d\(\s*["'](\w+?)_instagramRelayOperation["']\s*,\s*\[\]\s*,\s*\(?\s*function\s*\([^)]*\)\s*\{\s*\w+\.exports\s*=\s*["'](\d+)["']"""
)
INLINE_QUERY_PATTERNS = (
    re.compile(r'"queryName"\s*:\s*"(\w+)".{0,800}?"queryID"\s*:\s*"(\d+)"', re.DOTALL),
    re.compile(r'"queryID"\s*:\s*"(\d+)".{0,800}?"queryName"\s*:\s*"(\w+)"', re.DOTALL),
    re.compile(r'"(\w+?)_instagramRelayOperation"\s*:\s*"(\d+)"'),
)
SCRIPT_URL_PATTERNS = (
    re.compile(r'<script[^>]+src="(https://static\.cdninstagram\.com/rsrc\.php/[^"]+)"'),
    re.compile(r'<link[^>]+href="(https://static\.cdninstagram\.com/rsrc\.php/[^"]+\.js[^"]*)"'),
    re.compile(r'"(https:\\/\\/static\.cdninstagram\.com\\/rsrc\.php\\/[^"]+?\.js[^"]*)"'),
    re.compile(r'"(https://static\.cdninstagram\.com/rsrc\.php/[^"]+?\.js[^"]*)"'),
)
LSD_TOKEN_PATTERNS = (
    re.compile(r'"LSD",\[\],\{"token":"([^"]+)"'),
    re.compile(r'"lsd":"([^"]+)"'),
    re.compile(r'name="lsd" value="([^"]+)"'),
)
RELAY_PROVIDER_PATTERN = re.compile(r"__relay_internal__pv__(\w+?)relayprovider")
CSRF_TOKEN_PATTERN = re.compile(r'"csrf_token":"([^"]+)"')
APP_ID_PATTERN = re.compile(r'"APP_ID":"(\d+)"')
DEFAULT_APP_ID = "936619743392459"
CACHE_LIFETIME_SECONDS = 6 * 60 * 60


@dataclass
class WebTokens:
    lsd_token: str | None = None
    csrf_token: str | None = None
    app_id: str = DEFAULT_APP_ID
    doc_ids: dict[str, str] = field(default_factory=dict)
    relay_provider_names: list[str] = field(default_factory=list)
    harvested_at: float = 0.0
    harvest_report: dict = field(default_factory=dict)

    def relay_provider_variables(self) -> dict[str, bool]:
        return {f"__relay_internal__pv__{provider_name}relayprovider": False for provider_name in self.relay_provider_names}

    def is_fresh(self) -> bool:
        return bool(self.doc_ids) and time.time() - self.harvested_at < CACHE_LIFETIME_SECONDS

    def find_doc_id(self, *operation_name_fragments: str) -> tuple[str, str] | None:
        for operation_name_fragment in operation_name_fragments:
            for operation_name, doc_id in self.doc_ids.items():
                if operation_name_fragment.lower() in operation_name.lower():
                    return operation_name, doc_id
        return None


def extract_first_match(patterns: tuple[re.Pattern, ...], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def extract_page_tokens(html_text: str) -> WebTokens:
    app_id_match = APP_ID_PATTERN.search(html_text)
    csrf_match = CSRF_TOKEN_PATTERN.search(html_text)
    return WebTokens(
        lsd_token=extract_first_match(LSD_TOKEN_PATTERNS, html_text),
        csrf_token=csrf_match.group(1) if csrf_match else None,
        app_id=app_id_match.group(1) if app_id_match else DEFAULT_APP_ID,
        doc_ids={**extract_inline_queries(html_text), **extract_relay_operations(html_text)},
    )


def extract_script_urls(html_text: str) -> list[str]:
    collected_urls = []
    for script_url_pattern in SCRIPT_URL_PATTERNS:
        for raw_url in script_url_pattern.findall(html_text):
            collected_urls.append(raw_url.replace("\\/", "/").replace("&amp;", "&").replace("\\u0026", "&"))
    return list(dict.fromkeys(collected_urls))


def extract_relay_operations(javascript_text: str) -> dict[str, str]:
    return {operation_name: doc_id for operation_name, doc_id in RELAY_OPERATION_PATTERN.findall(javascript_text)}


def extract_inline_queries(html_text: str) -> dict[str, str]:
    inline_queries: dict[str, str] = {}
    for query_name, query_id in INLINE_QUERY_PATTERNS[0].findall(html_text):
        inline_queries[query_name] = query_id
    for query_id, query_name in INLINE_QUERY_PATTERNS[1].findall(html_text):
        inline_queries.setdefault(query_name, query_id)
    for query_name, query_id in INLINE_QUERY_PATTERNS[2].findall(html_text):
        inline_queries.setdefault(query_name, query_id)
    return inline_queries


def extract_relay_provider_names(javascript_text: str) -> list[str]:
    return sorted(set(RELAY_PROVIDER_PATTERN.findall(javascript_text)))


def load_cached_tokens(cache_path: Path) -> WebTokens | None:
    if not cache_path.exists():
        return None
    try:
        cached_data = json.loads(cache_path.read_text())
        return WebTokens(**cached_data)
    except (json.JSONDecodeError, TypeError):
        return None


def save_cached_tokens(cache_path: Path, web_tokens: WebTokens) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(web_tokens.__dict__, indent=2))
