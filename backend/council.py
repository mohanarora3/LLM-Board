"""The council: merge sources, extract claims, measure agreement, and prepare the verdict.

Two extraction modes:
- LLM mode: a model acting as clerk records each claim and every panch's position
  (supports / contradicts / silent), including contradictions.
- Heuristic mode (no LLM key): sentences are clustered by word overlap across panches.
  It measures agreement well but cannot detect contradictions, and says so.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from . import credibility, prompts
from .llm import LLM, LLMError
from .normalize import plain_text
from .panches import PANCHES, PanchResult

POSITIONS = {"supports", "contradicts", "silent"}

# --------------------------------------------------------------------------- sources


def canonical_url(url: str) -> str:
    parts = urlparse(url)
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(("utm_", "fbclid", "gclid", "srsltid"))])
    path = parts.path.rstrip("/") or "/"
    return urlunparse(("", host, path, "", query, ""))


@dataclass
class SourceBook:
    sources: list[dict[str, Any]] = field(default_factory=list)
    lookup: dict[tuple[str, int], int] = field(default_factory=dict)  # (panch_id, local n) -> source id

    def ids_for(self, panch_id: str, local_numbers: Iterable[int]) -> list[int]:
        out: list[int] = []
        for n in local_numbers:
            sid = self.lookup.get((panch_id, n))
            if sid and sid not in out:
                out.append(sid)
        return out


def build_sources(panches: list[PanchResult]) -> SourceBook:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    members: dict[tuple[str, int], str] = {}
    for panch in panches:
        if panch.status != "answered":
            continue
        for n, ref in enumerate(panch.references, start=1):
            key = canonical_url(ref["link"])
            members[(panch.id, n)] = key
            if key not in merged:
                merged[key] = {**ref, "cited_by": [], **credibility.describe(ref.get("domain", ""))}
                order.append(key)
            entry = merged[key]
            if panch.id not in entry["cited_by"]:
                entry["cited_by"].append(panch.id)
            if not entry.get("snippet") and ref.get("snippet"):
                entry["snippet"] = ref["snippet"]

    ranked = sorted(order, key=lambda k: (-len(merged[k]["cited_by"]), -merged[k]["tier_score"], order.index(k)))
    book = SourceBook()
    ids: dict[str, int] = {}
    for sid, key in enumerate(ranked, start=1):
        ids[key] = sid
        book.sources.append({"id": sid, **merged[key]})
    book.lookup = {member: ids[key] for member, key in members.items()}
    return book


# --------------------------------------------------------------------------- claims (LLM)


def panch_blocks(panches: list[PanchResult], limit: int = 3500) -> str:
    blocks = []
    for p in panches:
        text = p.answer if len(p.answer) <= limit else p.answer[:limit] + " …"
        blocks.append(f'<panch id="{p.id}" name="{PANCHES[p.id].name}">\n{text}\n</panch>')
    return "\n\n".join(blocks)


def clean_claims(raw: Any, panch_ids: list[str]) -> list[dict[str, Any]]:
    """Validate the clerk's JSON so a sloppy model reply can't break the UI."""
    claims = []
    for item in (raw or {}).get("claims", []) if isinstance(raw, dict) else []:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get("text", "")).split())
        if not text:
            continue
        positions_in = item.get("positions") if isinstance(item.get("positions"), dict) else {}
        cites_in = item.get("cites") if isinstance(item.get("cites"), dict) else {}
        positions = {}
        cites = {}
        for pid in panch_ids:
            pos = str(positions_in.get(pid, "silent")).lower().strip()
            positions[pid] = pos if pos in POSITIONS else "silent"
            nums = cites_in.get(pid) or []
            cites[pid] = [int(n) for n in nums if isinstance(n, (int, float)) or str(n).isdigit()][:6] if isinstance(nums, list) else []
        try:
            importance = min(3, max(1, int(item.get("importance", 2))))
        except (TypeError, ValueError):
            importance = 2
        claims.append({"text": text, "importance": importance, "positions": positions, "cites": cites})
    return claims[:12]


def extract_claims_llm(llm: LLM, question: str, panches: list[PanchResult], language: str) -> list[dict[str, Any]]:
    ids = [p.id for p in panches]
    raw = llm.complete_json(
        prompts.CLAIMS_SYSTEM.format(language=language),
        prompts.claims_user(question, panch_blocks(panches), ids),
        max_tokens=8192,
    )
    claims = clean_claims(raw, ids)
    if not claims:
        raise LLMError("The clerk returned no claims")
    return claims


# --------------------------------------------------------------------------- claims (heuristic)

_STOP = set(
    """a an the and or but if then than so of to in on at by for with from as is are was were be been being it its this that these
    those there their they them he she his her you your we our i me my not no yes can could should would will may might do does did
    has have had also more most such into about over after before during between which who whom what when where why how all any each
    other some only very just one two per via et al vs use used using get gets like
    का के की है हैं में से को और या पर यह वह एक भी तो ही कि था थे थी जो लिए नहीं""".split()
)
_SENT_SPLIT = re.compile(r"(?<=[.!?।])\s+|\n+")
_CITE = re.compile(r"\[(\d{1,2})\]")
_WORD = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {w for w in (m.group(0).lower() for m in _WORD.finditer(text)) if len(w) > 2 and w not in _STOP and not w.isdigit()} | {
        m.group(0) for m in re.finditer(r"\d+(?:\.\d+)?", text)
    }


def _similar(a: set[str], b: set[str]) -> bool:
    common = len(a & b)
    if common < 3:
        return False
    return common / min(len(a), len(b)) >= 0.6 or common / len(a | b) >= 0.4


_TRAILING_CITES = re.compile(r"([.!?।])((?:\s*\[\d{1,2}\])+)")


def extract_claims_heuristic(panches: list[PanchResult]) -> list[dict[str, Any]]:
    ids = [p.id for p in panches]
    sentences = []
    for p in panches:
        # "…orbit. [1][2]" → "…orbit [1][2]." so citations stay with their sentence.
        text_all = _TRAILING_CITES.sub(lambda m: m.group(2) + m.group(1), plain_text(p.answer))
        for raw in _SENT_SPLIT.split(text_all):
            cites = [int(n) for n in _CITE.findall(raw)]
            stripped = _CITE.sub("", raw).strip()
            if stripped.endswith(":"):
                continue  # a list heading, not a claim
            text = re.sub(r"\s+([.,;:!?।])", r"\1", " ".join(stripped.split())).strip(" -•:")
            words = len(text.split())
            if 6 <= words <= 60:
                sentences.append({"panch": p.id, "text": text, "cites": cites, "tokens": _tokens(text)})

    clusters: list[list[dict[str, Any]]] = []
    for sent in sentences:
        if not sent["tokens"]:
            continue
        for cluster in clusters:
            if _similar(sent["tokens"], cluster[0]["tokens"]):
                cluster.append(sent)
                break
        else:
            clusters.append([sent])

    def weight(cluster: list[dict[str, Any]]) -> tuple[int, int]:
        return (len({s["panch"] for s in cluster}), len(cluster))

    clusters.sort(key=weight, reverse=True)
    claims = []
    for rank, cluster in enumerate(clusters[:8]):
        members = {s["panch"] for s in cluster}
        rep = sorted(cluster, key=lambda s: len(s["text"]))[len(cluster) // 2]
        cites: dict[str, list[int]] = {pid: [] for pid in ids}
        for s in cluster:
            cites[s["panch"]] = sorted(set(cites[s["panch"]] + s["cites"]))
        claims.append({
            "text": rep["text"],
            "importance": 3 if rank < 2 else 2 if rank < 5 else 1,
            "positions": {pid: "supports" if pid in members else "silent" for pid in ids},
            "cites": cites,
        })
    return claims


# --------------------------------------------------------------------------- scoring

STATUS_LABELS = {
    "consensus": "Strong agreement",
    "majority": "Majority",
    "disputed": "Disputed",
    "minority": "Minority view",
}


def score_council(claims: list[dict[str, Any]], panches: list[PanchResult], book: SourceBook) -> dict[str, Any]:
    answering = [p.id for p in panches if p.status == "answered"]
    n = len(answering)
    majority = n // 2 + 1
    strong = max(2, math.ceil(0.75 * n)) if n > 1 else 1

    agree = {pid: 0.0 for pid in answering}
    disagree = {pid: 0.0 for pid in answering}
    against: dict[str, list[int]] = {pid: [] for pid in answering}

    scored = []
    for idx, claim in enumerate(claims, start=1):
        positions = {pid: claim["positions"].get(pid, "silent") for pid in answering}
        supporters = [pid for pid, pos in positions.items() if pos == "supports"]
        opponents = [pid for pid, pos in positions.items() if pos == "contradicts"]
        s, x = len(supporters), len(opponents)
        if s == 0:
            continue
        if s >= strong and x == 0:
            status = "consensus"
        elif s >= majority and s > x:
            status = "majority"  # may still carry a dissent; the UI shows the count
        elif x:
            status = "disputed"
        else:
            status = "minority"

        w = claim["importance"]
        if status in {"consensus", "majority"}:
            for pid in supporters:
                agree[pid] += w
            for pid in opponents:
                disagree[pid] += w
                against[pid].append(idx)
        elif status == "disputed":
            winners, losers = (supporters, opponents) if s > x else (opponents, supporters) if x > s else ([], [])
            for pid in winners:
                agree[pid] += w
            for pid in losers:
                disagree[pid] += w
                against[pid].append(idx)

        sources = []
        for pid in supporters:
            sources += [sid for sid in book.ids_for(pid, claim["cites"].get(pid, [])) if sid not in sources]
        scored.append({
            "id": idx,
            "text": claim["text"],
            "importance": w,
            "status": status,
            "status_label": STATUS_LABELS[status],
            "support": s,
            "contradict": x,
            "positions": positions,
            "sources": sources[:6],
        })

    stances = {}
    for pid in answering:
        a, d = agree[pid], disagree[pid]
        if a + d == 0:
            stance = "unclear"
        else:
            ratio = a / (a + d)
            stance = "agrees" if ratio >= 0.75 else "partly" if ratio >= 0.4 else "dissents"
        stances[pid] = {"stance": stance, "against_claims": against[pid]}
    for p in panches:
        if p.status != "answered":
            stances[p.id] = {"stance": "abstained", "against_claims": []}

    # Agreement on the core answer: for each core claim, the share of answering panches
    # backing it (net of panches contradicting it), weighted by importance.
    core = [c for c in scored if c["importance"] == 3] or scored
    if core and n:
        weight = sum(c["importance"] for c in core)
        backing = sum(c["importance"] * max(0, c["support"] - c["contradict"]) / n for c in core)
        consensus = round(100 * backing / weight)
    else:
        consensus = 0
    agreeing = sum(1 for pid in answering if stances[pid]["stance"] == "agrees")
    dissenting = [pid for pid in answering if stances[pid]["stance"] in {"dissents", "partly"}]

    if n == 0:
        ruling = "No panch could answer"
    elif n == 1:
        ruling = "Only one panch answered"
    elif consensus >= 75 and not dissenting:
        ruling = "Strong agreement"
    elif consensus >= 50:
        ruling = "Broad agreement, with dissent" if dissenting else "Broad agreement"
    else:
        ruling = "The panches are divided"

    return {
        "claims": scored,
        "stances": stances,
        "answering": n,
        "agreeing": agreeing,
        "dissenting": dissenting,
        "consensus": consensus,
        "ruling": ruling,
        "headline": f"{agreeing} of {n} panches agree" if n else "No answers",
    }


# --------------------------------------------------------------------------- verdict inputs


def ledger_text(council: dict[str, Any]) -> str:
    lines = []
    for c in council["claims"]:
        sup = [PANCHES[p].name for p, pos in c["positions"].items() if pos == "supports"]
        con = [PANCHES[p].name for p, pos in c["positions"].items() if pos == "contradicts"]
        tag = {"consensus": "strong agreement", "majority": "majority", "disputed": "disputed", "minority": "minority view"}[c["status"]]
        line = f"C{c['id']} [{tag}; {c['support']} of {council['answering']} support] {c['text']}\n    supported by: {', '.join(sup)}"
        if con:
            line += f"\n    contradicted by: {', '.join(con)}"
        line += f"\n    sources: {''.join(f'[{s}]' for s in c['sources']) or 'none'}"
        lines.append(line)
    return "\n".join(lines)


def sources_text(book: SourceBook, limit: int = 24) -> str:
    return "\n".join(
        f"[{s['id']}] {s['title']} — {s['domain']} ({s['tier_label']}; cited by {len(s['cited_by'])} panch{'es' if len(s['cited_by']) != 1 else ''})"
        for s in book.sources[:limit]
    )


def heuristic_verdict(council: dict[str, Any]) -> str:
    """A plain, honest verdict assembled from the ledger when no LLM is configured."""
    claims = council["claims"]
    if not claims:
        return "The panches answered, but their answers didn't share enough common ground to form a verdict. Open each panch in the council panel to read them directly."

    def cite(c: dict[str, Any]) -> str:
        return "".join(f" [{s}]" for s in c["sources"][:3])

    agreed = [c for c in claims if c["status"] in {"consensus", "majority"}]
    others = [c for c in claims if c["status"] in {"minority", "disputed"}]
    lead = agreed[0] if agreed else claims[0]
    parts = [f"{lead['text']}{cite(lead)}"]
    rest = [c for c in agreed if c is not lead]
    if rest:
        parts.append("**What most panches agree on**\n" + "\n".join(f"- {c['text']}{cite(c)}" for c in rest))
    if others:
        lines = []
        for c in others[:4]:
            who = [PANCHES[p].name for p, pos in c["positions"].items() if pos == "supports"]
            lines.append(f"- Only {', '.join(who)}: {c['text']}{cite(c)}")
        parts.append("**Said by fewer panches**\n" + "\n".join(lines))
    parts.append("_Verdict assembled without an LLM, by matching sentences across panches. Add an LLM key for contradiction checks and a written summary._")
    return "\n\n".join(parts)
