"""The debate: the AI panches cross-examine each other through SerpApi until they agree.

Every engine only takes a search query, so the "conversation" happens inside the
queries themselves. In each round, every debater is shown what a rival engine just
said and asked whether it is accurate:

  Round 1  Google AI Mode reads Bing Copilot's opening answer, Bing Copilot reads
           Brave AI's, Brave AI reads Google AI Mode's (a ring, so everyone is
           challenged once and challenges once).
  Round 2+ Each debater reads its rival's latest reply ("they now say …").

After each round we record every debater's stance (agrees / partly / disagrees).
When all of them agree, the panchayat has reached consensus and the debate stops
early; otherwise it runs for at most `rounds` rounds. Stances are judged by the
clerk LLM when one is configured, or by transparent phrase rules otherwise.

Cost: one SerpApi search per debater per round (3 engines x 2 rounds = 6 at most),
and cached like every other search.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from . import prompts
from .llm import LLM, LLMError
from .normalize import plain_text
from .panches import PANCHES, Locale, PanchResult, fetch_ai_mode, fetch_brave, fetch_copilot
from .serpapi_client import SearchClient, SerpApiError

# Engines that answer free-form prompts. AI Overview needs a plain Google query and
# the open web is not a model, so they give evidence but don't argue.
DEBATER_FETCHERS: dict[str, Callable[[SearchClient, str, Locale], PanchResult]] = {
    "ai_mode": fetch_ai_mode,
    "copilot": fetch_copilot,
    "brave": fetch_brave,
}
STANCES = ("agrees", "partly", "disagrees")
MAX_QUERY_CHARS = 420
# Every debate query contains this phrase; sample mode uses it to recognise one.
MARKER = "Another AI search engine"

_CITES = re.compile(r"\s*\[\d{1,2}\]")
_SENTENCE = re.compile(r"(?<=[.!?।])\s+")


def clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def lead(markdown: str, limit: int = 240) -> str:
    """The first one or two sentences of an answer, without citation markers."""
    text = _CITES.sub("", plain_text(markdown))
    sentences = [s.strip() for s in _SENTENCE.split(text) if len(s.split()) >= 4]
    if not sentences:
        return clip(text, limit)
    out = sentences[0]
    if len(out) < 90 and len(sentences) > 1:
        out = f"{out} {sentences[1]}"
    return clip(out, limit)


_FILLER = re.compile(
    r"^\s*(yes|no|correct|right|true|agreed|indeed|mostly accurate|that is (?:correct|right|accurate|not accurate)|"
    r"that's (?:correct|right)|i agree|that is not quite right)\b[^.!?]{0,60}[.!?:]\s+", re.IGNORECASE)


def substance(text: str) -> str:
    """Drop a leading "Yes, that is correct." so what is passed on is the actual claim."""
    stripped = _FILLER.sub("", text or "", count=1).strip()
    if stripped and stripped != text:
        stripped = stripped[0].upper() + stripped[1:]
    return stripped if len(stripped.split()) >= 5 else (text or "")


def challenge_query(question: str, quote: str, round_no: int) -> str:
    """Build the search query that puts a rival's claim in front of a debater."""
    if round_no == 1:
        head = f'{question} {MARKER} answered: "'
        tail = '". Is that accurate? Correct anything that is wrong.'
    else:
        head = f'{question} {MARKER} now says: "'
        tail = '". Do you agree? Give your final answer.'
    room = max(80, MAX_QUERY_CHARS - len(head) - len(tail))
    return head + clip(quote, room) + tail


# --------------------------------------------------------------------------- stance rules

_DISAGREE = (
    "not accurate", "inaccurate", "incorrect", "not correct", "not true", "isn't true", "is false",
    "that's false", "misleading", "not quite", "is wrong", "that is wrong", "i disagree", "disagree",
    "not entirely", "this is a misconception", "common misconception", "गलत", "सही नहीं",
)
_PARTLY = (
    "partly", "partially", "mostly", "largely", "somewhat", "however", "but ", "nuance", "oversimplif",
    "depends", "not always", "to some extent", "half true", "with caveats", "आंशिक",
)
_AGREE = (
    "yes", "correct", "accurate", "true", "that's right", "agree", "indeed", "confirm", "right", "सही", "हाँ",
)


def heuristic_stance(reply: str) -> str:
    """Judge a reply's stance from its opening words. Simple, visible rules; the LLM does it better."""
    opening = " ".join(_SENTENCE.split(_CITES.sub("", plain_text(reply)))[:2]).lower()
    first = opening.split(".")[0]
    if re.match(r"^\s*(no\b|nope\b|not\b)", first) or any(p in first for p in _DISAGREE):
        return "disagrees"
    if any(p in opening for p in _PARTLY):
        return "partly"
    if any(re.search(rf"\b{re.escape(p)}\b", first) for p in _AGREE):
        return "agrees"
    return "partly"


