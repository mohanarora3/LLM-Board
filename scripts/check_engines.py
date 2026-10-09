"""Check that your SerpApi key works with every engine Panchayat uses.

    python scripts/check_engines.py "is coffee good for you"

Costs about 6 SerpApi searches (results are not cached).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import load_settings  # noqa: E402
from backend.normalize import answer_from_blocks  # noqa: E402
from backend.panches import build_web_answer  # noqa: E402
from backend.serpapi_client import SerpApiClient, SerpApiError  # noqa: E402


def main() -> int:
    settings = load_settings()
    if not settings.serpapi_key:
        print("SERPAPI_API_KEY is not set. Copy .env.example to .env and add your key.")
        return 1
    query = " ".join(sys.argv[1:]) or "is coffee good for you"
    client = SerpApiClient(settings.serpapi_key, cache=None, timeout=settings.request_timeout)
    lang, country = settings.default_lang, settings.country
    print(f'Query: "{query}"  (hl={lang}, gl={country})\n')

    checks = [
        ("google", {"engine": "google", "q": query, "hl": lang, "gl": country}),
        ("google_ai_mode", {"engine": "google_ai_mode", "q": query, "hl": lang, "gl": country}),
        ("bing_copilot", {"engine": "bing_copilot", "q": query}),
        ("brave_ai_mode", {"engine": "brave_ai_mode", "q": query, "country": country, "language": lang}),
        ("google_forums", {"engine": "google_forums", "q": query, "hl": lang, "gl": country}),
    ]
    ok = True
    google_data = None
    for name, params in checks:
        started = time.perf_counter()
        try:
            data = client.search(params, use_cache=False).data
        except SerpApiError as exc:
            ok = ok and exc.empty
            print(f"  {'–' if exc.empty else '✗'} {name:<20} {exc}")
            continue
        ms = int((time.perf_counter() - started) * 1000)
        if name == "google":
            google_data = data
            text, refs = build_web_answer(data, None)
            overview = data.get("ai_overview") or {}
            extra = "AI Overview: " + ("inline" if overview.get("text_blocks") else "page_token" if overview.get("page_token") else "none")
        elif name == "google_forums":
            text, refs, extra = "", data.get("organic_results") or [], ""
        else:
            text, refs = answer_from_blocks(data)
            extra = ""
        print(f"  ✓ {name:<20} {ms:>6} ms  {len(text):>5} chars  {len(refs):>2} refs  {extra}")

    token = ((google_data or {}).get("ai_overview") or {}).get("page_token")
    if token:
        try:
            data = client.search({"engine": "google_ai_overview", "page_token": token}, use_cache=False).data
            text, refs = answer_from_blocks(data.get("ai_overview") or data)
            print(f"  ✓ {'google_ai_overview':<20} {'':>6}     {len(text):>5} chars  {len(refs):>2} refs")
        except SerpApiError as exc:
            print(f"  ✗ google_ai_overview   {exc}")

    print(f"\nSearches used: {client.searches_made}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
