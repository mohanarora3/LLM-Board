import unittest

from backend import credibility
from backend.council import (
    build_sources,
    canonical_url,
    clean_claims,
    extract_claims_heuristic,
    heuristic_verdict,
    ledger_text,
    score_council,
)
from backend.llm import LLMError, parse_json
from backend.panches import PanchResult

IDS = ["ai_overview", "ai_mode", "copilot", "brave", "web"]


def panch(pid, answer="", refs=None, status="answered"):
    return PanchResult(id=pid, status=status, answer=answer, references=refs or [])


def ref(link):
    return {"title": link, "link": link, "snippet": "", "source": "", "domain": link.split("/")[2].removeprefix("www.")}


def claim(text, importance=3, **positions):
    return {"text": text, "importance": importance,
            "positions": {p: positions.get(p, "silent") for p in IDS}, "cites": {p: [1] for p in IDS}}


class SourceTests(unittest.TestCase):
    def test_canonical_url_drops_tracking(self):
        self.assertEqual(canonical_url("https://www.x.com/a/?utm_source=z&id=2#top"), "//x.com/a?id=2")

    def test_sources_merge_and_rank_by_citations(self):
        panches = [
            panch("ai_overview", "x", [ref("https://blog.example.com/p"), ref("https://www.who.int/a")]),
            panch("ai_mode", "y", [ref("https://who.int/a/")]),
            panch("brave", "", [ref("https://ignored.com")], status="abstained"),
        ]
        book = build_sources(panches)
        self.assertEqual(len(book.sources), 2)
        self.assertEqual(book.sources[0]["domain"], "who.int")
        self.assertEqual(book.sources[0]["cited_by"], ["ai_overview", "ai_mode"])
        self.assertEqual(book.sources[0]["tier"], "official")
        self.assertEqual(book.ids_for("ai_mode", [1]), [1])

    def test_credibility_tiers(self):
        self.assertEqual(credibility.tier_of("pib.gov.in"), "official")
        self.assertEqual(credibility.tier_of("en.wikipedia.org"), "reference")
        self.assertEqual(credibility.tier_of("thehindu.com"), "news")
        self.assertEqual(credibility.tier_of("old.reddit.com"), "community")
        self.assertEqual(credibility.tier_of("random-blog.in"), "web")


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.panches = [panch(p, "answer", [ref(f"https://{p}.com/x")]) for p in IDS]
        self.book = build_sources(self.panches)

    def test_dissent_is_detected(self):
        claims = [
            claim("Not visible from the Moon", ai_overview="supports", ai_mode="supports", copilot="supports", brave="supports", web="supports"),
            claim("Hard to see from orbit", ai_overview="supports", ai_mode="supports", copilot="supports", web="supports", brave="contradicts"),
            claim("Visible from orbit", 2, brave="supports", ai_overview="contradicts", ai_mode="contradicts"),
        ]
        council = score_council(clean_claims({"claims": claims}, IDS), self.panches, self.book)
        statuses = [c["status"] for c in council["claims"]]
        self.assertEqual(statuses, ["consensus", "majority", "disputed"])
        self.assertEqual(council["stances"]["brave"]["stance"], "dissents")
        self.assertEqual(council["agreeing"], 4)
        self.assertEqual(council["headline"], "4 of 5 panches agree")
        self.assertEqual(council["consensus"], 80)
        self.assertIn("contradicted by: Brave AI", ledger_text(council))

    def test_abstained_panches_are_excluded(self):
        panches = self.panches[:3] + [panch("brave", status="abstained"), panch("web", status="error")]
        claims = [claim("X", ai_overview="supports", ai_mode="supports", copilot="supports", brave="supports")]
        council = score_council(clean_claims({"claims": claims}, IDS), panches, self.book)
        self.assertEqual(council["answering"], 3)
        self.assertEqual(council["claims"][0]["support"], 3)
        self.assertEqual(council["stances"]["brave"]["stance"], "abstained")

    def test_unsupported_claims_are_dropped(self):
        council = score_council(clean_claims({"claims": [claim("nobody says this")]}, IDS), self.panches, self.book)
        self.assertEqual(council["claims"], [])

    def test_clean_claims_rejects_bad_values(self):
        out = clean_claims({"claims": [{"text": " ok ", "importance": "9", "positions": {"ai_mode": "maybe"}, "cites": {"ai_mode": ["2", "x"]}}, "junk"]}, IDS)
        self.assertEqual(out[0]["importance"], 3)
        self.assertEqual(out[0]["positions"]["ai_mode"], "silent")
        self.assertEqual(out[0]["cites"]["ai_mode"], [2])

    def test_heuristic_clusters_agreeing_sentences(self):
        panches = [
            panch("ai_overview", "The Great Wall is not visible from the Moon with the naked eye. [1]"),
            panch("ai_mode", "From the Moon, the Great Wall is not visible to the naked eye at all [1]."),
            panch("copilot", "Bananas are a good source of potassium for most adults today."),
        ]
        claims = extract_claims_heuristic(panches)
        top = claims[0]
        self.assertEqual(top["positions"]["ai_overview"], "supports")
        self.assertEqual(top["positions"]["ai_mode"], "supports")
        self.assertEqual(top["positions"]["copilot"], "silent")
        self.assertEqual(top["cites"]["ai_mode"], [1])
        self.assertNotIn(" .", top["text"])

    def test_heuristic_verdict_mentions_minority(self):
        claims = [claim("A widely held view", ai_overview="supports", ai_mode="supports", copilot="supports"),
                  claim("A lone view", 1, brave="supports")]
        council = score_council(clean_claims({"claims": claims}, IDS), self.panches, self.book)
        text = heuristic_verdict(council)
        self.assertIn("A widely held view", text)
        self.assertIn("Only Brave AI", text)


class JsonParsingTests(unittest.TestCase):
    def test_parse_json_with_fences(self):
        self.assertEqual(parse_json('Sure!\n```json\n{"claims": []}\n```'), {"claims": []})

    def test_parse_json_errors(self):
        with self.assertRaises(LLMError):
            parse_json("no json here")


if __name__ == "__main__":
    unittest.main()
