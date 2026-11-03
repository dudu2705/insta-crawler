from .apify import ApifyProvider
from .base import CAPABILITIES, CapabilityNotSupportedError, InstagramDataProvider, ProviderRequestError, ProviderResult
from .hikerapi import HikerApiProvider
from .socialcrawl import SocialCrawlProvider

PROVIDER_CLASSES: dict[str, type[InstagramDataProvider]] = {
    "apify": ApifyProvider,
    "socialcrawl": SocialCrawlProvider,
    "hikerapi": HikerApiProvider,
}

__all__ = [
    "CAPABILITIES",
    "PROVIDER_CLASSES",
    "CapabilityNotSupportedError",
    "InstagramDataProvider",
    "ProviderRequestError",
    "ProviderResult",
]
