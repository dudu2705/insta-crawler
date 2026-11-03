import os
import time
from dataclasses import dataclass, field
from typing import Any

from curl_cffi import requests

from ..models import InstagramMedia

CAPABILITIES = ("trending", "hashtag", "hashtag_recent", "search", "profile_posts", "post")


class CapabilityNotSupportedError(RuntimeError):
    pass


class ProviderRequestError(RuntimeError):
    pass


@dataclass
class ProviderResult:
    provider_name: str
    capability: str
    target: str | None
    items: list[InstagramMedia] = field(default_factory=list)
    raw_responses: list[Any] = field(default_factory=list)
    duration_seconds: float = 0.0
    cost_note: str | None = None
    next_cursor: str | None = None

    def to_dict(self, include_raw: bool = False) -> dict[str, Any]:
        result_dict = {
            "provider": self.provider_name,
            "capability": self.capability,
            "target": self.target,
            "item_count": len(self.items),
            "duration_seconds": self.duration_seconds,
            "cost_note": self.cost_note,
            "next_cursor": self.next_cursor,
            "items": [item.to_dict() for item in self.items],
        }
        if include_raw:
            result_dict["raw_responses"] = self.raw_responses
        return result_dict


class InstagramDataProvider:
    provider_name = "base"
    api_key_environment_variable = ""
    supported_capabilities: tuple[str, ...] = ()

    def __init__(self, api_key: str | None = None, request_timeout_seconds: int = 120):
        self.api_key = api_key or os.environ.get(self.api_key_environment_variable)
        if not self.api_key:
            raise RuntimeError(f"{self.provider_name}: set {self.api_key_environment_variable} in .env")
        self.request_timeout_seconds = request_timeout_seconds

    def send_request(self, method: str, url: str, **request_options) -> Any:
        response = requests.request(method, url, timeout=self.request_timeout_seconds, **request_options)
        if response.status_code >= 400:
            raise ProviderRequestError(f"{self.provider_name} HTTP {response.status_code}: {response.text[:300]}")
        return response.json()

    def run(self, capability: str, target: str | None, limit: int) -> ProviderResult:
        if capability not in self.supported_capabilities:
            raise CapabilityNotSupportedError(f"{self.provider_name} has no '{capability}' capability")
        started_at = time.time()
        provider_result: ProviderResult = getattr(self, f"fetch_{capability}")(target, limit)
        provider_result.duration_seconds = round(time.time() - started_at, 2)
        return provider_result
