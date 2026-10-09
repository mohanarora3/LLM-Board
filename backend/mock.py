"""Offline sample mode (PANCHAYAT_MOCK=1): replays bundled sample responses shaped exactly
like SerpApi's JSON, with realistic delays. For UI work and tests without spending credits.
The interface shows a clear "sample data" banner whenever this mode is on."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .serpapi_client import SearchResponse, SerpApiError

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DELAYS = {"google": 0.7, "google_ai_overview": 0.9, "google_ai_mode": 2.4, "bing_copilot": 3.1, "brave_ai_mode": 1.8, "google_forums": 0.8}

SAMPLE_QUESTION = "Is the Great Wall of China visible from space?"


class MockSearchClient:
    def __init__(self, speed: float = 1.0) -> None:
        self.speed = speed
        self._lock = threading.Lock()
        self.searches_made = 0

    def search(self, params: dict[str, Any], use_cache: bool = True) -> SearchResponse:
        engine = params.get("engine", "google")
        path = FIXTURES / f"{engine}.json"
        if not path.exists():
            raise SerpApiError(f"No sample data for engine {engine}", empty=True)
        time.sleep(DELAYS.get(engine, 1.0) * self.speed)
        with self._lock:
            self.searches_made += 1
        return SearchResponse(json.loads(path.read_text(encoding="utf-8")), cached=False)
