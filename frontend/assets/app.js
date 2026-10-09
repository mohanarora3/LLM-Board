import { api } from "./api.js";
import { hydrateIcons, icon, setIcon } from "./icons.js";
import { escapeHtml as esc, renderMarkdown } from "./markdown.js";
import { store } from "./store.js";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const uid = () => Math.random().toString(36).slice(2, 10) + Date.now().toString(36).slice(-4);

const FALLBACK_PANCHES = [
  { id: "ai_overview", name: "Google AI Overview", short: "AI Overview", initials: "AO", engines: "google + google_ai_overview" },
  { id: "ai_mode", name: "Google AI Mode", short: "AI Mode", initials: "AM", engines: "google_ai_mode" },
  { id: "copilot", name: "Bing Copilot", short: "Copilot", initials: "BC", engines: "bing_copilot" },
  { id: "brave", name: "Brave AI", short: "Brave AI", initials: "BR", engines: "brave_ai_mode" },
  { id: "web", name: "Open web", short: "Open web", initials: "WEB", engines: "google + google_forums" },
];
const LANG_NATIVE = { en: "English", hi: "हिंदी", bn: "বাংলা", ta: "தமிழ்", te: "తెలుగు", mr: "मराठी", kn: "ಕನ್ನಡ", ml: "മലയാളം", gu: "ગુજરાતી", pa: "ਪੰਜਾਬੀ" };
const STANCE_LABEL = { agrees: "Agrees", partly: "Partly agrees", dissents: "Dissents", unclear: "Adds detail", abstained: "Abstained", error: "Unavailable", waiting: "Thinking…", answered: "Answered" };
const TIER_ORDER = ["official", "reference", "news", "web", "community"];

const state = {
  health: null,
  panches: FALLBACK_PANCHES,
  thread: null,
  focusedTurn: null,
  streaming: null,
  openPanches: new Set(),
  openTurns: new Set(),
  sourceFilter: "all",
  mode: "debate",
  tab: "answer",
  detailsOpen: false,
};
const STANCE_WORD = { agrees: "Agrees", partly: "Partly agrees", disagrees: "Disagrees", silent: "No reply" };

// ------------------------------------------------------------------ helpers

const panchMeta = (id, turn) => (turn?.order || state.panches).find((p) => p.id === id) || { id, name: id, initials: "?" };
const avatar = (p, size = "") => `<span class="av ${size}" data-p="${esc(p.id)}" title="${esc(p.name)}">${esc(p.initials)}</span>`;

function shortSite(domain = "") {
  const parts = domain.split(".").filter(Boolean);
  if (parts.length >= 3 && ["co", "gov", "ac", "org", "net", "com", "nic", "edu", "res"].includes(parts.at(-2)) && parts.at(-1).length === 2) {
    return parts.at(-3);
  }
  return parts.length >= 2 ? parts.at(-2) : parts[0] || "source";
}

const favicon = (domain) => `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=32`;
const findTurn = (id) => state.thread?.turns.find((t) => t.id === id);
const sourceById = (turn, id) => (turn.sources || []).find((s) => s.id === id);

function autosize(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = Math.min(textarea.scrollHeight, 220) + "px";
}

