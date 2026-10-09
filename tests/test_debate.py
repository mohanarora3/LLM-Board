import asyncio
import dataclasses
import unittest

from backend.config import load_settings
from backend.debate import MARKER, MAX_QUERY_CHARS, challenge_query, heuristic_stance, substance
from backend.mock import MockSearchClient
from backend.pipeline import Panchayat


def settings(**overrides):
    return dataclasses.replace(load_settings(), **{"mock": True, "llm_provider": "none", **overrides})


def events(panchayat, **kw):
    async def collect():
        return [e async for e in panchayat.convene("Is the Great Wall of China visible from space?", **kw)]
    return asyncio.run(collect())


class JudgeLLM:
    """Fake clerk: claims for the council, then a referee verdict for each debate round."""

    def __init__(self):
        self.judged = 0

    def complete_text(self, system, user, max_tokens=0):
        return "unused"

    def complete_json(self, system, user, max_tokens=0):
        if "referee a debate" in system:
            self.judged += 1
            return {"turns": {p: {"stance": "agrees", "summary": f"{p} accepts it."} for p in ("ai_mode", "copilot", "brave")}}
        ids = ["ai_overview", "ai_mode", "copilot", "brave", "web"]
        return {"claims": [{"text": "Not visible from the Moon", "importance": 3,
                            "positions": {p: "supports" for p in ids}, "cites": {}}]}

    def stream(self, system, user, max_tokens=0):
        self.verdict_prompt = user
        yield "They agree it is not visible from the Moon [1]."


class StanceRules(unittest.TestCase):
    def test_disagreement(self):
        self.assertEqual(heuristic_stance("That is not accurate. The wall cannot be seen."), "disagrees")
        self.assertEqual(heuristic_stance("No, it can't be seen from orbit."), "disagrees")

    def test_partial(self):
        self.assertEqual(heuristic_stance("Mostly accurate, but it needs one correction."), "partly")
        self.assertEqual(heuristic_stance("Yes, but only with a zoom lens."), "partly")

    def test_agreement(self):
        self.assertEqual(heuristic_stance("Yes, that is correct. It is not visible."), "agrees")

    def test_filler_is_stripped(self):
        self.assertEqual(substance("Yes, I agree with that. It is not visible from the Moon at all."),
                         "It is not visible from the Moon at all.")
        self.assertEqual(substance("It is a myth that you can see it."), "It is a myth that you can see it.")


class ChallengeQuery(unittest.TestCase):
    def test_query_fits_and_is_marked(self):
        q = challenge_query("Is the wall visible?", "word " * 300, 1)
        self.assertLessEqual(len(q), MAX_QUERY_CHARS + 5)
        self.assertIn(MARKER, q)
        self.assertIn("now says", challenge_query("Q?", "claim", 2))


class DebateInPipeline(unittest.TestCase):
    def test_sample_debate_reaches_consensus(self):
        out = events(Panchayat(settings(), MockSearchClient(speed=0.01), None))
        kinds = [e["event"] for e in out]
        for kind in ("debate_start", "debate_turn", "debate_round", "debate_end", "media"):
            self.assertIn(kind, kinds)
        self.assertLess(kinds.index("debate_end"), kinds.index("answer"))
        end = next(e["data"] for e in out if e["event"] == "debate_end")
        self.assertTrue(end["consensus"])
        self.assertEqual(end["rounds_used"], 2)
        rounds = [e["data"] for e in out if e["event"] == "debate_round" and e["data"]["status"] == "done"]
        self.assertFalse(rounds[0]["consensus"])  # round 1 has a real disagreement
        answer = next(e["data"]["markdown"] for e in out if e["event"] == "answer")
        self.assertIn("After the debate", answer)
        done = next(e["data"] for e in out if e["event"] == "done")
        self.assertEqual(done["searches"], 6 + 6)  # 6 to convene, 3 debaters x 2 rounds

    def test_debate_can_be_turned_off(self):
        out = events(Panchayat(settings(), MockSearchClient(speed=0.01), None), debate=False)
        self.assertNotIn("debate_start", [e["event"] for e in out])
        out = events(Panchayat(settings(debate_rounds=0), MockSearchClient(speed=0.01), None))
        self.assertNotIn("debate_start", [e["event"] for e in out])

    def test_one_round_limit(self):
        out = events(Panchayat(settings(debate_rounds=1), MockSearchClient(speed=0.01), None))
        end = next(e["data"] for e in out if e["event"] == "debate_end")
        self.assertEqual(end["rounds_used"], 1)
        self.assertFalse(end["consensus"])
        self.assertIn("copilot", end["holdouts"])

    def test_llm_judges_and_verdict_sees_transcript(self):
        llm = JudgeLLM()
        out = events(Panchayat(settings(llm_provider="gemini", llm_api_key="x"), MockSearchClient(speed=0.01), llm))
        end = next(e["data"] for e in out if e["event"] == "debate_end")
        self.assertTrue(end["consensus"])
        self.assertEqual(end["rounds_used"], 1)  # the referee said everyone agreed in round 1
        self.assertEqual(llm.judged, 1)
        self.assertIn("Debate transcript", llm.verdict_prompt)

    def test_too_few_debaters_skips(self):
        out = events(Panchayat(settings(enabled_panches=("ai_overview", "ai_mode", "web")), MockSearchClient(speed=0.01), None))
        self.assertIn("debate_skip", [e["event"] for e in out])


if __name__ == "__main__":
    unittest.main()
