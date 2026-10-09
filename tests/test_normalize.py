import json
import unittest
from pathlib import Path

from backend.normalize import answer_from_blocks, domain_of, normalize_references, plain_text
from backend.panches import build_web_answer

FIX = Path(__file__).resolve().parent.parent / "backend" / "fixtures"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


class NormalizeTests(unittest.TestCase):
    def test_domain_strips_www(self):
        self.assertEqual(domain_of("https://www.nasa.gov/x"), "nasa.gov")
        self.assertEqual(domain_of("https://en.wikipedia.org/wiki/X"), "en.wikipedia.org")

    def test_reference_index_mapping_and_dedupe(self):
        refs, imap = normalize_references([
            {"index": 3, "link": "https://a.com", "title": "A"},
            {"index": 7, "link": "https://b.com"},
            {"index": 9, "link": "https://a.com"},  # duplicate link → same local number
            {"index": 10, "title": "no link"},
        ])
        self.assertEqual(len(refs), 2)
        self.assertEqual(imap, {3: 1, 7: 2, 9: 1})
        self.assertEqual(refs[1]["title"], "b.com")

    def test_ai_overview_blocks_with_list(self):
        md, refs = answer_from_blocks(load("google_ai_overview")["ai_overview"])
        self.assertIn("No, the Great Wall", md)
        self.assertIn("- **Too narrow**: Most sections", md)
        self.assertIn("[3]", md)  # reference index 2 → local 3
        self.assertEqual(len(refs), 5)

    def test_copilot_header_is_included(self):
        md, refs = answer_from_blocks(load("bing_copilot"))
        self.assertTrue(md.startswith("The Great Wall of China is not visible"))
        self.assertEqual(len(refs), 3)

    def test_brave_segments_keep_citations_with_their_text(self):
        md, _ = answer_from_blocks(load("brave_ai_mode"))
        self.assertIn("good conditions. [1] It is not visible", md)

    def test_ai_mode_heading(self):
        md, _ = answer_from_blocks(load("google_ai_mode"))
        self.assertIn("### From low Earth orbit", md)

    def test_table_block(self):
        md, _ = answer_from_blocks({"text_blocks": [{"type": "table", "headers": ["a", "b"], "table": [["1", "2|3"]]}]})
        self.assertIn("| a | b |", md)
        self.assertIn("| 1 | 2/3 |", md)

    def test_markdown_fallback(self):
        md, _ = answer_from_blocks({"markdown": "Plain **answer**"})
        self.assertEqual(md, "Plain **answer**")

    def test_plain_text_keeps_citations(self):
        self.assertEqual(plain_text("### Title\n- **Bold** point [2]"), "Title\nBold point [2]")

    def test_web_answer_uses_results_paa_and_forums(self):
        md, refs = build_web_answer(load("google"), load("google_forums"))
        self.assertIn("**Featured answer**", md)
        self.assertIn("**People also ask**", md)
        self.assertIn("**What people say in forums**", md)
        links = [r["link"] for r in refs]
        self.assertEqual(len(links), len(set(links)))


if __name__ == "__main__":
    unittest.main()