function formatSecs(ms) {
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`;
}

// ------------------------------------------------------------------ boot

async function boot() {
  hydrateIcons();
  $("#modKey").textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘" : "Ctrl";
  if (store.pref("sidebar") === "collapsed") $("#app").classList.add("collapsed");
  state.mode = store.pref("mode") === "council" ? "council" : "debate";
  syncModeUI();
  syncThemeIcon();
  bindChrome();

  try {
    state.health = await api.health();
    if (state.health.panches?.length) state.panches = state.health.panches;
  } catch (_) {
    state.health = null;
  }
  renderStatus();
  fillLanguageSelects();
  renderCouncilChip();
  renderSetup();
  loadSuggestions();
  renderRecent();
  window.addEventListener("hashchange", route);
  route();
}

function bindChrome() {
  $("#newThread").addEventListener("click", newThread);
  $("#collapseSidebar").addEventListener("click", () => {
    const collapsed = $("#app").classList.toggle("collapsed");
    store.pref("sidebar", collapsed ? "collapsed" : "open");
  });
  $("#themeToggle").addEventListener("click", toggleTheme);
  $("#themeToggleTop").addEventListener("click", toggleTheme);
  $("#sessionsToggle").addEventListener("click", () => {
    const open = $("#sessionsToggle").getAttribute("aria-expanded") !== "true";
    $("#sessionsToggle").setAttribute("aria-expanded", String(open));
    $("#recentThreads").hidden = !open;
  });
  $$(".mode-switch .mode, .mode-card").forEach((b) =>
    b.addEventListener("click", () => {
      setMode(b.dataset.mode);
      if (b.classList.contains("mode-card")) $("#homeInput").focus();
    }),
  );
  $$(".tab").forEach((t) => t.addEventListener("click", () => setTab(t.dataset.tab)));
  bindMic();
  $("#mobileMenu").addEventListener("click", () => openOverlay("menu"));
  $("#scrim").addEventListener("click", closeOverlays);
  $("#councilToggle").addEventListener("click", () => openOverlay("council"));
  $("#councilClose").addEventListener("click", closeOverlays);
  $("#copyThread").addEventListener("click", copyThread);
  $("#librarySearch").addEventListener("input", renderLibrary);

  for (const [form, input, lang] of [["#homeForm", "#homeInput", "#homeLang"], ["#followForm", "#followInput", "#followLang"]]) {
    const ta = $(input);
    ta.addEventListener("input", () => autosize(ta));
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        $(form).requestSubmit();
      }
    });
    $(form).addEventListener("submit", (e) => {
      e.preventDefault();
      if (state.streaming && form === "#followForm") return stopStreaming();
      const q = ta.value.trim();
      if (!q || state.streaming) return;
      ta.value = "";
      autosize(ta);
      const chosen = $(lang).value;
      store.pref("lang", chosen);
      if (form === "#homeForm") startThread(q, chosen);
      else ask(q, chosen);
    });
  }

  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      newThread();
    }
    if (e.key === "Escape") closeOverlays();
  });

  // Citation hover cards + highlighting the source in the council column.
  document.addEventListener("mouseover", (e) => {
    const cite = e.target.closest(".cite");
    if (cite) showCiteTip(cite);
  });
  document.addEventListener("mouseout", (e) => {
    const cite = e.target.closest(".cite");
    if (cite && !cite.contains(e.relatedTarget)) hideCiteTip();
  });

  $("#turns").addEventListener("click", (e) => {
    const turnEl = e.target.closest(".turn");
    if (!turnEl) return;
    const action = e.target.closest("[data-action]");
    const turn = findTurn(turnEl.dataset.turn);
    if (action && turn) return handleTurnAction(action, turn);
    if (!e.target.closest("a, button") && turn) focusTurn(turn.id);
  });

  $("#councilContent").addEventListener("click", (e) => {
    const more = e.target.closest("[data-dturn]");
    if (more) {
      const key = more.dataset.dturn;
      state.openTurns.has(key) ? state.openTurns.delete(key) : state.openTurns.add(key);
      renderCouncil();
      return;
    }
    if (e.target.closest("[data-details]")) {
      state.detailsOpen = !state.detailsOpen;
      renderCouncil();
      return;
    }
    const row = e.target.closest(".panch-row");
    if (row) {
      const id = row.dataset.panch;
      state.openPanches.has(id) ? state.openPanches.delete(id) : state.openPanches.add(id);
      row.parentElement.classList.toggle("open");
      row.setAttribute("aria-expanded", row.parentElement.classList.contains("open"));
      return;
    }
    const filter = e.target.closest(".filter");
    if (filter) {
      state.sourceFilter = filter.dataset.tier;
      renderCouncil();
    }
  });
}

// ------------------------------------------------------------------ chrome: status, theme, overlays

function renderStatus() {
  const h = state.health;
  const box = $("#statusBox");
  if (!h) {
    box.innerHTML = `<div class="row"><span class="dot bad"></span>Server unreachable</div>`;
    return;
  }
  const serp = h.mock
    ? `<span class="dot warn"></span>SerpApi: sample mode`
    : h.serpapi
      ? `<span class="dot ok"></span>SerpApi: live`
      : `<span class="dot bad"></span>SerpApi: key missing`;
  const llm = h.llm !== "none" ? `<span class="dot ok"></span>Clerk: ${esc(h.llm)}` : `<span class="dot warn"></span>Clerk: no LLM`;
  box.innerHTML = `<div class="row">${serp}</div><div class="row" title="${esc(h.llm_model || "Sentence matching")}">${llm}</div>
    <div class="row">${h.searches_this_session} searches · ${h.cache_entries} cached</div>`;
  $("#mockBanner").hidden = !h.mock;
}

async function refreshHealth() {
  try {
    state.health = await api.health();
    renderStatus();
  } catch (_) {
    /* keep last known */
  }
}

function syncThemeIcon() {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  setIcon($("#themeToggle"), dark ? "sun" : "moon");
  setIcon($("#themeToggleTop"), dark ? "sun" : "moon");
}

function toggleTheme() {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  const next = dark ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  store.pref("theme", next);
  syncThemeIcon();
}

function openOverlay(which) {
  if (which === "menu") $("#app").classList.add("menu-open");
  if (which === "council") {
    $("#councilPanel").classList.add("open");
    $("#councilToggle").setAttribute("aria-expanded", "true");
  }
  $("#scrim").hidden = false;
}

function closeOverlays() {
  $("#app").classList.remove("menu-open");
  $("#councilPanel").classList.remove("open");
  $("#councilToggle").setAttribute("aria-expanded", "false");
  $("#scrim").hidden = true;
}

function fillLanguageSelects() {
  const langs = state.health?.languages || { en: "English", hi: "Hindi" };
  const saved = store.pref("lang") || "auto";
  const options =
    `<option value="auto">Auto</option>` +
    Object.entries(langs)
      .map(([code, name]) => `<option value="${code}" title="${esc(name)}">${esc(LANG_NATIVE[code] || name)}</option>`)
      .join("");
  for (const sel of [$("#homeLang"), $("#followLang")]) {
    sel.innerHTML = options;
    sel.value = [...sel.options].some((o) => o.value === saved) ? saved : "auto";
    sel.addEventListener("change", () => {
      store.pref("lang", sel.value);
      $("#homeLang").value = $("#followLang").value = sel.value;
    });
  }
}

function renderCouncilChip() {
  $("#councilChip").innerHTML = `<span class="avatars">${state.panches.map((p) => avatar(p)).join("")}</span>
    <span class="label-text">${state.panches.length} panches</span>`;
}

function renderSetup() {
  const card = $("#setupCard");
  const h = state.health;
  if (!h) {
    card.hidden = false;
    card.innerHTML = `<h3>Can't reach the Panchayat server</h3><p>Start it with <code>python -m backend</code> and reload this page.</p>`;
    return;
  }
  if (h.serpapi) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  card.innerHTML = `<h3>Add your SerpApi key to convene the council</h3>
    <ol><li>Get a free key at serpapi.com (250 searches a month).</li>
    <li>Copy <code>.env.example</code> to <code>.env</code> and set <code>SERPAPI_API_KEY</code>.</li>
    <li>Optional: add <code>GEMINI_API_KEY</code> (free) so the clerk can detect contradictions.</li>
    <li>Restart the server. To try the interface first, run with <code>PANCHAYAT_MOCK=1</code>.</li></ol>`;
}

async function loadSuggestions() {
  try {
    const { suggestions } = await api.suggestions();
    $("#suggestions").innerHTML = suggestions.map((s) => `<button class="suggestion" type="button">${esc(s)}</button>`).join("");
    $$("#suggestions .suggestion").forEach((b) => b.addEventListener("click", () => startThread(b.textContent, $("#homeLang").value)));
  } catch (_) {
    /* optional */
  }
}

// ------------------------------------------------------------------ mode, tabs, voice

function setMode(mode) {
  state.mode = mode === "council" ? "council" : "debate";
  store.pref("mode", state.mode);
  syncModeUI();
}

