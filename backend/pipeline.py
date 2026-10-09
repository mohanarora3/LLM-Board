"""The panchayat session: convene the panches, record claims, score agreement, stream the verdict.

`convene()` is an async generator of events that the API forwards to the browser
as Server-Sent Events:

  start → query → panch (×5, as each arrives) → sources → media → council
        → debate_start → debate_round / debate_turn (…) → debate_end
        → token (…) → answer → related → done
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any, AsyncIterator, Callable, Iterator

from . import council as council_mod
from . import debate as debate_mod
from . import prompts
from .config import Settings
from .llm import LLM, LLMError
from .panches import (
    LANGUAGES,
    PANCHES,
    Locale,
    PanchResult,
    fetch_ai_mode,
    fetch_ai_overview,
    fetch_brave,
    fetch_copilot,
    fetch_forums,
    fetch_web,
    google_params,
)
from .serpapi_client import SearchClient, SerpApiError

SCRIPTS = [
    ("hi", "ऀ", "ॿ"),
    ("bn", "ঀ", "৿"),
    ("pa", "਀", "੿"),
    ("gu", "઀", "૿"),
    ("ta", "஀", "௿"),
    ("te", "ఀ", "౿"),
    ("kn", "ಀ", "೿"),
    ("ml", "ഀ", "ൿ"),
]


def detect_language(text: str) -> str:
    counts = {code: sum(lo <= ch <= hi for ch in text) for code, lo, hi in SCRIPTS}
    best = max(counts, key=counts.get)
    return best if counts[best] >= 2 else "en"


def _event(kind: str, **data: Any) -> dict[str, Any]:
    return {"event": kind, "data": data}


async def _stream_in_thread(make_iter: Callable[[], Iterator[str]]) -> AsyncIterator[str]:
    """Run a blocking generator in a worker thread and yield its items asynchronously."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    done = object()

    def worker() -> None:
        try:
            for item in make_iter():
                loop.call_soon_threadsafe(queue.put_nowait, item)
        except Exception as exc:  # surfaced to the caller below
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, done)

    task = loop.run_in_executor(None, worker)
    while True:
        item = await queue.get()
        if item is done:
            break
        if isinstance(item, Exception):
            await task
            raise item
        yield item
    await task


async def _chunked(text: str) -> AsyncIterator[str]:
    """Stream a precomputed answer word by word so the UI behaves the same with or without an LLM."""
    for piece in re.findall(r"\S+\s*|\s+", text):
        yield piece
        await asyncio.sleep(0.012)


