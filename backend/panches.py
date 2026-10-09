"""The five panches: each one is a SerpApi engine (or group of engines) that answers the question.

| Panch              | SerpApi engines                         |
|--------------------|-----------------------------------------|
| Google AI Overview | google  →  google_ai_overview (token)   |
| Google AI Mode     | google_ai_mode                          |
| Bing Copilot       | bing_copilot                            |
| Brave AI           | brave_ai_mode                           |
| Open web           | google (organic, answer box, PAA) + google_forums |

Every fetcher is a plain synchronous function so it can run in a worker thread.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .normalize import answer_from_blocks, domain_of
from .serpapi_client import SearchClient, SearchResponse, SerpApiError

LANGUAGES = {
    "en": "English",
    "hi": "Hindi",
    "bn": "Bengali",
    "ta": "Tamil",
    "te": "Telugu",
    "mr": "Marathi",
    "kn": "Kannada",
    "ml": "Malayalam",
    "gu": "Gujarati",
    "pa": "Punjabi",
}


@dataclass(frozen=True)
class PanchInfo:
    id: str
    name: str
    short: str
    initials: str
    engines: str


PANCHES: dict[str, PanchInfo] = {
    "ai_overview": PanchInfo("ai_overview", "Google AI Overview", "AI Overview", "AO", "google + google_ai_overview"),
    "ai_mode": PanchInfo("ai_mode", "Google AI Mode", "AI Mode", "AM", "google_ai_mode"),
    "copilot": PanchInfo("copilot", "Bing Copilot", "Copilot", "BC", "bing_copilot"),
    "brave": PanchInfo("brave", "Brave AI", "Brave AI", "BR", "brave_ai_mode"),
    "web": PanchInfo("web", "Open web", "Open web", "WEB", "google + google_forums"),
}


@dataclass(frozen=True)
class Locale:
    lang: str = "en"
    country: str = "in"


@dataclass
class PanchResult:
    id: str
    status: str = "answered"  # answered | abstained | error
    answer: str = ""
    references: list[dict[str, Any]] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    note: str = ""
    latency_ms: int = 0
    cached: bool = True
    searches: int = 0  # live (billed) SerpApi searches made for this panch

    def to_dict(self) -> dict[str, Any]:
        info = PANCHES[self.id]
        return {**asdict(self), "name": info.name, "short": info.short, "initials": info.initials, "engines": info.engines}


def abstain(panch_id: str, note: str) -> PanchResult:
    return PanchResult(id=panch_id, status="abstained", note=note)


def google_params(query: str, locale: Locale) -> dict[str, Any]:
    return {"engine": "google", "q": query, "hl": locale.lang, "gl": locale.country}


def _related_from(data: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("related_questions", "follow_up_questions", "related_searches"):
        for item in data.get(key) or []:
            text = item.get("question") or item.get("query") if isinstance(item, dict) else item
            if isinstance(text, str) and text.strip() and text.strip() not in out:
                out.append(text.strip())
    return out[:6]


def _finish(panch_id: str, answer: str, refs: list[dict[str, Any]], related: list[str], responses: list[SearchResponse]) -> PanchResult:
    if not answer.strip():
        result = abstain(panch_id, "The engine returned no answer for this question.")
    else:
        result = PanchResult(id=panch_id, answer=answer, references=refs, related=related)
    result.cached = all(r.cached for r in responses)
    result.searches = sum(0 if r.cached else 1 for r in responses)
    return result


# --- Google AI Overview ------------------------------------------------------

def fetch_ai_overview(client: SearchClient, google: SearchResponse, query: str, locale: Locale) -> PanchResult:
    responses = [google]
    overview = google.data.get("ai_overview")
    if not overview:
        result = abstain("ai_overview", "Google showed no AI Overview for this search.")
        result.cached = google.cached
        return result
    if overview.get("error"):
        return abstain("ai_overview", str(overview["error"]))

    if not overview.get("text_blocks") and overview.get("page_token"):
        # The AI Overview loads separately. Its page_token lives about a minute, so if the
        # Google result came from our cache the token may be stale: refresh once and retry.
        try:
            follow = client.search({"engine": "google_ai_overview", "page_token": overview["page_token"]})
        except SerpApiError:
            if not google.cached:
                raise
            fresh = client.search(google_params(query, locale), use_cache=False)
            responses.append(fresh)
            token = (fresh.data.get("ai_overview") or {}).get("page_token")
            if not token:
                return _finish("ai_overview", *answer_from_blocks(fresh.data.get("ai_overview") or {}), [], responses)
            follow = client.search({"engine": "google_ai_overview", "page_token": token})
        responses.append(follow)
        overview = follow.data.get("ai_overview") or follow.data

    answer, refs = answer_from_blocks(overview)
    return _finish("ai_overview", answer, refs, [], responses)


# --- Google AI Mode ----------------------------------------------------------

def fetch_ai_mode(client: SearchClient, query: str, locale: Locale) -> PanchResult:
    resp = client.search({"engine": "google_ai_mode", "q": query, "hl": locale.lang, "gl": locale.country})
    answer, refs = answer_from_blocks(resp.data)
    return _finish("ai_mode", answer, refs, _related_from(resp.data), [resp])


# --- Bing Copilot ------------------------------------------------------------

def fetch_copilot(client: SearchClient, query: str, locale: Locale) -> PanchResult:
    resp = client.search({"engine": "bing_copilot", "q": query})
    answer, refs = answer_from_blocks(resp.data)
    return _finish("copilot", answer, refs, _related_from(resp.data), [resp])


# --- Brave AI Mode -----------------------------------------------------------

def fetch_brave(client: SearchClient, query: str, locale: Locale) -> PanchResult:
    params = {"engine": "brave_ai_mode", "q": query, "country": locale.country, "language": locale.lang}
    try:
        resp = client.search(params)
    except SerpApiError as exc:
        if exc.empty or locale.lang == "en":
            raise
        # Brave may not support every Indian language code; ask again without it.
        params.pop("language")
        resp = client.search(params)
    answer, refs = answer_from_blocks(resp.data)
    return _finish("brave", answer, refs, _related_from(resp.data), [resp])


# --- Open web (Google results + forums) --------------------------------------

def _clip(text: str, limit: int = 320) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def build_web_answer(google: dict[str, Any], forums: dict[str, Any] | None) -> tuple[str, list[dict[str, Any]]]:
    refs: list[dict[str, Any]] = []
    by_link: dict[str, int] = {}

    def ref(link: str, title: str, snippet: str, source: str = "") -> int:
        if link in by_link:
            return by_link[link]
        refs.append({"title": title or domain_of(link), "link": link, "snippet": _clip(snippet, 240),
                     "source": source, "domain": domain_of(link)})
        by_link[link] = len(refs)
        return by_link[link]

    sections: list[str] = []

    box = google.get("answer_box") or {}
    box_text = box.get("answer") or box.get("snippet") or box.get("result")
    if isinstance(box_text, str) and box.get("link"):
        n = ref(box["link"], box.get("title", ""), box_text)
        sections.append(f"**Featured answer**: {_clip(box_text)} [{n}]")

    kg = google.get("knowledge_graph") or {}
    kg_link = (kg.get("source") or {}).get("link") if isinstance(kg.get("source"), dict) else None
    if kg.get("description") and kg_link:
        n = ref(kg_link, kg.get("title", ""), kg["description"])
        sections.append(f"**Knowledge panel**: {_clip(kg['description'])} [{n}]")

    lines = []
    for item in (google.get("organic_results") or [])[:6]:
        link, snippet = item.get("link"), item.get("snippet")
        if link and snippet:
            n = ref(link, item.get("title", ""), snippet, item.get("source", ""))
            lines.append(f"- {_clip(snippet)} [{n}]")
    if lines:
        sections.append("**What the top results say**\n" + "\n".join(lines))

    lines = []
    for item in (google.get("related_questions") or [])[:3]:
        link, snippet, question = item.get("link"), item.get("snippet"), item.get("question")
        if link and snippet and question:
            n = ref(link, item.get("title", ""), snippet)
            lines.append(f"- **{question}** {_clip(snippet, 260)} [{n}]")
    if lines:
        sections.append("**People also ask**\n" + "\n".join(lines))

    lines = []
    for item in ((forums or {}).get("organic_results") or [])[:4]:
        link = item.get("link")
        answers = item.get("answers") or []
        top = next((a for a in answers if isinstance(a, dict) and a.get("top_answer")), answers[0] if answers else None)
        text = (top or {}).get("answer") if isinstance(top, dict) else None
        text = text or item.get("snippet")
        if link and text:
            n = ref(link, item.get("title", ""), text, item.get("source", ""))
            lines.append(f"- {_clip(text, 260)} [{n}]")
    if lines:
        sections.append("**What people say in forums**\n" + "\n".join(lines))

    return "\n\n".join(sections), refs


def fetch_web(google: SearchResponse, forums: SearchResponse | None) -> PanchResult:
    responses = [google] + ([forums] if forums else [])
    answer, refs = build_web_answer(google.data, forums.data if forums else None)
    return _finish("web", answer, refs, _related_from(google.data), responses)


def fetch_forums(client: SearchClient, query: str, locale: Locale) -> SearchResponse | None:
    try:
        return client.search({"engine": "google_forums", "q": query, "hl": locale.lang, "gl": locale.country})
    except SerpApiError:
        return None  # forums are a bonus for the open-web panch, never a blocker