function syncModeUI() {
  $$(".mode-switch .mode").forEach((b) => {
    const on = b.dataset.mode === state.mode;
    b.classList.toggle("on", on);
    b.setAttribute("aria-checked", String(on));
  });
  $$(".mode-card").forEach((c) => c.classList.toggle("on", c.dataset.mode === state.mode));
}

function setTab(tab) {
  state.tab = tab;
  $$(".tab").forEach((t) => {
    const on = t.dataset.tab === tab;
    t.classList.toggle("on", on);
    t.setAttribute("aria-selected", String(on));
  });
  $("#turns").hidden = tab !== "answer";
  $("#linksPane").hidden = tab !== "links";
  $("#imagesPane").hidden = tab !== "images";
  renderPanes();
  $("#threadScroll").scrollTop = 0;
}

function focusedTurnObj() {
  return findTurn(state.focusedTurn) || state.thread?.turns.at(-1) || null;
}

function renderPanes() {
  const turn = focusedTurnObj();
  const sources = turn?.sources || [];
  const images = turn?.images || [];
  $("#linksCount").textContent = sources.length ? String(sources.length) : "";
  $("#imagesCount").textContent = images.length ? String(images.length) : "";
  if (state.tab === "links") {
    $("#linksPane").innerHTML = sources.length
      ? `<div class="pane-head">${turn ? esc(turn.question) : ""}</div><ol class="links">${sources
          .map((s) => {
            const by = s.cited_by.map((id) => avatar(panchMeta(id, turn))).join("");
            return `<li class="link"><a href="${esc(s.link)}" target="_blank" rel="noopener noreferrer">
              <span class="l-site"><img src="${favicon(s.domain)}" alt="" loading="lazy" onerror="this.remove()"/>${esc(s.domain)}<span class="tier ${s.tier}">${esc(s.tier_label)}</span></span>
              <span class="l-title">${esc(s.title)}</span>
              ${s.snippet ? `<span class="l-snip">${esc(s.snippet)}</span>` : ""}
              <span class="l-by"><span class="avatars">${by}</span>cited by ${s.cited_by.length} panch${s.cited_by.length === 1 ? "" : "es"}</span></a></li>`;
          })
          .join("")}</ol>`
      : `<div class="c-empty">${turn?.status === "loading" ? "Links appear as the panches answer…" : "No links for this answer."}</div>`;
  }
  if (state.tab === "images") {
    $("#imagesPane").innerHTML = images.length
      ? `<div class="pane-head">${turn ? esc(turn.question) : ""}</div><div class="images">${images
          .map(
            (im) => `<a class="img" href="${esc(im.link || im.original)}" target="_blank" rel="noopener noreferrer" title="${esc(im.title)}">
              <img src="${esc(im.thumbnail)}" alt="${esc(im.title)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.closest('.img').remove()"/>
              <span class="img-cap">${esc(im.title || im.domain)}</span></a>`,
          )
          .join("")}</div>`
      : `<div class="c-empty">${turn?.status === "loading" ? "Looking for images…" : "No images for this question."}</div>`;
  }
}

function bindMic() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  $$(".mic").forEach((btn) => {
    if (!Recognition) {
      btn.hidden = true;
      return;
    }
    btn.addEventListener("click", () => {
      const input = $("#" + btn.dataset.mic);
      const rec = new Recognition();
      const lang = (btn.closest("form").querySelector("select")?.value || "auto");
      rec.lang = lang === "auto" ? navigator.language || "en-IN" : `${lang}-IN`;
      rec.interimResults = true;
      btn.classList.add("live");
      rec.onresult = (e) => {
        input.value = [...e.results].map((r) => r[0].transcript).join("");
        autosize(input);
      };
      rec.onend = () => btn.classList.remove("live");
      rec.onerror = () => btn.classList.remove("live");
      rec.start();
    });
  });
}

// ------------------------------------------------------------------ routing & library

function showView(name) {
  $("#homeView").hidden = name !== "home";
  $("#threadView").hidden = name !== "thread";
  $("#libraryView").hidden = name !== "library";
  $$(".sb-link").forEach((a) => a.classList.toggle("active", a.dataset.route === name));
  closeOverlays();
}

