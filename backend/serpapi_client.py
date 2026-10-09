"""Thin SerpApi client: caching, error handling and a running count of searches spent."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from .cache import Cache

SERPAPI_URL = "https://serpapi.com/search.json"

# SerpApi reports "no results" as an error string with HTTP 200. These are not
# failures of our system; the panch simply has nothing to say.
EMPTY_HINTS = ("hasn't returned any results", "no results", "returned no results")


class SerpApiError(RuntimeError):
    """Raised when SerpApi returns an error or an unusable response."""

    def __init__(self, message: str, empty: bool = False) -> None:
        super().__init__(message)
        self.empty = empty


@dataclass
class SearchResponse:
    data: dict[str, Any]
    cached: bool


class SearchClient(Protocol):
    def search(self, params: dict[str, Any], use_cache: bool = True) -> SearchResponse: ...


def cache_key(params: dict[str, Any]) -> str:
    clean = {k: v for k, v in sorted(params.items()) if k != "api_key"}
    return "serpapi:" + hashlib.sha256(json.dumps(clean, ensure_ascii=False).encode()).hexdigest()


class SerpApiClient:
    def __init__(self, api_key: str, cache: Cache | None, timeout: int = 60) -> None:
        if not api_key:
            raise ValueError("SERPAPI_API_KEY is not set")
        self.api_key = api_key
        self.cache = cache
        self.timeout = timeout
        self._session = requests.Session()
        self._lock = threading.Lock()
        self.searches_made = 0

    def search(self, params: dict[str, Any], use_cache: bool = True) -> SearchResponse:
        params = {k: v for k, v in params.items() if v not in (None, "")}
        key = cache_key(params)
        if use_cache and self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                return SearchResponse(hit, cached=True)

        try:
            response = self._session.get(
                SERPAPI_URL, params={**params, "api_key": self.api_key}, timeout=self.timeout
            )
        except requests.Timeout as exc:
            raise SerpApiError(f"{params.get('engine')} timed out after {self.timeout}s") from exc
        except requests.RequestException as exc:
            raise SerpApiError(f"Network error calling SerpApi: {exc.__class__.__name__}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise SerpApiError(f"SerpApi returned a non-JSON response (HTTP {response.status_code})") from exc

        error = data.get("error")
        if error:
            empty = any(hint in error.lower() for hint in EMPTY_HINTS)
            raise SerpApiError(error, empty=empty)
        if response.status_code >= 400:
            raise SerpApiError(f"SerpApi HTTP {response.status_code}")

        # Only successful searches count against the SerpApi plan.
        with self._lock:
            self.searches_made += 1

        if self.cache is not None:
            self.cache.set(key, data)
        return SearchResponse(data, cached=False)