# --------------------------------------------------------------------------- data


@dataclass
class Turn:
    round: int
    speaker: str
    rival: str
    quote: str
    query: str
    status: str = "spoke"  # spoke | silent
    reply: str = ""
    references: list[dict[str, Any]] = field(default_factory=list)
    stance: str = "partly"
    summary: str = ""
    latency_ms: int = 0
    cached: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "speaker": self.speaker,
            "speaker_name": PANCHES[self.speaker].name,
            "rival": self.rival,
            "rival_name": PANCHES[self.rival].name,
            "quote": self.quote,
            "query": self.query,
            "status": self.status,
            "reply": self.reply[:2500],
            "references": self.references[:6],
            "stance": self.stance,
            "summary": self.summary,
            "latency_ms": self.latency_ms,
            "cached": self.cached,
            "note": self.note,
        }


@dataclass
class DebateResult:
    debaters: list[str]
    openings: dict[str, str]
    turns: list[Turn] = field(default_factory=list)
    rounds_used: int = 0
    consensus: bool = False
    resolution: str = ""
    endorsed_by: list[str] = field(default_factory=list)
    holdouts: list[str] = field(default_factory=list)

    def transcript(self) -> str:
        """Plain-text transcript for the sarpanch's verdict prompt."""
        lines = ["Opening answers:"]
        lines += [f"- {PANCHES[p].name}: {self.openings[p]}" for p in self.debaters]
        for r in range(1, self.rounds_used + 1):
            lines.append(f"Round {r}:")
            for t in self.turns:
                if t.round == r and t.status == "spoke":
                    lines.append(f'- {PANCHES[t.speaker].name} was shown {PANCHES[t.rival].name}\'s claim "{t.quote}" '
                                 f"and {t.stance}: {t.summary}")
        state = "reached consensus" if self.consensus else "did not fully agree"
        lines.append(f"Outcome: the engines {state} after {self.rounds_used} round(s).")
        if self.resolution:
            lines.append(f"Agreed position: {self.resolution}")
        if self.holdouts:
            lines.append("Still holding out: " + ", ".join(PANCHES[p].name for p in self.holdouts))
        return "\n".join(lines)


# --------------------------------------------------------------------------- the session


def _event(kind: str, **data: Any) -> dict[str, Any]:
    return {"event": kind, "data": data}


def pick_debaters(answering: list[PanchResult], enabled: tuple[str, ...]) -> list[PanchResult]:
    return [p for p in answering if p.id in DEBATER_FETCHERS and p.id in enabled]