function route() {
  const hash = location.hash || "#/";
  const match = hash.match(/^#\/t\/([\w-]+)/);
  if (match) {
    const id = match[1];
    if (state.thread?.id !== id) {
      const saved = store.get(id);
      if (!saved) return (location.hash = "#/");
      saved.turns.forEach((t) => {
        if (t.status === "loading") t.status = "stopped";
      });
      state.thread = saved;
      state.focusedTurn = saved.turns.at(-1)?.id || null;
      renderThread();
    }
    showView("thread");
  } else if (hash.startsWith("#/library")) {
    renderLibrary();
    showView("library");
  } else {
    showView("home");
    setTimeout(() => $("#homeInput").focus(), 30);
  }
  renderRecent();
}

function newThread() {
  if (state.streaming) stopStreaming();
  location.hash = "#/";
  $("#homeInput").value = "";
  $("#homeInput").focus();
}

function renderRecent() {
  const list = store.list().slice(0, 18);
  $("#recentThreads").innerHTML = list
    .map((t) => `<li><a href="#/t/${t.id}" class="${t.id === state.thread?.id && !$("#threadView").hidden ? "active" : ""}" title="${esc(t.title)}">${esc(t.title)}</a></li>`)
    .join("");
}

function renderLibrary() {
  const query = $("#librarySearch").value.trim().toLowerCase();
  const items = store.list().filter((t) => !query || t.title.toLowerCase().includes(query) || t.turns.some((x) => x.question.toLowerCase().includes(query)));
  $("#libraryList").innerHTML = items.length
    ? items
        .map((t) => {
          const last = t.turns.at(-1);
          const preview = (last?.answer || "").replace(/\[\d+\]/g, "").replace(/[#*_>`|-]/g, "").slice(0, 220);
          const when = new Date(t.updated || t.created).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
          return `<li><a href="#/t/${t.id}"><span class="lt">${esc(t.title)}</span><span class="lp">${esc(preview)}</span>
            <span class="lm">${t.turns.length} question${t.turns.length === 1 ? "" : "s"} · ${esc(when)}</span></a>
            <button class="icon-btn" data-del="${t.id}" aria-label="Delete thread">${icon("trash")}</button></li>`;
        })
        .join("")
    : `<li class="c-empty">${query ? "No threads match." : "Your threads will appear here."}</li>`;
  $$("#libraryList [data-del]").forEach((b) =>
    b.addEventListener("click", () => {
      store.remove(b.dataset.del);
      if (state.thread?.id === b.dataset.del) state.thread = null;
      renderLibrary();
      renderRecent();
    }),
  );
}

// ------------------------------------------------------------------ asking

function startThread(question, lang) {
  if (state.streaming) return;
  setTab("answer");
  state.thread = { id: uid(), title: question.slice(0, 120), created: Date.now(), turns: [] };
  state.openPanches.clear();
  renderThread();
  location.hash = `#/t/${state.thread.id}`;
  showView("thread");
  ask(question, lang);
}

function setStreamingUI(on) {
  const btn = $("#followSend");
  btn.classList.toggle("stop", on);
  btn.setAttribute("aria-label", on ? "Stop" : "Send");
  setIcon(btn, on ? "stop" : "arrow-up");
  $("#followInput").placeholder = on ? "The council is deliberating…" : "Ask a follow-up";
}

function stopStreaming() {
  state.streaming?.controller.abort();
}

async function ask(question, lang = "auto") {
  if (state.streaming || !state.thread) return;
  const thread = state.thread;
  const history = thread.turns.filter((t) => t.status === "done").slice(-3).map((t) => ({ question: t.question, answer: t.answer.slice(0, 1500) }));
  const turn = {
    id: uid(), question, lang, status: "loading", order: state.panches, panches: {}, sources: [], council: null,
    method: "", note: "", answer: "", related: [], stats: null, query: "", step: "", error: "",
    images: [], mode: state.mode, debate: null,
  };
  thread.turns.push(turn);
  $("#turns").insertAdjacentHTML("beforeend", turnShell(turn));
  focusTurn(turn.id);
  renderTurn(turn);
  document.title = (thread.title) ? `${thread.title} · Panchayat` : "Panchayat";
  requestAnimationFrame(() => $(`#turn-${turn.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }));

  const controller = new AbortController();
  state.streaming = { controller, turnId: turn.id };
  setStreamingUI(true);

  try {
    await api.ask({ question, lang, history, debate: turn.mode === "debate" }, (event, data) => onEvent(turn, event, data), controller.signal);
    if (turn.status === "loading") turn.status = turn.answer ? "done" : "error";
    if (turn.status === "error" && !turn.error) turn.error = "The council ended without a verdict.";
  } catch (err) {
    if (err.name === "AbortError") {
      turn.status = "stopped";
    } else {
      turn.status = "error";
      turn.error = err.message || "Something went wrong.";
    }
  } finally {
    state.streaming = null;
    setStreamingUI(false);
    renderTurn(turn);
    if (state.focusedTurn === turn.id) renderCouncil();
    store.save(thread);
    renderRecent();
    refreshHealth();
  }
}

let answerFrame = null;
function onEvent(turn, event, data) {
  switch (event) {
    case "start":
      turn.order = data.panches;
      turn.lang = data.lang;
      turn.llm = data.llm;
      break;
    case "query":
      turn.query = data.query;
      turn.rewritten = data.rewritten;
      break;
    case "panch":
      turn.panches[data.panch.id] = data.panch;
      break;
    case "step":
      turn.step = data.text;
      break;
    case "sources":
      turn.sources = data.sources;
      renderPanes();
      break;
    case "media":
      turn.images = data.images || [];
      renderPanes();
      break;
    case "debate_start":
      turn.debate = { debaters: data.debaters, rounds: data.rounds, openings: data.openings, turns: {}, done: {}, current: 0, end: null };
      break;
    case "debate_round":
      if (!turn.debate) break;
      if (data.status === "started") turn.debate.current = data.round;
      else turn.debate.done[data.round] = data;
      break;
    case "debate_turn":
      if (!turn.debate) break;
      turn.debate.turns[`${data.turn.round}:${data.turn.speaker}`] = data.turn;
      break;
    case "debate_end":
      if (turn.debate) turn.debate.end = data;
      break;
    case "debate_skip":
      turn.debate = { skipped: data.reason };
      break;
    case "council":
      turn.council = data.council;
      turn.method = data.method;
      turn.note = data.note || "";
      break;
    case "token":
      turn.answer += data.text;
      if (!answerFrame) {
        answerFrame = requestAnimationFrame(() => {
          answerFrame = null;
          renderAnswer(turn, true);
          followScroll();
        });
      }
      return;
    case "answer":
      turn.answer = data.markdown;
      break;
    case "related":
      turn.related = data.questions || [];
      break;
    case "done":
      turn.stats = data;
      turn.status = "done";
      break;
    case "error":
      turn.status = "error";
      turn.error = data.message;
      break;
  }
  renderTurn(turn);
  if (state.focusedTurn === turn.id && ["start", "panch", "sources", "council", "done", "debate_start", "debate_round", "debate_turn", "debate_end", "debate_skip", "step"].includes(event)) renderCouncil();
}

function followScroll() {
  const scroller = $("#threadScroll");
  if (scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 260) scroller.scrollTop = scroller.scrollHeight;
}

function handleTurnAction(button, turn) {
  const action = button.dataset.action;
  if (action === "copy") {
    navigator.clipboard?.writeText(answerAsText(turn)).then(() => flash(button));
  } else if (action === "lang") {
    ask(turn.question, button.dataset.lang);
  } else if (action === "retry") {
    ask(turn.question, turn.lang || "auto");
  } else if (action === "related") {
    ask(button.dataset.q, $("#followLang").value);
  } else if (action === "council") {
    focusTurn(turn.id);
    openOverlay("council");
  } else if (action === "progress") {
    button.closest(".progress").classList.toggle("collapsed");
  }
}

function flash(button) {
  const label = button.querySelector("span:last-child");
  const old = label?.textContent;
  if (label) label.textContent = "Copied";
  setTimeout(() => label && (label.textContent = old), 1400);
}

function answerAsText(turn) {
  const used = new Set([...turn.answer.matchAll(/\[(\d{1,2})\]/g)].map((m) => Number(m[1])));
  const refs = (turn.sources || []).filter((s) => used.has(s.id)).map((s) => `[${s.id}] ${s.title} — ${s.link}`);
  const head = turn.council ? `Panchayat verdict: ${turn.council.headline} (${turn.council.ruling})\n\n` : "";
  return `${turn.question}\n\n${head}${turn.answer}${refs.length ? "\n\nSources:\n" + refs.join("\n") : ""}`;
}

function copyThread() {
  if (!state.thread) return;
  const text = state.thread.turns.map(answerAsText).join("\n\n---\n\n");
  navigator.clipboard?.writeText(text).then(() => flash($("#copyThread")));
}

// ------------------------------------------------------------------ thread rendering

function renderThread() {
  const thread = state.thread;
  document.title = (thread?.title || "") ? `${thread?.title || ""} · Panchayat` : "Panchayat";
  $("#turns").innerHTML = (thread?.turns || []).map(turnShell).join("");
  (thread?.turns || []).forEach(renderTurn);
  focusTurn(state.focusedTurn || thread?.turns.at(-1)?.id);
}

function turnShell(turn) {
  return `<article class="turn" id="turn-${turn.id}" data-turn="${turn.id}">
    <div class="q-row"><h1 class="q">${esc(turn.question)}</h1></div>
    <div data-slot="progress"></div>
    <div data-slot="error"></div>
    <div class="section-label" data-slot="label">${icon("scale")}<span>Answer</span><span data-slot="ruling"></span></div>
    <div class="note" data-slot="note" hidden></div>
    <div class="prose answer" data-slot="answer"></div>
    <div class="turn-actions" data-slot="actions"></div>
    <div class="related" data-slot="related" hidden></div>
  </article>`;
}

function slot(turn, name) {
  return $(`#turn-${turn.id} [data-slot="${name}"]`);
}

function renderTurn(turn) {
  if (!$(`#turn-${turn.id}`)) return;
  renderProgress(turn);
  renderRuling(turn);
  renderAnswer(turn, turn.status === "loading");
  renderActions(turn);
  renderRelated(turn);

  const note = slot(turn, "note");
  note.hidden = !turn.note;
  note.innerHTML = turn.note ? `${icon("info")}<span>${esc(turn.note)}</span>` : "";

  const err = slot(turn, "error");
  err.innerHTML = turn.status === "error" ? `<div class="error-box">${esc(turn.error || "Something went wrong.")}</div>` : "";
  slot(turn, "label").hidden = turn.status === "error" && !turn.answer;
}

function renderProgress(turn) {
  const box = slot(turn, "progress");
  const order = turn.order || state.panches;
  const arrived = Object.values(turn.panches);
  const answered = arrived.filter((p) => p.status === "answered").length;
  const loading = turn.status === "loading";
  let headText;
  if (loading) {
    headText = turn.step || (arrived.length < order.length ? `Convening the panchayat · ${arrived.length} of ${order.length} have spoken` : "Weighing the answers");
    if (turn.debate?.current && !turn.debate.end) headText = `Debating · round ${turn.debate.current} of ${turn.debate.rounds}`;
  } else {
    const s = turn.stats;
    const d = turn.debate?.end;
    const verb = d ? `Debated ${d.rounds_used} round${d.rounds_used === 1 ? "" : "s"}${d.consensus ? " · consensus" : ""}` : "Researched";
    headText = `${verb} · ${answered} of ${order.length} panches` + (s ? ` · ${s.searches} SerpApi search${s.searches === 1 ? "" : "es"} · ${formatSecs(s.elapsed_ms)}` : "");
  }

  const rows = order
    .map((meta) => {
      const p = turn.panches[meta.id];
      let status;
      if (!p) status = loading ? `<span class="spin"></span>` : `<span class="stance abstained">Stopped</span>`;
      else if (p.status === "answered")
        status = `<span class="meta">${formatSecs(p.latency_ms)} · ${p.references.length} source${p.references.length === 1 ? "" : "s"}${p.cached ? " · cached" : ""}</span>${icon("check")}`;
      else status = `<span class="meta" title="${esc(p.note)}">${p.status === "error" ? "Unavailable" : "Abstained"}</span>`;
      return `<div class="p-row">${avatar(meta)}<span class="name">${esc(meta.name)}</span>${status.includes("spin") ? `<span class="meta">Thinking…</span>${status}` : status}</div>`;
    })
    .join("");
  const query = turn.query && turn.rewritten ? `<div class="p-query">Searched for: “${esc(turn.query)}”</div>` : "";
  const collapsed = box.firstElementChild?.classList.contains("collapsed");
  const shouldCollapse = !loading && (collapsed || !box.dataset.touched);
  box.innerHTML = `<div class="progress ${shouldCollapse ? "collapsed" : ""}">
    <button class="progress-head" data-action="progress" type="button">${loading ? `<span class="spin"></span>` : icon("list-tree")}
      <span>${esc(headText)}</span>${icon("chevron-down", "chev")}</button>
    <div class="progress-body">${query}${rows}</div></div>`;
  if (!loading) box.dataset.touched = "1";
}

function renderRuling(turn) {
  const host = slot(turn, "ruling");
  const c = turn.council;
  if (!c || !c.answering) {
    host.innerHTML = "";
    return;
  }
  host.innerHTML = `<button class="ruling" data-action="council" type="button" title="See who agreed">
    <span class="meter-mini"><i style="width:${c.consensus}%"></i></span>${esc(c.headline)} · ${esc(c.ruling)}</button>`;
}

function citeHtml(turn, nums) {
  const found = nums.map((n) => sourceById(turn, n)).filter(Boolean);
  if (!found.length) return "";
  const first = found[0];
  const more = found.length > 1 ? ` +${found.length - 1}` : "";
  return `<a class="cite" href="${esc(first.link)}" target="_blank" rel="noopener noreferrer" data-turn="${turn.id}" data-src="${found.map((s) => s.id).join(",")}">${esc(shortSite(first.domain))}${more}</a>`;
}

function renderAnswer(turn, streaming) {
  const box = slot(turn, "answer");
  if (!box) return;
  let md = turn.answer || "";
  if (streaming) md = md.replace(/\[\d{0,2}$/, "");
  if (!md && streaming) {
    box.innerHTML = turn.council ? `<p><span class="caret"></span></p>` : "";
    return;
  }
  let html = renderMarkdown(md, { cite: (nums) => citeHtml(turn, nums) });
  if (streaming && md) html = html.replace(/(<\/(p|li|td|h3|h4)>)(?!.*<\/(p|li|td|h3|h4)>)/s, ` <span class="caret"></span>$1`);
  if (turn.status === "stopped" && md) html += `<p class="note">Stopped.</p>`;
  box.innerHTML = html;
}

function renderActions(turn) {
  const box = slot(turn, "actions");
  if (turn.status === "loading" || !turn.answer) {
    box.innerHTML = turn.status === "error" ? `<button class="ghost-btn" data-action="retry">${icon("refresh")}<span>Try again</span></button>` : "";
    return;
  }
  const other = turn.lang === "hi" ? { code: "en", label: "Ask in English" } : { code: "hi", label: "हिंदी में पूछें" };
  const s = turn.stats;
  box.innerHTML = `<button class="ghost-btn" data-action="copy">${icon("copy")}<span>Copy</span></button>
    <button class="ghost-btn" data-action="lang" data-lang="${other.code}">${icon("languages")}<span>${other.label}</span></button>
    <button class="ghost-btn" data-action="retry">${icon("refresh")}<span>Ask again</span></button>
    <button class="ghost-btn debate-toggle" data-action="council">${icon("messages")}<span>Debate</span></button>
    ${s ? `<span class="stats">${s.cached_panches ? `${s.cached_panches} from cache · ` : ""}${s.searches} searches · ${formatSecs(s.elapsed_ms)}</span>` : ""}`;
}

function renderRelated(turn) {
  const box = slot(turn, "related");
  const list = turn.status === "done" ? turn.related || [] : [];
  box.hidden = !list.length;
  box.innerHTML = list.length
    ? `<div class="section-label">${icon("list-tree")}<span>Related</span></div><ul>${list
        .map((q) => `<li><button data-action="related" data-q="${esc(q)}"><span>${esc(q)}</span>${icon("plus")}</button></li>`)
        .join("")}</ul>`
    : "";
}

function focusTurn(id) {
  if (!id) {
    state.focusedTurn = null;
    renderCouncil();
    return;
  }
  state.focusedTurn = id;
  $$(".turn").forEach((el) => el.classList.toggle("focused", el.dataset.turn === id));
  renderCouncil();
  renderPanes();
}

// ------------------------------------------------------------------ council column

function renderCouncil() {
  const host = $("#councilContent");
  const turn = findTurn(state.focusedTurn);
  if (!turn) {
    host.innerHTML = `<div class="c-empty">Ask a question to convene the council.<br/>The panches' debate will appear here.</div>`;
    return;
  }
  const c = turn.council;
  const order = turn.order || state.panches;
  const details = [verdictCard(turn, c), panchSection(turn, c, order), claimsSection(turn, c, order)].join("");
  const hasDebate = turn.debate && !turn.debate.skipped;
  host.innerHTML = debateSection(turn) + (hasDebate
    ? `<section class="c-section details ${state.detailsOpen ? "open" : ""}"><button class="details-head" data-details type="button">
        ${icon("scale")}<span>Council details</span><span class="count">${c ? esc(c.headline) : ""}</span>${icon("chevron-down", "chev")}</button>
        <div class="details-body">${details}</div></section>`
    : details);
  const live = host.querySelector(".typing:last-of-type, .d-end");
  if (turn.status === "loading" && live) live.scrollIntoView({ block: "nearest" });
}

const STANCE_ICON = { agrees: "✓", partly: "~", disagrees: "✕", silent: "…" };

function debateSection(turn) {
  const d = turn.debate;
  if (!d) {
    if (turn.mode === "council") return `<div class="d-off">${icon("info")}<span>Council mode: the panches answered independently, without debating. Switch to <b>Debate</b> to make them cross-examine each other.</span></div>`;
    if (turn.status !== "loading") return "";
    return `<section class="debate"><div class="d-head">${icon("messages")}<span class="d-title">The debate</span><span class="d-state">waiting for answers</span></div>
      <div class="typing">${icon("users")}<span>The panches are answering. The debate starts when they're done.</span><i></i><i></i><i></i></div></section>`;
  }
  if (d.skipped) return `<div class="d-off">${icon("info")}<span>${esc(d.skipped)}</span></div>`;
  const meta = (id) => d.debaters.find((x) => x.id === id) || panchMeta(id, turn);
  const name = (id) => esc(meta(id).name);
  const state_ = d.end ? (d.end.consensus ? `<span class="d-state ok">Consensus · round ${d.end.rounds_used}</span>` : `<span class="d-state warn">No full consensus</span>`)
    : `<span class="d-state live"><span class="pulse"></span>Round ${d.current || 1} of ${d.rounds}</span>`;
  const parts = [`<div class="d-head">${icon("messages")}<span class="d-title">The debate</span>
    <span class="avatars">${d.debaters.map((p) => avatar(p)).join("")}</span>${state_}</div>`];

  parts.push(`<div class="d-round"><span>Opening answers</span></div>`);
  for (const o of d.openings) {
    parts.push(`<div class="msg">${avatar(meta(o.panch), "lg")}<div class="bubble"><div class="b-head"><b>${name(o.panch)}</b></div>
      <div class="b-text">${esc(o.text)}</div></div></div>`);
  }

  const lastRound = d.end ? d.end.rounds_used : d.current;
  for (let r = 1; r <= lastRound; r++) {
    parts.push(`<div class="d-round"><span>Round ${r} · ${r === 1 ? "Cross-examination" : "Rebuttal"}</span></div>`);
    for (const p of d.debaters) {
      const t = d.turns[`${r}:${p.id}`];
      if (!t) {
        if (!d.done[r]) parts.push(`<div class="typing">${avatar(p)}<span>${esc(p.name)} is reading a rival's answer</span><i></i><i></i><i></i></div>`);
        continue;
      }
      const key = `${turn.id}:${r}:${p.id}`;
      const open = state.openTurns.has(key);
      const stance = t.status === "silent" ? "silent" : t.stance;
      const full = open && t.reply
        ? `<div class="b-full prose">${renderMarkdown(t.reply)}</div>${t.references?.length ? `<div class="b-refs">${t.references.map((ref) => `<a href="${esc(ref.link)}" target="_blank" rel="noopener noreferrer"><img src="${favicon(ref.domain)}" alt="" onerror="this.remove()"/>${esc(shortSite(ref.domain))}</a>`).join("")}</div>` : ""}
           <div class="b-meta">Searched: “${esc(t.query)}” · ${formatSecs(t.latency_ms)}${t.cached ? " · cached" : ""}</div>`
        : "";
      parts.push(`<div class="msg ${stance}">${avatar(p, "lg")}<div class="bubble">
        <div class="b-head"><b>${name(t.speaker)}</b><span class="to">${icon("arrow-right")}${name(t.rival)}</span>
          <span class="stance-chip ${stance}">${STANCE_ICON[stance] || ""} ${esc(STANCE_WORD[stance] || stance)}${t.judged ? "" : turn.status === "loading" && turn.llm !== "none" ? " ·" : ""}</span></div>
        <div class="b-quote">“${esc(t.quote)}”</div>
        <div class="b-text">${t.status === "silent" ? `<span class="muted">${esc(t.note || "Did not reply this round.")}</span>` : esc(t.summary)}</div>
        ${full}
        ${t.reply ? `<button class="b-more" type="button" data-dturn="${key}">${open ? "Hide full reply" : "Full reply"}</button>` : ""}
      </div></div>`);
    }
    const done = d.done[r];
    if (done) {
      parts.push(`<div class="d-tally ${done.consensus ? "ok" : ""}">${done.consensus ? icon("check") : icon("scale")}
        <span>${done.agree} of ${done.spoke} agree${done.consensus ? " · consensus reached" : r < d.rounds && !d.end ? " · another round" : ""}</span></div>`);
    }
  }

  if (d.end) {
    const endorsed = (d.end.endorsed_by || []).map((id) => avatar(meta(id))).join("");
    const hold = (d.end.holdouts || []).map((id) => name(id)).join(", ");
    parts.push(`<div class="d-end ${d.end.consensus ? "ok" : "warn"}">
      <div class="e-head">${d.end.consensus ? icon("check") : icon("alert")}<b>${d.end.consensus ? "The panchayat agrees" : "The panchayat is still split"}</b></div>
      ${d.end.resolution ? `<div class="e-text">${esc(d.end.resolution)}</div>` : ""}
      <div class="e-meta">${endorsed ? `<span class="avatars">${endorsed}</span>` : ""}${d.end.consensus ? `Settled in ${d.end.rounds_used} round${d.end.rounds_used === 1 ? "" : "s"}` : hold ? `Still disagreeing: ${hold}` : "Agreement with caveats"}</div></div>`);
  }
  return `<section class="debate">${parts.join("")}</section>`;
}

function verdictCard(turn, c) {
  const pct = c ? c.consensus : 0;
  const r = 32;
  const circ = 2 * Math.PI * r;
  const offset = circ * (1 - pct / 100);
  const loading = turn.status === "loading" && !c;
  const method = turn.method === "llm" ? `Claims recorded by the clerk (${esc(turn.llm || "LLM")})` : turn.method === "heuristic" ? "Matched by sentence overlap" : "";
  return `<section class="c-section"><div class="verdict-card">
    <div class="ring"><svg viewBox="0 0 76 76"><circle class="track" cx="38" cy="38" r="${r}"/>
      <circle class="val" cx="38" cy="38" r="${r}" stroke-dasharray="${circ.toFixed(1)}" stroke-dashoffset="${offset.toFixed(1)}"/></svg>
      <div class="pct">${loading ? `<span class="spin"></span>` : `${pct}%`}</div></div>
    <div><div class="headline">${c ? esc(c.headline) : loading ? "The panches are speaking…" : "No verdict"}</div>
      <div class="ruling-text">${c ? esc(c.ruling) : ""}</div>
      <div class="sub">${c ? `Agreement on the core answer${method ? " · " + method : ""}` : ""}</div></div>
  </div></section>`;
}

function stanceFor(turn, c, id) {
  const p = turn.panches[id];
  if (!p) return turn.status === "loading" ? "waiting" : "abstained";
  if (p.status === "error") return "error";
  if (p.status !== "answered") return "abstained";
  return c?.stances?.[id]?.stance || "answered";
}

function panchSection(turn, c, order) {
  const items = order
    .map((meta) => {
      const p = turn.panches[meta.id];
      const stance = stanceFor(turn, c, meta.id);
      const open = state.openPanches.has(meta.id);
      let body = "";
      if (p && p.status === "answered") {
        const againstIds = c?.stances?.[meta.id]?.against_claims || [];
        const against = againstIds.map((cid) => c.claims.find((x) => x.id === cid)).filter(Boolean);
        const why = against.length
          ? `<div class="why">Disagrees with the majority on: ${against.map((x) => `“${esc(x.text)}”`).join("; ")}</div>`
          : "";
        const answer = renderMarkdown(p.answer, {
          cite: (nums) =>
            nums
              .map((n) => p.references[n - 1])
              .filter(Boolean)
              .map((ref) => `<a class="cite-num" href="${esc(ref.link)}" target="_blank" rel="noopener noreferrer" title="${esc(ref.title)}">${p.references.indexOf(ref) + 1}</a>`)
              .join(""),
        });
        body = `${why}<div class="prose">${answer}</div><div class="meta">${formatSecs(p.latency_ms)} · ${p.references.length} sources${p.cached ? " · from cache" : ""} · ${esc(meta.engines)}</div>`;
      } else if (p) {
        body = `<div class="meta">${esc(p.note || "No answer.")}</div>`;
      } else {
        body = `<div class="meta">${turn.status === "loading" ? "Waiting for this panch…" : "No answer received."}</div>`;
      }
      const stanceText = stance === "unclear" ? STANCE_LABEL.unclear : STANCE_LABEL[stance] || stance;
      return `<li class="panch ${open ? "open" : ""}"><button class="panch-row" data-panch="${meta.id}" aria-expanded="${open}" type="button">
        ${avatar(meta, "lg")}<span class="who"><span class="nm">${esc(meta.name)}</span><span class="eng">${esc(meta.engines)}</span></span>
        <span class="stance ${stance}">${stance === "waiting" ? `<span class="spin"></span>` : ""}${esc(stanceText)}</span>${icon("chevron-down", "chev")}</button>
        <div class="panch-body">${body}</div></li>`;
    })
    .join("");
  const answered = Object.values(turn.panches).filter((p) => p.status === "answered").length;
  return `<section class="c-section"><h3 class="c-title">${icon("users")}The panches <span class="count">${answered} of ${order.length} answered</span></h3>
    <ul class="panch-list">${items}</ul></section>`;
}

function claimsSection(turn, c, order) {
  if (!c || !c.claims.length) return "";
  const items = c.claims
    .map((claim) => {
      const votes = order
        .map((meta) => {
          const answered = turn.panches[meta.id]?.status === "answered";
          const pos = answered ? claim.positions[meta.id] || "silent" : "abstained";
          const mark = pos === "supports" ? "✓" : pos === "contradicts" ? "✕" : "";
          return `<span class="vote ${pos}" title="${esc(meta.name)}: ${pos}">${mark}</span>`;
        })
        .join("");
      const dissent = claim.status === "majority" && claim.contradict ? ` · ${claim.contradict} dissent` : "";
      const srcs = claim.sources.map((id) => citeHtml(turn, [id])).join("");
      return `<li class="claim"><div class="top"><span class="tag ${claim.status}">${esc(claim.status_label)}${dissent}</span>
        <span class="votes" aria-label="${claim.support} support, ${claim.contradict} contradict">${votes}</span></div>
        <div class="text">${esc(claim.text)}</div>${srcs ? `<div class="srcs">${srcs}</div>` : ""}</li>`;
    })
    .join("");
  return `<section class="c-section"><h3 class="c-title">${icon("scale")}Claim by claim <span class="count">${c.claims.length}</span></h3>
    <ul class="claims">${items}</ul></section>`;
}

function sourcesSection(turn) {
  const sources = turn.sources || [];
  if (!sources.length) return "";
  const tiers = TIER_ORDER.filter((t) => sources.some((s) => s.tier === t));
  if (state.sourceFilter !== "all" && !tiers.includes(state.sourceFilter)) state.sourceFilter = "all";
  const shown = sources.filter((s) => state.sourceFilter === "all" || s.tier === state.sourceFilter);
  const filters = [`<button class="filter ${state.sourceFilter === "all" ? "on" : ""}" data-tier="all">All</button>`]
    .concat(
      tiers.map((t) => {
        const label = sources.find((s) => s.tier === t).tier_label;
        return `<button class="filter ${state.sourceFilter === t ? "on" : ""}" data-tier="${t}">${esc(label)}</button>`;
      }),
    )
    .join("");
  const items = shown
    .map((s) => {
      const by = s.cited_by.map((id) => avatar(panchMeta(id, turn))).join("");
      return `<li class="src" data-src-id="${s.id}"><span class="n">${s.id}</span><div>
        <a class="t" href="${esc(s.link)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>
        <div class="d"><img src="${favicon(s.domain)}" alt="" loading="lazy" onerror="this.remove()"/>${esc(s.domain)}<span class="tier ${s.tier}">${esc(s.tier_label)}</span></div>
        <div class="by"><span class="avatars">${by}</span>cited by ${s.cited_by.length} panch${s.cited_by.length === 1 ? "" : "es"}</div></div></li>`;
    })
    .join("");
  return `<section class="c-section"><h3 class="c-title">${icon("library")}Sources <span class="count">${sources.length}</span></h3>
    <div class="filters">${filters}</div><ol class="sources">${items}</ol></section>`;
}

// ------------------------------------------------------------------ citation tooltip

function showCiteTip(cite) {
  const turn = findTurn(cite.dataset.turn);
  if (!turn) return;
  const ids = cite.dataset.src.split(",").map(Number);
  const sources = ids.map((id) => sourceById(turn, id)).filter(Boolean);
  if (!sources.length) return;
  const s = sources[0];
  const tip = $("#tooltip");
  tip.innerHTML = `<div class="tt-t">${esc(s.title)}</div>
    <div class="tt-d"><img src="${favicon(s.domain)}" alt="" width="14" height="14" onerror="this.remove()"/>${esc(s.domain)}<span class="tier ${s.tier}">${esc(s.tier_label)}</span>
    <span class="avatars">${s.cited_by.map((id) => avatar(panchMeta(id, turn))).join("")}</span></div>
    ${s.snippet ? `<div class="tt-s">${esc(s.snippet.slice(0, 220))}</div>` : ""}
    ${sources.length > 1 ? `<div class="tt-d" style="margin-top:6px">+ ${sources.slice(1).map((x) => esc(x.domain)).join(", ")}</div>` : ""}`;
  tip.hidden = false;
  const rect = cite.getBoundingClientRect();
  const width = Math.min(320, window.innerWidth - 24);
  tip.style.left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)) + "px";
  const below = rect.bottom + 8;
  tip.style.top = (below + tip.offsetHeight > window.innerHeight ? rect.top - tip.offsetHeight - 8 : below) + "px";

  if (state.focusedTurn !== turn.id) focusTurn(turn.id);
  $$(".src.lit, .cite.lit").forEach((n) => n.classList.remove("lit"));
  ids.forEach((id) => {
    const row = $(`#councilContent .src[data-src-id="${id}"]`);
    if (row) row.classList.add("lit");
  });
  const firstRow = $(`#councilContent .src[data-src-id="${ids[0]}"]`);
  const panel = $("#councilPanel");
  if (firstRow && getComputedStyle(panel).position !== "fixed") {
    const pr = panel.getBoundingClientRect();
    const rr = firstRow.getBoundingClientRect();
    if (rr.top < pr.top || rr.bottom > pr.bottom) panel.scrollTo({ top: panel.scrollTop + rr.top - pr.top - 120, behavior: "smooth" });
  }
}

function hideCiteTip() {
  $("#tooltip").hidden = true;
  $$(".src.lit").forEach((n) => n.classList.remove("lit"));
}

boot();
