"""Run the full council for your demo questions ahead of time, so the live demo is instant and free.

    python scripts/warm_cache.py                       # the built-in suggestion questions
    python scripts/warm_cache.py "question one" "question two"

Each new question costs about 6 SerpApi searches. Results are cached in data/cache.sqlite3
(CACHE_TTL_HOURS, default 72), and repeat runs of the same question cost nothing.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app import SUGGESTIONS  # noqa: E402
from backend.cache import Cache  # noqa: E402
from backend.config import load_settings  # noqa: E402
from backend.llm import LLM  # noqa: E402
from backend.pipeline import Panchayat  # noqa: E402
from backend.serpapi_client import SerpApiClient  # noqa: E402


async def warm(questions: list[str]) -> None:
    settings = load_settings()
    if not settings.serpapi_key:
        print("SERPAPI_API_KEY is not set.")
        return
    client = SerpApiClient(settings.serpapi_key, Cache(settings.cache_path, settings.cache_ttl_hours), settings.request_timeout)
    council = Panchayat(settings, client, LLM(settings) if settings.llm_enabled else None)
    for question in questions:
        print(f"\n▶ {question}")
        async for event in council.convene(question):
            kind, data = event["event"], event["data"]
            if kind == "panch":
                p = data["panch"]
                print(f"   {p['name']:<20} {p['status']:<10} {'cached' if p['cached'] else ''}")
            elif kind == "council":
                c = data["council"]
                print(f"   → {c['headline']} · {c['ruling']} · {c['consensus']}%  ({data['method']})")
            elif kind == "done":
                print(f"   {data['searches']} searches, {data['elapsed_ms'] / 1000:.1f}s")
    print(f"\nTotal SerpApi searches used: {client.searches_made}")


if __name__ == "__main__":
    asyncio.run(warm(sys.argv[1:] or SUGGESTIONS))
