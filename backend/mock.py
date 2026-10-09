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

# Sample debate replies, keyed by (engine, round). Round 1 shows real disagreement
# (Brave AI's opening answer claimed the wall is visible from orbit); round 2 converges.
DEBATE_REPLIES = {
    ("google_ai_mode", 1): "Mostly accurate, but it needs one correction: the wall is not visible from the Moon, and from the space station it is only visible in photos taken with zoom lenses, not to the naked eye.",
    ("bing_copilot", 1): "That is not accurate. The Great Wall cannot be seen with the naked eye from low Earth orbit under normal conditions; it is only a few metres wide and blends into the land. The 'visible from space' idea is a popular myth.",
    ("brave_ai_mode", 1): "Yes, that is correct. The wall is not visible from the Moon, and astronauts on the space station say it is very hard to pick out without a camera.",
    ("google_ai_mode", 2): "Yes, I agree. The Great Wall is not visible from the Moon and is extremely hard to see from orbit with the naked eye; cameras with long lenses can capture it.",
    ("bing_copilot", 2): "Yes, that is right. It is not visible from the Moon, and from low orbit it is practically invisible to the naked eye, though zoom photos have captured parts of it.",
    ("brave_ai_mode", 2): "Yes, I agree with that correction. Earlier claims that it is easy to see from orbit are a myth: it is not visible from the Moon and is very hard to see from orbit without a camera.",
}


class MockSearchClient:
    def __init__(self, speed: float = 1.0) -> None:
        self.speed = speed
        self._lock = threading.Lock()
        self.searches_made = 0

    def search(self, params: dict[str, Any], use_cache: bool = True) -> SearchResponse:
        engine = params.get("engine", "google")
        query = str(params.get("q", ""))
        if "Another AI search engine" in query:
            return self._debate_reply(engine, query)
        path = FIXTURES / f"{engine}.json"
        if not path.exists():
            raise SerpApiError(f"No sample data for engine {engine}", empty=True)
        time.sleep(DELAYS.get(engine, 1.0) * self.speed)
        with self._lock:
            self.searches_made += 1
        return SearchResponse(json.loads(path.read_text(encoding="utf-8")), cached=False)

    def _debate_reply(self, engine: str, query: str) -> SearchResponse:
        round_no = 2 if "now says" in query else 1
        text = DEBATE_REPLIES.get((engine, round_no))
        if not text:
            raise SerpApiError(f"No sample debate reply for {engine}", empty=True)
        base = json.loads((FIXTURES / f"{engine}.json").read_text(encoding="utf-8"))
        time.sleep(DELAYS.get(engine, 1.0) * self.speed * 0.6)
        with self._lock:
            self.searches_made += 1
        data = {
            "search_metadata": {"status": "Success"},
            "text_blocks": [{"type": "paragraph", "snippet": text, "reference_indexes": [0, 1]}],
            "references": base.get("references", [])[:3],
        }
        return SearchResponse(data, cached=False)
