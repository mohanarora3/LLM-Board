"""HTTP API (Starlette): health, suggestions, and the streaming /api/ask endpoint. Also serves the frontend."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .cache import Cache
from .config import ROOT, Settings, load_settings
from .llm import LLM
from .mock import SAMPLE_QUESTION, MockSearchClient
from .panches import LANGUAGES, PANCHES
from .pipeline import Panchayat
from .serpapi_client import SerpApiClient

log = logging.getLogger("panchayat")

SUGGESTIONS = [
    "Is it safe to give paracetamol and ibuprofen together to a child?",
    "Who is eligible for PM-KISAN and how much do farmers get?",
    "Is the Great Wall of China visible from space?",
    "Can I withdraw my full PF balance before retirement?",
    "क्या खाली पेट चाय पीना सेहत के लिए नुकसानदायक है?",
    "Does drinking water from a copper vessel have proven health benefits?",
]

MAX_QUESTION_CHARS = 500


def build_app(settings: Settings | None = None, panchayat: Panchayat | None = None) -> Starlette:
    settings = settings or load_settings()

    if panchayat is None:
        llm = LLM(settings) if settings.llm_enabled else None
        if settings.mock:
            client = MockSearchClient()
        elif settings.serpapi_key:
            client = SerpApiClient(settings.serpapi_key, Cache(settings.cache_path, settings.cache_ttl_hours), settings.request_timeout)
        else:
            client = None
        panchayat = Panchayat(settings, client, llm) if client else None

    gates: list[asyncio.Semaphore] = []

    def gate() -> asyncio.Semaphore:
        # Created on first use, inside the server's event loop (needed on Python 3.9).
        if not gates:
            gates.append(asyncio.Semaphore(3))  # at most 3 councils at once, to protect SerpApi credits
        return gates[0]

    async def health(request: Request) -> JSONResponse:
        client = panchayat.client if panchayat else None
        cache = getattr(client, "cache", None)
        return JSONResponse({
            "ok": True,
            "serpapi": bool(settings.serpapi_key) or settings.mock,
            "mock": settings.mock,
            "llm": settings.llm_provider if panchayat and panchayat.llm else "none",
            "llm_model": settings.llm_model if panchayat and panchayat.llm else "",
            "panches": [{"id": p, **PANCHES[p].__dict__} for p in settings.enabled_panches],
            "languages": LANGUAGES,
            "searches_this_session": getattr(client, "searches_made", 0),
            "cache_entries": cache.count() if cache else 0,
            "sample_question": SAMPLE_QUESTION if settings.mock else None,
            "debate_rounds": settings.debate_rounds,
        })

    async def suggestions(request: Request) -> JSONResponse:
        return JSONResponse({"suggestions": [SAMPLE_QUESTION] if settings.mock else SUGGESTIONS})

    async def ask(request: Request):
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"error": "Send JSON: {\"question\": \"…\"}"}, status_code=400)
        question = " ".join(str(body.get("question", "")).split())
        if not question:
            return JSONResponse({"error": "Ask a question first."}, status_code=400)
        if len(question) > MAX_QUESTION_CHARS:
            return JSONResponse({"error": f"Keep questions under {MAX_QUESTION_CHARS} characters."}, status_code=400)
        if panchayat is None:
            return JSONResponse({"error": "SERPAPI_API_KEY is missing. Add it to .env and restart the server."}, status_code=503)
        lang = str(body.get("lang") or "auto")
        want_debate = body.get("debate", True) is not False
        raw_history = body.get("history") if isinstance(body.get("history"), list) else []
        history = [
            {"question": str(h.get("question", ""))[:500], "answer": str(h.get("answer", ""))[:1500]}
            for h in raw_history[-4:]
            if isinstance(h, dict)
        ]

        async def events() -> AsyncIterator[str]:
            async with gate():
                try:
                    async for item in panchayat.convene(question, history, lang, debate=want_debate):
                        yield f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
                except Exception as exc:  # report, don't crash the stream
                    log.exception("council failed")
                    payload = json.dumps({"message": f"Something went wrong: {exc.__class__.__name__}"})
                    yield f"event: error\ndata: {payload}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    routes = [
        Route("/api/health", health),
        Route("/api/suggestions", suggestions),
        Route("/api/ask", ask, methods=["POST"]),
        Mount("/", app=StaticFiles(directory=ROOT / "frontend", html=True), name="frontend"),
    ]
    return Starlette(routes=routes)
