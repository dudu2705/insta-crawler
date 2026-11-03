from typing import Any

from ..parsers import find_media_nodes
from .base import InstagramDataProvider, ProviderResult

HIKERAPI_BASE_URL = "https://api.hikerapi.com"


def split_chunk_response(chunk_response: Any) -> tuple[list[Any], str | None]:
    if isinstance(chunk_response, list) and len(chunk_response) == 2 and isinstance(chunk_response[0], list):
        next_cursor = chunk_response[1] if isinstance(chunk_response[1], str) else None
        return chunk_response[0], next_cursor
    if isinstance(chunk_response, list):
        return chunk_response, None
    if isinstance(chunk_response, dict):
        for list_key in ("items", "medias", "response"):
            if isinstance(chunk_response.get(list_key), list):
                return chunk_response[list_key], chunk_response.get("next_page_id") or chunk_response.get("end_cursor")
        return [chunk_response], None
    return [], None


class HikerApiProvider(InstagramDataProvider):
    provider_name = "hikerapi"
    api_key_environment_variable = "HIKERAPI_KEY"
    supported_capabilities = ("hashtag", "search", "profile_posts", "post")

    def get(self, endpoint_path: str, query_parameters: dict[str, Any]) -> Any:
        return self.send_request("GET", f"{HIKERAPI_BASE_URL}{endpoint_path}", params=query_parameters, headers={"x-access-key": self.api_key, "accept": "application/json"})

    def collect_chunks(self, endpoint_path: str, query_parameters: dict[str, Any], cursor_parameter: str, capability: str, target: str, limit: int) -> ProviderResult:
        provider_result = ProviderResult(provider_name=self.provider_name, capability=capability, target=target)
        current_parameters = dict(query_parameters)
        request_count = 0
        while len(provider_result.items) < limit and request_count < 10:
            chunk_response = self.get(endpoint_path, current_parameters)
            request_count += 1
            provider_result.raw_responses.append(chunk_response)
            media_rows, next_cursor = split_chunk_response(chunk_response)
            for media_row in media_rows:
                provider_result.items.extend(find_media_nodes(media_row, f"provider_hikerapi_{capability}"))
            provider_result.next_cursor = next_cursor
            if not next_cursor or not media_rows:
                break
            current_parameters[cursor_parameter] = next_cursor
        provider_result.items = provider_result.items[:limit]
        provider_result.cost_note = f"{request_count} requests (~$1 per 1k, 4xx also billed)"
        return provider_result

    def fetch_hashtag(self, hashtag_name: str, limit: int) -> ProviderResult:
        return self.collect_chunks("/v1/hashtag/medias/top/chunk", {"name": hashtag_name.lstrip("#")}, "max_id", "hashtag", hashtag_name, limit)

    def fetch_search(self, query: str, limit: int) -> ProviderResult:
        hashtag_matches = self.get("/v1/search/hashtags", {"query": query})
        hashtag_rows = hashtag_matches if isinstance(hashtag_matches, list) else hashtag_matches.get("results", [])
        if not hashtag_rows:
            return ProviderResult(provider_name=self.provider_name, capability="search", target=query, raw_responses=[hashtag_matches], cost_note="1 request")
        best_hashtag_name = hashtag_rows[0].get("name")
        provider_result = self.fetch_hashtag(best_hashtag_name, limit)
        provider_result.capability = "search"
        provider_result.target = f"{query} -> #{best_hashtag_name}"
        provider_result.raw_responses.insert(0, hashtag_matches)
        return provider_result

    def fetch_profile_posts(self, username: str, limit: int) -> ProviderResult:
        user_payload = self.get("/v1/user/by/username", {"username": username.lstrip("@")})
        user_id = user_payload.get("pk") or user_payload.get("id")
        provider_result = self.collect_chunks("/v1/user/clips/chunk", {"user_id": user_id}, "end_cursor", "profile_posts", username, limit)
        provider_result.raw_responses.insert(0, user_payload)
        return provider_result

    def fetch_post(self, shortcode: str, limit: int) -> ProviderResult:
        media_payload = self.get("/v1/media/by/code", {"code": shortcode})
        return ProviderResult(
            provider_name=self.provider_name,
            capability="post",
            target=shortcode,
            items=find_media_nodes(media_payload, "provider_hikerapi_post"),
            raw_responses=[media_payload],
            cost_note="1 request",
        )