class Debate:
    def __init__(self, client: SearchClient, llm: LLM | None, rounds: int, language: str, locale: Locale) -> None:
        self.client = client
        self.llm = llm
        self.rounds = max(1, rounds)
        self.language = language
        self.locale = locale

    async def _speak(self, turn: Turn) -> Turn:
        started = time.perf_counter()
        fetch = DEBATER_FETCHERS[turn.speaker]
        try:
            result = await asyncio.to_thread(fetch, self.client, turn.query, self.locale)
        except SerpApiError as exc:
            result = PanchResult(id=turn.speaker, status="abstained", note=str(exc)[:200])
        except Exception as exc:  # a broken engine sits this round out
            result = PanchResult(id=turn.speaker, status="error", note=f"{exc.__class__.__name__}"[:200])
        turn.latency_ms = int((time.perf_counter() - started) * 1000)
        if result.status != "answered" or not result.answer.strip():
            turn.status = "silent"
            turn.note = result.note or "No reply this round."
            turn.stance = "silent"
            return turn
        turn.reply = result.answer
        turn.references = [{k: r.get(k, "") for k in ("title", "link", "domain")} for r in result.references]
        turn.cached = result.cached
        turn.stance = heuristic_stance(result.answer)
        turn.summary = lead(result.answer)
        return turn

    def _judge(self, question: str, turns: list[Turn]) -> None:
        """Let the clerk LLM read the round and set each stance + one-line summary."""
        spoke = [t for t in turns if t.status == "spoke"]
        if not self.llm or not spoke:
            return
        blocks = "\n\n".join(
            f'<turn speaker="{t.speaker}">\nStatement it was shown: "{t.quote}"\nIts reply:\n{clip(plain_text(t.reply), 1800)}\n</turn>'
            for t in spoke
        )
        try:
            raw = self.llm.complete_json(prompts.JUDGE_SYSTEM.format(language=self.language),
                                         prompts.judge_user(question, blocks), max_tokens=1500)
        except (LLMError, Exception):
            return  # keep the rule-based stances
        verdicts = raw.get("turns") if isinstance(raw, dict) else None
        if not isinstance(verdicts, dict):
            return
        for t in spoke:
            v = verdicts.get(t.speaker)
            if not isinstance(v, dict):
                continue
            stance = str(v.get("stance", "")).lower().strip()
            if stance in STANCES:
                t.stance = stance
            summary = " ".join(str(v.get("summary", "")).split())
            if summary:
                t.summary = clip(summary, 260)

    async def run(self, question: str, debaters: list[PanchResult], result_box: list[DebateResult]) -> AsyncIterator[dict[str, Any]]:
        ids = [p.id for p in debaters]
        openings = {p.id: lead(p.answer) for p in debaters}
        result = DebateResult(debaters=ids, openings=openings)
        result_box.append(result)
        yield _event("debate_start", debaters=[{"id": i, "name": PANCHES[i].name, "initials": PANCHES[i].initials} for i in ids],
                     rounds=self.rounds, openings=[{"panch": i, "text": openings[i]} for i in ids])

        latest = dict(openings)  # what each engine most recently said
        for round_no in range(1, self.rounds + 1):
            yield _event("debate_round", round=round_no, status="started")
            turns = []
            for i, speaker in enumerate(ids):
                rival = ids[(i + 1) % len(ids)]
                quote = latest[rival]
                turns.append(Turn(round_no, speaker, rival, quote, challenge_query(question, quote, round_no)))

            tasks = [asyncio.ensure_future(self._speak(t)) for t in turns]
            for finished in asyncio.as_completed(tasks):
                turn = await finished
                yield _event("debate_turn", turn={**turn.to_dict(), "judged": False})

            if self.llm:
                yield _event("step", text=f"Judging round {round_no}")
                await asyncio.to_thread(self._judge, question, turns)
            for t in turns:
                yield _event("debate_turn", turn={**t.to_dict(), "judged": True})
                if t.status == "spoke":
                    latest[t.speaker] = substance(t.summary) or latest[t.speaker]

            result.turns.extend(turns)
            result.rounds_used = round_no
            spoke = [t for t in turns if t.status == "spoke"]
            agree = [t.speaker for t in spoke if t.stance == "agrees"]
            consensus = len(spoke) >= 2 and len(agree) == len(spoke)
            yield _event("debate_round", round=round_no, status="done", consensus=consensus,
                         stances={t.speaker: t.stance for t in turns}, agree=len(agree), spoke=len(spoke))
            if consensus:
                result.consensus = True
                break

        self._resolve(result)
        yield _event("debate_end", consensus=result.consensus, rounds_used=result.rounds_used, resolution=result.resolution,
                     endorsed_by=result.endorsed_by, holdouts=result.holdouts)

    @staticmethod
    def _resolve(result: DebateResult) -> None:
        """The position the engines converged on: the latest statement that the most debaters accepted."""
        last_round = [t for t in result.turns if t.round == result.rounds_used and t.status == "spoke"]
        if not last_round:
            return
        result.holdouts = [t.speaker for t in last_round if t.stance == "disagrees"]
        endorsed = [t for t in last_round if t.stance == "agrees"] or [t for t in last_round if t.stance == "partly"]
        if not endorsed:
            return
        # Everyone who agreed accepted their rival's statement, so that statement plus the
        # agreeing replies is the shared position. Prefer the reply that says it most fully.
        best = max(endorsed, key=lambda t: len(substance(t.summary)))
        result.resolution = substance(best.summary)
        result.endorsed_by = [t.speaker for t in endorsed]


def debate_markdown(result: DebateResult) -> str:
    """A short, honest paragraph about the debate for the no-LLM verdict."""
    if not result.rounds_used:
        return ""
    rounds = f"{result.rounds_used} round{'s' if result.rounds_used != 1 else ''}"
    names = lambda ids: ", ".join(PANCHES[i].name for i in ids)  # noqa: E731
    if result.consensus:
        text = f"**After the debate:** the engines cross-examined each other and reached consensus in {rounds}."
    elif result.holdouts:
        text = f"**After the debate:** after {rounds}, {names(result.holdouts)} still disagreed with the others."
    else:
        text = f"**After the debate:** after {rounds}, the engines broadly accepted each other's answers, with some caveats."
    if result.resolution:
        text += f" The position they settled on: *{result.resolution}*"
    return text