class Panchayat:
    def __init__(self, settings: Settings, client: SearchClient, llm: LLM | None) -> None:
        self.settings = settings
        self.client = client
        self.llm = llm

    # ------------------------------------------------------------------ helpers

    def _prepare_query(self, question: str, history: list[dict[str, str]], lang: str, asked_lang: str) -> str:
        needs_translation = lang != asked_lang
        if not self.llm or (not history and not needs_translation):
            return question
        convo = "\n".join(f"User: {h.get('question', '')}\nAnswer: {h.get('answer', '')[:600]}" for h in history[-3:])
        user = (f"Conversation so far:\n{convo}\n\n" if convo else "") + f"Latest message: {question}"
        try:
            query = self.llm.complete_text(prompts.QUERY_SYSTEM.format(language=LANGUAGES.get(lang, "English")), user, 200)
        except LLMError:
            return question
        query = query.strip().strip('"').splitlines()[0].strip() if query.strip() else ""
        return query[:300] or question

    async def _run_panch(self, panch_id: str, call: Callable[[], PanchResult]) -> PanchResult:
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(call)
        except SerpApiError as exc:
            result = PanchResult(id=panch_id, status="abstained" if exc.empty else "error", note=str(exc)[:240])
        except Exception as exc:  # one broken panch must never sink the council
            result = PanchResult(id=panch_id, status="error", note=f"{exc.__class__.__name__}: {exc}"[:240])
        result.latency_ms = int((time.perf_counter() - started) * 1000)
        return result

    def _panch_tasks(self, query: str, locale: Locale) -> list[asyncio.Task]:
        enabled = self.settings.enabled_panches
        client = self.client
        tasks: list[asyncio.Task] = []
        google_task = None
        if "ai_overview" in enabled or "web" in enabled:
            google_task = asyncio.ensure_future(asyncio.to_thread(client.search, google_params(query, locale)))

        async def with_google(panch_id: str, build: Callable[[Any], PanchResult]) -> PanchResult:
            started = time.perf_counter()
            try:
                google = await google_task
            except SerpApiError as exc:
                result = PanchResult(id=panch_id, status="abstained" if exc.empty else "error", note=str(exc)[:240])
                result.latency_ms = int((time.perf_counter() - started) * 1000)
                return result
            result = await self._run_panch(panch_id, lambda: build(google))
            result.latency_ms = int((time.perf_counter() - started) * 1000)
            return result

        if "ai_overview" in enabled:
            tasks.append(asyncio.ensure_future(with_google("ai_overview", lambda g: fetch_ai_overview(client, g, query, locale))))
        if "ai_mode" in enabled:
            tasks.append(asyncio.ensure_future(self._run_panch("ai_mode", lambda: fetch_ai_mode(client, query, locale))))
        if "copilot" in enabled:
            tasks.append(asyncio.ensure_future(self._run_panch("copilot", lambda: fetch_copilot(client, query, locale))))
        if "brave" in enabled:
            tasks.append(asyncio.ensure_future(self._run_panch("brave", lambda: fetch_brave(client, query, locale))))
        if "web" in enabled:
            forums_task = asyncio.ensure_future(asyncio.to_thread(fetch_forums, client, query, locale))

            async def web() -> PanchResult:
                forums = await forums_task
                return await with_google("web", lambda g: fetch_web(g, forums))

            tasks.append(asyncio.ensure_future(web()))
        return tasks

    # ------------------------------------------------------------------ session

    async def convene(self, question: str, history: list[dict[str, str]] | None = None, lang: str = "auto",
                      debate: bool = True) -> AsyncIterator[dict[str, Any]]:
        started = time.perf_counter()
        history = history or []
        asked_lang = detect_language(question)
        lang = asked_lang if lang in ("", "auto") or lang not in LANGUAGES else lang
        language = LANGUAGES[lang]
        searches_before = getattr(self.client, "searches_made", 0)

        yield _event(
            "start",
            question=question,
            lang=lang,
            language=language,
            panches=[{"id": p, "name": PANCHES[p].name, "short": PANCHES[p].short, "initials": PANCHES[p].initials,
                      "engines": PANCHES[p].engines} for p in self.settings.enabled_panches],
            llm=self.settings.llm_provider if self.llm else "none",
            mock=self.settings.mock,
            debate=bool(debate and self.settings.debate_rounds),
        )

        query = await asyncio.to_thread(self._prepare_query, question, history, lang, asked_lang)
        yield _event("query", query=query, rewritten=query != question)

        locale = Locale(lang=lang, country=self.settings.country)
        results: list[PanchResult] = []
        for finished in asyncio.as_completed(self._panch_tasks(query, locale)):
            result = await finished
            results.append(result)
            yield _event("panch", panch=result.to_dict())

        order = list(self.settings.enabled_panches)
        results.sort(key=lambda r: order.index(r.id))
        answering = [r for r in results if r.status == "answered"]

        book = council_mod.build_sources(results)
        yield _event("sources", sources=book.sources)
        images: list[dict[str, Any]] = []
        for r in results:
            for img in r.images:
                if img["thumbnail"] not in {i["thumbnail"] for i in images}:
                    images.append(img)
        yield _event("media", images=images[:12])

        if not answering:
            message = "None of the panches could answer this question. Try rephrasing it, or check the SerpApi key and credits."
            yield _event("council", council=council_mod.score_council([], results, book), method="none", note=message)
            yield _event("answer", markdown=message)
            yield _event("done", **self._stats(started, searches_before, results))
            return

        method, note = "heuristic", ""
        claims: list[dict[str, Any]] = []
        if self.llm:
            yield _event("step", text="Recording each panch's claims")
            try:
                claims = await asyncio.to_thread(council_mod.extract_claims_llm, self.llm, query, answering, language)
                method = "llm"
            except (LLMError, Exception) as exc:  # fall back rather than fail the whole answer
                note = f"Claim analysis fell back to sentence matching ({exc.__class__.__name__})."
        if not claims:
            claims = council_mod.extract_claims_heuristic(answering)
            if not self.llm:
                note = "No LLM key set: agreement is measured by matching sentences, and contradictions aren't detected."

        council = council_mod.score_council(claims, results, book)
        yield _event("council", council=council, method=method, note=note)

        debate_result: debate_mod.DebateResult | None = None
        if debate and self.settings.debate_rounds:
            debaters = debate_mod.pick_debaters(answering, self.settings.enabled_panches)
            if len(debaters) >= 2:
                yield _event("step", text="The panches are debating")
                box: list[debate_mod.DebateResult] = []
                session = debate_mod.Debate(self.client, self.llm, self.settings.debate_rounds, language, locale)
                async for item in session.run(query, debaters, box):
                    yield item
                debate_result = box[0] if box else None
            else:
                yield _event("debate_skip", reason="Fewer than two AI engines answered, so there was no one to debate.")

        yield _event("step", text="Writing the verdict")
        answer = ""
        if self.llm and method == "llm":
            system = prompts.VERDICT_SYSTEM.format(language=language)
            user = prompts.verdict_user(question, f"{council['headline']} — {council['ruling']} ({council['consensus']}% of key claims agreed)",
                                        council_mod.ledger_text(council), council_mod.sources_text(book),
                                        debate_result.transcript() if debate_result else "")
            try:
                async for piece in _stream_in_thread(lambda: self.llm.stream(system, user)):
                    answer += piece
                    yield _event("token", text=piece)
            except Exception:
                if answer:
                    answer += "\n\n_(The verdict was cut short by a model error.)_"
                    yield _event("token", text="\n\n_(The verdict was cut short by a model error.)_")
        if not answer.strip():
            answer = council_mod.heuristic_verdict(council)
            if debate_result:
                extra = debate_mod.debate_markdown(debate_result)
                if extra:
                    first, _, rest = answer.partition("\n\n")
                    answer = f"{first}\n\n{extra}" + (f"\n\n{rest}" if rest else "")
            async for piece in _chunked(answer):
                yield _event("token", text=piece)
        yield _event("answer", markdown=answer)

        related: list[str] = []
        for r in results:
            for q in r.related:
                if q.lower() not in {x.lower() for x in related} and q.lower() != question.lower():
                    related.append(q)
        yield _event("related", questions=related[:5])
        yield _event("done", **self._stats(started, searches_before, results))

    def _stats(self, started: float, searches_before: int, results: list[PanchResult]) -> dict[str, Any]:
        return {
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "searches": getattr(self.client, "searches_made", 0) - searches_before,
            "cached_panches": sum(1 for r in results if r.cached and r.status == "answered"),
        }
