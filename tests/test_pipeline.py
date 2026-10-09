import asyncio
import dataclasses
import json
import threading
import time
import unittest
from pathlib import Path

import requests

from backend.config import load_settings
from backend.mock import MockSearchClient
from backend.pipeline import Panchayat, detect_language
from backend.serpapi_client import SearchResponse, SerpApiError

FIX = Path(__file__).resolve().parent.parent / "backend" / "fixtures"


class FakeLLM:
    """Stands in for Gemini/OpenAI/Anthropic so the LLM path is tested offline."""

    def __init__(self, fail_claims=False):
        self.fail_claims = fail_claims
        self.prompts = []

    def complete_text(self, system, user, max_tokens=0):
        return "चीन की महान दीवार अंतरिक्ष से दिखती है?"

    def complete_json(self, system, user, max_tokens=0):
        self.prompts.append(user)
        if self.fail_claims:
            raise RuntimeError("model down")
        ids = ["ai_overview", "ai_mode", "copilot", "brave", "web"]
        sup = {p: "supports" for p in ids}
        return {"claims": [
            {"text": "Not visible from the Moon", "importance": 3, "positions": sup, "cites": {"ai_overview": [1]}},
            {"text": "Visible from orbit with the naked eye", "importance": 2,
             "positions": {"brave": "supports", "ai_overview": "contradicts", "ai_mode": "contradicts"}, "cites": {"brave": [1]}},
        ]}

    def stream(self, system, user, max_tokens=0):
        yield "It is not visible from the Moon [1]. "
        yield "One panch, Brave AI, disagrees."


def settings(**overrides):
    return dataclasses.replace(load_settings(), mock=True, **overrides)


def run(panchayat, question="Is the Great Wall visible from space?", **kw):
    async def collect():
        return [e async for e in panchayat.convene(question, **kw)]
    return asyncio.run(collect())


class PipelineTests(unittest.TestCase):
    def test_detect_language(self):
        self.assertEqual(detect_language("Is tea bad?"), "en")
        self.assertEqual(detect_language("क्या चाय खराब है?"), "hi")
        self.assertEqual(detect_language("தேநீர் கெட்டதா?"), "ta")

    def test_full_council_with_llm(self):
        llm = FakeLLM()
        events = run(Panchayat(settings(), MockSearchClient(speed=0.01), llm))
        kinds = [e["event"] for e in events]
        self.assertEqual(kinds[:2], ["start", "query"])
        self.assertEqual(kinds.count("panch"), 5)
        self.assertEqual(kinds[-1], "done")
        council = next(e["data"] for e in events if e["event"] == "council")
        self.assertEqual(council["method"], "llm")
        self.assertEqual(council["council"]["stances"]["brave"]["stance"], "partly")  # agrees on the Moon, loses on orbit
        self.assertIn("brave", council["council"]["dissenting"])
        answer = next(e["data"]["markdown"] for e in events if e["event"] == "answer")
        self.assertIn("Brave AI, disagrees", answer)
        self.assertEqual(events[-1]["data"]["searches"], 6)  # google, ai_overview token, ai_mode, copilot, brave, forums
        self.assertIn('<panch id="brave"', llm.prompts[0])

    def test_llm_failure_falls_back_to_heuristic(self):
        events = run(Panchayat(settings(), MockSearchClient(speed=0.01), FakeLLM(fail_claims=True)))
        council = next(e["data"] for e in events if e["event"] == "council")
        self.assertEqual(council["method"], "heuristic")
        self.assertIn("fell back", council["note"])
        self.assertTrue(next(e["data"]["markdown"] for e in events if e["event"] == "answer"))

    def test_translation_rewrites_query(self):
        events = run(Panchayat(settings(), MockSearchClient(speed=0.01), FakeLLM()), lang="hi")
        query = next(e["data"] for e in events if e["event"] == "query")
        self.assertTrue(query["rewritten"])
        self.assertEqual(events[0]["data"]["language"], "Hindi")

    def test_failing_panch_does_not_sink_council(self):
        class Flaky(MockSearchClient):
            def search(self, params, use_cache=True):
                if params["engine"] == "bing_copilot":
                    raise SerpApiError("Bing Copilot is down")
                if params["engine"] == "brave_ai_mode":
                    raise SerpApiError("Brave hasn't returned any results for this query.", empty=True)
                return super().search(params, use_cache)

        events = run(Panchayat(settings(), Flaky(speed=0.01), None))
        statuses = {e["data"]["panch"]["id"]: e["data"]["panch"]["status"] for e in events if e["event"] == "panch"}
        self.assertEqual(statuses["copilot"], "error")
        self.assertEqual(statuses["brave"], "abstained")
        self.assertEqual(statuses["ai_mode"], "answered")
        self.assertEqual(events[-1]["event"], "done")

    def test_stale_ai_overview_token_is_refreshed(self):
        google = json.loads((FIX / "google.json").read_text())
        overview = json.loads((FIX / "google_ai_overview.json").read_text())

        class TokenClient:
            searches_made = 0

            def __init__(self):
                self.calls = []

            def search(self, params, use_cache=True):
                self.calls.append((params["engine"], use_cache))
                if params["engine"] == "google":
                    fresh = not use_cache
                    data = json.loads(json.dumps(google))
                    data["ai_overview"]["page_token"] = "fresh" if fresh else "stale"
                    return SearchResponse(data, cached=not fresh)
                if params["engine"] == "google_ai_overview":
                    if params["page_token"] == "stale":
                        raise SerpApiError("Invalid page_token")
                    return SearchResponse(overview, cached=False)
                raise SerpApiError("no", empty=True)

        client = TokenClient()
        events = run(Panchayat(settings(enabled_panches=("ai_overview",)), client, None))
        panch = next(e["data"]["panch"] for e in events if e["event"] == "panch")
        self.assertEqual(panch["status"], "answered")
        self.assertIn(("google", False), client.calls)


class ApiTests(unittest.TestCase):
    """Runs the real Starlette app under uvicorn and talks to it over HTTP."""

    @classmethod
    def setUpClass(cls):
        import uvicorn

        from backend.app import build_app

        app = build_app(panchayat=Panchayat(settings(), MockSearchClient(speed=0.01), FakeLLM()), settings=settings())
        cls.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8799, log_level="error"))
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        for _ in range(50):
            if cls.server.started:
                break
            time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(timeout=5)

    def test_health_and_frontend(self):
        health = requests.get("http://127.0.0.1:8799/api/health", timeout=5).json()
        self.assertTrue(health["mock"])
        self.assertEqual(len(health["panches"]), 5)
        page = requests.get("http://127.0.0.1:8799/", timeout=5)
        self.assertIn("panchayat", page.text)

    def test_ask_streams_events(self):
        resp = requests.post("http://127.0.0.1:8799/api/ask", json={"question": "Great Wall?"}, stream=True, timeout=30)
        self.assertEqual(resp.headers["content-type"].split(";")[0], "text/event-stream")
        events = [line[7:] for line in resp.iter_lines(decode_unicode=True) if line.startswith("event: ")]
        self.assertIn("council", events)
        self.assertEqual(events[-1], "done")

    def test_validation(self):
        self.assertEqual(requests.post("http://127.0.0.1:8799/api/ask", json={"question": " "}, timeout=5).status_code, 400)
        self.assertEqual(requests.post("http://127.0.0.1:8799/api/ask", json={"question": "x" * 600}, timeout=5).status_code, 400)


if __name__ == "__main__":
    unittest.main()
