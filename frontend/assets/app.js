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
  sourceFilter: "all",
};

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
  };
  thread.turns.push(turn);
  $("#turns").insertAdjacentHTML("beforeend", turnShell(turn));
  focusTurn(turn.id);
  renderTurn(turn);
  $("#threadTitle").textContent = thread.title;
  requestAnimationFrame(() => $(`#turn-${turn.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }));

  const controller = new AbortController();
  state.streaming = { controller, turnId: turn.id };
  setStreamingUI(true);

  try {
    await api.ask({ question, lang, history }, (event, data) => onEvent(turn, event, data), controller.signal);
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
  if (state.focusedTurn === turn.id && ["start", "panch", "sources", "council", "done"].includes(event)) renderCouncil();
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
  $("#threadTitle").textContent = thread?.title || "";
  $("#turns").innerHTML = (thread?.turns || []).map(turnShell).join("");
  (thread?.turns || []).forEach(renderTurn);
  focusTurn(state.focusedTurn || thread?.turns.at(-1)?.id);
}

function turnShell(turn) {
  return `<article class="turn" id="turn-${turn.id}" data-turn="${turn.id}">
    <h1 class="q">${esc(turn.question)}</h1>
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
  } else {
    const s = turn.stats;
    headText = `Consulted ${answered} of ${order.length} panches` + (s ? ` · ${s.searches} SerpApi search${s.searches === 1 ? "" : "es"} · ${formatSecs(s.elapsed_ms)}` : "");
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
    <button class="ghost-btn council-toggle" data-action="council">${icon("users")}<span>Council</span></button>
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
}

// ------------------------------------------------------------------ council column

function renderCouncil() {
  const host = $("#councilContent");
  const turn = findTurn(state.focusedTurn);
  if (!turn) {
    host.innerHTML = `<div class="c-empty">Ask a question to convene the council.<br/>Every panch's stance and source will appear here.</div>`;
    return;
  }
  const c = turn.council;
  const order = turn.order || state.panches;
  host.innerHTML = [verdictCard(turn, c), panchSection(turn, c, order), claimsSection(turn, c, order), sourcesSection(turn)].join("");
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
