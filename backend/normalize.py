"""Turn the different SerpApi AI-answer formats into one shape: markdown plus numbered references.

Google AI Overview, Google AI Mode, Bing Copilot and Brave AI Mode all return
`text_blocks` (paragraph / heading / list / table / code blocks) that point at
`references` through `reference_indexes`. Brave also nests text in `segments`
with per-segment `citations`. Every panch answer is rendered as markdown whose
citations are local markers like [1], [2] that index into its own reference list.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

MAX_ANSWER_CHARS = 7000


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def normalize_references(raw_refs: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], dict[int, int]]:
    """Return (references, map from SerpApi reference index -> local 1-based number)."""
    refs: list[dict[str, Any]] = []
    by_link: dict[str, int] = {}
    index_map: dict[int, int] = {}
    for position, raw in enumerate(raw_refs or []):
        if not isinstance(raw, dict):
            continue
        link = raw.get("link") or raw.get("url")
        if not link or not str(link).startswith(("http://", "https://")):
            continue
        raw_index = raw.get("index", position)
        if link in by_link:
            local = by_link[link]
        else:
            refs.append(
                {
                    "title": (raw.get("title") or domain_of(link) or link).strip(),
                    "link": link,
                    "snippet": (raw.get("snippet") or raw.get("cited_snippet") or "").strip(),
                    "source": (raw.get("source") or "").strip(),
                    "domain": domain_of(link),
                }
            )
            local = len(refs)
            by_link[link] = local
        if isinstance(raw_index, int):
            index_map[raw_index] = local
    return refs, index_map


def _indexes_from(value: Any) -> list[int]:
    """Accept [0, 2], [{'reference_index': 1}], or a single int."""
    out: list[int] = []
    if value is None:
        return out
    if isinstance(value, (int, dict)):
        value = [value]
    if not isinstance(value, list):
        return out
    for item in value:
        if isinstance(item, int):
            out.append(item)
        elif isinstance(item, dict):
            idx = item.get("reference_index", item.get("index"))
            if isinstance(idx, int):
                out.append(idx)
    return out


def _cite(raw_indexes: list[int], index_map: dict[int, int]) -> str:
    seen: list[int] = []
    for raw in raw_indexes:
        local = index_map.get(raw)
        if local and local not in seen:
            seen.append(local)
    return "".join(f" [{n}]" for n in seen[:4])


def _segments_text(segments: list[Any], index_map: dict[int, int]) -> str:
    parts = []
    for seg in segments:
        if isinstance(seg, str):
            parts.append(seg)
        elif isinstance(seg, dict):
            text = seg.get("snippet") or seg.get("text") or ""
            body = text.rstrip()
            cite = _cite(_indexes_from(seg.get("citations") or seg.get("reference_indexes")), index_map)
            parts.append(body + cite + text[len(body):])  # keep the segment's trailing space after the marker
    return "".join(parts).strip()


def _item_text(item: dict[str, Any], index_map: dict[int, int]) -> str:
    title = (item.get("title") or "").strip()
    if item.get("segments"):
        body = _segments_text(item["segments"], index_map)
    else:
        body = (item.get("snippet") or "").strip()
    if title and body and not body.startswith(title):
        text = f"**{title.rstrip(':')}**: {body}"
    else:
        text = body or (f"**{title}**" if title else "")
    return text + _cite(_indexes_from(item.get("reference_indexes") or item.get("citations")), index_map)


def _list_lines(items: list[Any], index_map: dict[int, int], depth: int, ordered: bool = False) -> list[str]:
    lines: list[str] = []
    indent = "  " * depth
    for n, item in enumerate(items, start=1):
        if isinstance(item, str):
            text = item.strip()
            nested = None
        elif isinstance(item, dict):
            text = _item_text(item, index_map)
            nested = item.get("list")
        else:
            continue
        if text:
            bullet = f"{n}." if ordered else "-"
            lines.append(f"{indent}{bullet} {text}")
        if isinstance(nested, list) and nested:
            lines.extend(_list_lines(nested, index_map, depth + 1))
    return lines


def _table_lines(block: dict[str, Any]) -> list[str]:
    rows = block.get("table") or block.get("rows") or []
    headers = block.get("headers")
    if not rows and isinstance(block.get("formatted"), list) and block["formatted"]:
        first = block["formatted"][0]
        if isinstance(first, dict):
            headers = headers or list(first.keys())
            rows = [[str(r.get(h, "")) for h in headers] for r in block["formatted"] if isinstance(r, dict)]
    rows = [r for r in rows if isinstance(r, list)]
    if not rows:
        return []
    if not headers:
        headers, rows = rows[0], rows[1:]

    def cell(value: Any) -> str:
        return str(value).replace("|", "/").replace("\n", " ").strip()

    lines = ["| " + " | ".join(cell(h) for h in headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return lines


def _block_lines(block: Any, index_map: dict[int, int], depth: int = 0) -> list[str]:
    if isinstance(block, str):
        return [block.strip()] if block.strip() else []
    if not isinstance(block, dict):
        return []
    kind = (block.get("type") or "paragraph").lower()
    cites = _cite(
        _indexes_from(block.get("reference_indexes")) + _indexes_from(block.get("citations")), index_map
    )

    if isinstance(block.get("text_blocks"), list):  # some answers nest blocks (expandable sections)
        nested: list[str] = []
        for child in block["text_blocks"]:
            nested.extend(_block_lines(child, index_map, depth))
        return nested

    if block.get("segments"):
        text = _segments_text(block["segments"], index_map)
    else:
        text = (block.get("snippet") or block.get("title") or block.get("text") or "").strip()

    if kind == "heading":
        return [f"### {text}"] if text else []
    if kind in {"code_block", "code"}:
        code = block.get("code") or block.get("snippet") or ""
        lang = block.get("language") or ""
        return [f"```{lang}", code.rstrip(), "```"] if code else []
    if kind == "table":
        return _table_lines(block)

    lines: list[str] = [text + cites] if text else []
    if isinstance(block.get("list"), list):
        lines.extend(_list_lines(block["list"], index_map, depth, ordered=kind == "ordered_list"))
    return lines


def answer_from_blocks(data: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Render any SerpApi AI answer (AI Overview / AI Mode / Copilot / Brave) as markdown + references."""
    refs, index_map = normalize_references(data.get("references"))
    chunks: list[str] = []

    header = data.get("header")
    if isinstance(header, str) and header.strip():
        chunks.append(header.strip())

    for block in data.get("text_blocks") or []:
        lines = _block_lines(block, index_map)
        if lines:
            chunks.append("\n".join(lines))

    if not chunks:
        markdown = data.get("markdown") or data.get("answer") or ""
        if isinstance(markdown, str) and markdown.strip():
            chunks.append(markdown.strip())

    text = "\n\n".join(chunks).strip()
    if len(text) > MAX_ANSWER_CHARS:
        text = text[:MAX_ANSWER_CHARS].rsplit("\n", 1)[0] + "\n\n…"
    return text, refs


_MD_NOISE = re.compile(r"(\*\*|__|`{1,3}|^#{1,6}\s*|^\s*[-*]\s+|^\s*\d+\.\s+|^\|?-{3,}.*$)", re.MULTILINE)


def plain_text(markdown: str) -> str:
    """Markdown → plain text, keeping citation markers like [2]."""
    text = _MD_NOISE.sub("", markdown)
    text = text.replace("|", " ")
    return re.sub(r"[ \t]+", " ", text).strip()
