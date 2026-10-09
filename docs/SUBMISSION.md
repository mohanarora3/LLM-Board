# Submission kit: SerpApi India Hackathon 2026

Everything the submission form asks for, ready to paste, plus a 3-minute demo script and a final checklist.

**Deadline:** October 10, 2026, 23:59 IST. Submit at <https://serpapi.github.io/serpapi-india-hackathon-2026/submit.html>.

---

## Form fields

**Project name**
Panchayat

**Tagline**
Five AI search engines debate. You get one verdict.

**Track**
Knowledge & Public Interest

**Description (what it does, who it helps, how it uses SerpApi)**

> Millions of people now rely on AI-generated answers for health, money and civic decisions, but different AI search engines often give conflicting answers with equal confidence, and most people only ever ask one. Panchayat convenes a "council" of five: Google AI Overview, Google AI Mode, Bing Copilot, Brave AI and the open web (Google results plus forums), all fetched live through SerpApi. An LLM clerk records every claim and each engine's position on it (supports, contradicts or silent). Deterministic code then scores agreement, and a cited verdict is streamed in a Perplexity-style interface. A side column shows each engine's stance, a claim-by-claim vote, and every source with its credibility tier and the engines that cited it, so users see what the AIs agree on, where they disagree, and what each claim rests on. Users can ask follow-ups or re-ask in Hindi (10 Indian languages are supported) to see whether the verdict changes.
>
> It helps anyone who asks AI about things that matter: parents checking medicine advice, farmers checking scheme eligibility, students, journalists and fact-checkers.

**How SerpApi is used, and why it matters**

> SerpApi is the product: each of the five panches is a SerpApi engine. We use `google` (organic results, answer box, knowledge graph, People also ask, and the AI Overview `page_token`), `google_ai_overview`, `google_ai_mode`, `bing_copilot`, `brave_ai_mode` and `google_forums`, localized with `hl`/`gl` and `language`/`country`. We normalize all five answer formats (`text_blocks`, `segments`, `citations`, `references`, `reference_indexes`) into one shape so claims can be compared fairly and traced back to sources. Without SerpApi's unified access to multiple AI answer engines, Panchayat could not exist. Each question costs about 6 searches, with SQLite caching for repeats.

**Did the project exist before the hackathon?**
No. Built during the hackathon. *(Change this if it isn't accurate for you.)*

**AI tools used**
> Claude (Anthropic) helped design the architecture and write the code, tests and documentation, which I reviewed and tested. *(Edit to match exactly how you used AI tools.)*

---

## 3-minute demo script

Record the app running locally (`./run.sh`). Warm the cache first so nothing stalls on camera:

```bash
python scripts/warm_cache.py \
  "Is it safe to give paracetamol and ibuprofen together to a child?" \
  "Who is eligible for PM-KISAN and how much do farmers get?"
```

| Time | Show | Say |
|---|---|---|
| 0:00–0:20 | Home screen | "Different AIs often give different answers to the same question, each one sounding sure. Panchayat asks five of them at once, through SerpApi, and shows where they agree." |
| 0:20–0:55 | Ask a health question. Show the progress box as the five panches arrive one by one | "Google AI Overview, Google AI Mode, Bing Copilot, Brave AI and the open web all answer live. Each is a different SerpApi engine." |
| 0:55–1:30 | The streamed answer with source pills. Hover a pill | "The verdict only uses claims from the ledger, and every sentence is cited. Hovering shows the source, its credibility tier and which AIs cited it." |
| 1:30–2:15 | Council column: ring, panch stances, open the dissenting panch, then claim by claim | "Here's the council. Four of five agree. This panch dissents, and here's exactly which claim and which source it relied on." |
| 2:15–2:40 | Click **हिंदी में पूछें** | "Same question in Hindi. Does the verdict change? Now anyone can see whether non-English users get a different answer." |
| 2:40–3:00 | Ask a follow-up, then show the Library | "Follow-ups keep context, threads are saved, and repeat questions come from cache so they cost nothing." |

Tip: pick demo questions where the engines actually disagree. Run a few candidates through `warm_cache.py` and keep the ones with a visible dissent.

---

## Final checklist

- [ ] `.env` is **not** committed (it's in `.gitignore`). No keys appear anywhere in the repo or the video.
- [ ] `python -m unittest discover -s tests -t .` passes.
- [ ] `python scripts/check_engines.py` shows all engines responding with your key.
- [ ] README screenshot replaced with one from a live run (optional but nice).
- [ ] Public GitHub repo created and pushed; it opens in a private/incognito window.
- [ ] Demo video under 3 minutes, unlisted YouTube or shareable Drive link; it opens in a private window.
- [ ] Form submitted with **"Submit project"** (a saved draft doesn't count) before Oct 10, 23:59 IST.

### Push to GitHub

```bash
git init && git add . && git commit -m "Panchayat: five AI search engines debate, one verdict"
git branch -M main
git remote add origin https://github.com/<you>/panchayat.git
git push -u origin main
```
