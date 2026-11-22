/*
 * Chat widget. Add one line to any website:
 *   <script src="https://YOUR-HOST/widget.js" data-tenant="dentiste" defer></script>
 * Options: data-color="#0e7490", data-open="true", data-lang="fr|en|ar", data-timing="true"
 */
(function () {
  "use strict";
  const script = document.currentScript;
  if (!script || window.__aiAssistantLoaded) return;
  window.__aiAssistantLoaded = true;

  const API = new URL(script.src).origin;
  const TENANT = script.dataset.tenant || "dentiste";
  const SHOW_TIMING = script.dataset.timing !== "false";
  const STORE_KEY = "ai-assistant:" + TENANT;

  const T = {
    fr: { placeholder: "Écrivez votre message…", send: "Envoyer", online: "En ligne · répond en quelques secondes",
      typing: "L'assistant écrit…", lead: "✓ Votre demande a été transmise à l'équipe",
      human: "Un membre de l'équipe a repris la conversation", demo: "Démo : n'entrez pas de vraies données personnelles.",
      reset: "Nouvelle conversation", error: "Connexion impossible, réessayez dans un instant.", answered: "répondu en",
      sources: "Sources", open: "Une question ? Écrivez-nous", team: "Équipe" },
    en: { placeholder: "Type your message…", send: "Send", online: "Online · answers in seconds",
      typing: "The assistant is typing…", lead: "✓ Your request was sent to the team",
      human: "A team member has taken over the chat", demo: "Demo: please don't enter real personal data.",
      reset: "New conversation", error: "Can't connect, please try again in a moment.", answered: "answered in",
      sources: "Sources", open: "Questions? Chat with us", team: "Team" },
    ar: { placeholder: "اكتب رسالتك…", send: "إرسال", online: "متصل · يجاوب في ثواني",
      typing: "المساعد يكتب…", lead: "✓ طلبك وصل للفريق",
      human: "واحد من الفريق خذا المحادثة", demo: "ديمو: ما تكتبش معطيات شخصية حقيقية.",
      reset: "محادثة جديدة", error: "مشكل في الاتصال، عاود بعد شوية.", answered: "جاوب في",
      sources: "المصادر", open: "عندك سؤال؟ احكي معانا", team: "الفريق" },
  };

  const load = () => { try { return JSON.parse(localStorage.getItem(STORE_KEY)) || {}; } catch { return {}; } };
  const save = (s) => { try { localStorage.setItem(STORE_KEY, JSON.stringify(s)); } catch { /* private mode */ } };

  let state = load();
  // The page's language first: a French site gets a French widget whatever the browser says.
  let lang = (script.dataset.lang || state.lang || document.documentElement.lang || navigator.language || "fr").slice(0, 2);
  if (!T[lang]) lang = "fr";
  let tenant = null;
  let busy = false;
  let pollTimer = null;

  const host = document.createElement("div");
  host.id = "ai-assistant-widget";
  document.body.appendChild(host);
  const root = host.attachShadow({ mode: "open" });

  const color = script.dataset.color || "#0f766e";
  root.innerHTML = `
  <style>
    :host { all: initial; }
    * { box-sizing: border-box; font-family: "Inter", system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans Arabic", sans-serif; }
    .launcher { position: fixed; right: 20px; bottom: 20px; z-index: 2147483000; display: flex; align-items: center; gap: 10px;
      border: 0; cursor: pointer; background: ${color}; color: #fff; border-radius: 999px; padding: 14px 20px 14px 16px;
      font-size: 15px; font-weight: 600; box-shadow: 0 10px 30px rgba(0,0,0,.25); transition: transform .15s; }
    .launcher:hover { transform: translateY(-2px); }
    .launcher svg { width: 22px; height: 22px; }
    .panel { position: fixed; right: 20px; bottom: 20px; z-index: 2147483001; width: 380px; height: min(620px, calc(100vh - 40px));
      background: #fff; color: #111827; border-radius: 18px; box-shadow: 0 20px 60px rgba(0,0,0,.3); display: none;
      flex-direction: column; overflow: hidden; }
    .panel.open { display: flex; animation: pop .18s ease-out; }
    @keyframes pop { from { opacity: 0; transform: translateY(12px) scale(.98); } }
    @media (max-width: 480px) { .panel { right: 0; bottom: 0; width: 100vw; height: 100dvh; border-radius: 0; } }
    header { background: ${color}; color: #fff; padding: 14px 14px 12px 16px; display: flex; align-items: center; gap: 12px; }
    .avatar { width: 38px; height: 38px; border-radius: 50%; background: rgba(0,0,0,.22); display: grid; place-items: center; font-weight: 700; }
    .title { flex: 1; min-width: 0; }
    .title b { display: block; font-size: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .title small { font-size: 12px; opacity: .9; display: flex; align-items: center; gap: 6px; }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: #4ade80; display: inline-block; }
    .icon-btn { background: transparent; border: 0; color: #fff; cursor: pointer; padding: 6px; border-radius: 8px; font-size: 13px; }
    .icon-btn:hover { background: rgba(255,255,255,.15); }
    .langs { display: flex; gap: 2px; }
    .langs button { background: transparent; border: 0; color: #fff; opacity: .9; cursor: pointer; font-size: 12px; font-weight: 600; padding: 4px 6px; border-radius: 6px; }
    .langs button.on { opacity: 1; background: rgba(0,0,0,.22); }
    .msgs { flex: 1; overflow-y: auto; padding: 16px; background: #f6f7f9; display: flex; flex-direction: column; gap: 10px; }
    .row { display: flex; flex-direction: column; max-width: 85%; }
    .row.user { align-self: flex-end; align-items: flex-end; }
    .bubble { padding: 10px 13px; border-radius: 16px; font-size: 14.5px; line-height: 1.45; white-space: pre-wrap; word-wrap: break-word; }
    .user .bubble { background: ${color}; color: #fff; border-bottom-right-radius: 4px; }
    .bot .bubble { background: #fff; border: 1px solid #e5e7eb; border-bottom-left-radius: 4px; }
    .human .bubble { background: #fffbeb; border: 1px solid #fcd34d; border-bottom-left-radius: 4px; }
    .meta { font-size: 11px; color: #6b7280; margin-top: 4px; padding: 0 4px; display: flex; gap: 8px; flex-wrap: wrap; }
    .meta details { cursor: pointer; }
    .meta summary { list-style: none; text-decoration: underline dotted; }
    .meta ul { margin: 4px 0 0; padding-left: 16px; }
    .notice { align-self: center; font-size: 12px; color: #065f46; background: #d1fae5; padding: 6px 10px; border-radius: 999px; text-align: center; }
    .notice.warn { color: #92400e; background: #fef3c7; }
    .notice.err { color: #991b1b; background: #fee2e2; }
    .typing { display: inline-flex; gap: 4px; padding: 12px 14px; }
    .typing i { width: 6px; height: 6px; border-radius: 50%; background: #9ca3af; animation: blink 1.2s infinite; }
    .typing i:nth-child(2) { animation-delay: .2s; } .typing i:nth-child(3) { animation-delay: .4s; }
    @keyframes blink { 0%, 80%, 100% { opacity: .3; } 40% { opacity: 1; } }
    .chips { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 16px 10px; background: #f6f7f9; }
    .chips button { border: 1px solid ${color}; color: ${color}; background: #fff; border-radius: 999px; padding: 6px 11px; font-size: 13px; cursor: pointer; }
    .chips button:hover { background: ${color}; color: #fff; }
    form { display: flex; gap: 8px; padding: 10px; border-top: 1px solid #e5e7eb; background: #fff; }
    textarea { flex: 1; resize: none; border: 1px solid #d1d5db; border-radius: 12px; padding: 10px 12px; font-size: 14.5px; max-height: 110px; outline: none; color: #111827; background: #fff; }
    textarea:focus { border-color: ${color}; box-shadow: 0 0 0 3px ${color}22; }
    form button { border: 0; background: ${color}; color: #fff; border-radius: 12px; width: 44px; cursor: pointer; display: grid; place-items: center; }
    form button:disabled { opacity: .5; cursor: default; }
    .foot { font-size: 11px; color: #6b7280; text-align: center; padding: 0 10px 8px; background: #fff; }
  </style>
  <button class="launcher" part="launcher" aria-label="Chat">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
    <span class="launch-label"></span>
  </button>
  <section class="panel" role="dialog" aria-label="Chat">
    <header>
      <div class="avatar">AI</div>
      <div class="title"><b class="name">…</b><small><span class="dot"></span><span class="status"></span></small></div>
      <div class="langs"><button data-l="fr">FR</button><button data-l="en">EN</button><button data-l="ar">ع</button></div>
      <button class="icon-btn reset" title="">↺</button>
      <button class="icon-btn close" aria-label="Close">✕</button>
    </header>
    <div class="msgs" aria-live="polite"></div>
    <div class="chips"></div>
    <form><textarea rows="1"></textarea><button type="submit" aria-label="Send">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4 20-7z"/></svg>
    </button></form>
    <div class="foot"></div>
  </section>`;

  const $ = (s) => root.querySelector(s);
  const panel = $(".panel"), msgs = $(".msgs"), input = $("textarea"), sendBtn = $("form button"), chips = $(".chips");

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function scroll() { msgs.scrollTop = msgs.scrollHeight; }

  function addBubble(role, text, meta) {
    const row = el("div", "row " + role);
    const b = el("div", "bubble", text);
    b.dir = "auto";
    row.appendChild(b);
    if (meta) row.appendChild(meta);
    msgs.appendChild(row);
    scroll();
    return row;
  }

  function addNotice(text, kind) { msgs.appendChild(el("div", "notice " + (kind || ""), text)); scroll(); }

  function metaFor(seconds, sources) {
    const t = T[lang];
    const meta = el("div", "meta");
    if (SHOW_TIMING && seconds != null) meta.appendChild(el("span", "", `${t.answered} ${seconds.toFixed(1)} s`));
    if (sources && sources.length) {
      const d = el("details");
      d.appendChild(el("summary", "", `${t.sources} (${sources.length})`));
      const ul = el("ul");
      sources.slice(0, 3).forEach((s) => ul.appendChild(el("li", "", `${s.source} › ${s.heading}`)));
      d.appendChild(ul);
      meta.appendChild(d);
    }
    return meta.childNodes.length ? meta : null;
  }

  function renderStatic() {
    const t = T[lang];
    $(".launch-label").textContent = t.open;
    $(".status").textContent = state.mode === "human" ? t.human : t.online;
    $(".reset").title = t.reset;
    input.placeholder = t.placeholder;
    $(".foot").textContent = t.demo;
    root.querySelectorAll(".langs button").forEach((b) => b.classList.toggle("on", b.dataset.l === lang));
    panel.dir = lang === "ar" ? "rtl" : "ltr";
  }

  function renderChips() {
    chips.innerHTML = "";
    if (!tenant || (state.history && state.history.length)) return;
    (tenant.suggestions[lang] || tenant.suggestions.fr).forEach((s) => {
      const c = el("button", "", s);
      c.type = "button";
      c.dir = "auto";
      c.onclick = () => send(s);
      chips.appendChild(c);
    });
  }

  function renderHistory() {
    msgs.innerHTML = "";
    if (tenant) addBubble("bot", tenant.welcome[lang] || tenant.welcome.fr);
    (state.history || []).forEach((m) => addBubble(m.role, m.text));
    if (state.leadShown) addNotice(T[lang].lead);
    renderChips();
  }

  function remember(role, text) {
    state.history = (state.history || []).concat([{ role, text }]).slice(-40);
    save(state);
  }

  async function send(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    busy = true; sendBtn.disabled = true; input.value = ""; chips.innerHTML = "";
    addBubble("user", text);
    remember("user", text);
    const typing = el("div", "row bot");
    const dots = el("div", "bubble typing"); dots.innerHTML = "<i></i><i></i><i></i>";
    typing.appendChild(dots);
    if (state.mode !== "human") { msgs.appendChild(typing); scroll(); }
    const started = performance.now();
    try {
      const res = await fetch(API + "/api/v1/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tenant: TENANT, message: text, conversation_id: state.conversationId || null }),
      });
      typing.remove();
      if (!res.ok) throw new Error(res.status);
      const data = await res.json();
      state.conversationId = data.conversation_id;
      const seconds = (performance.now() - started) / 1000;
      if (data.reply) {
        addBubble("bot", data.reply, metaFor(seconds, data.sources));
        remember("bot", data.reply);
      }
      if (data.lead_captured && !state.leadShown) { state.leadShown = true; addNotice(T[lang].lead); }
      if (data.mode === "human" && state.mode !== "human") addNotice(T[lang].human, "warn");
      state.mode = data.mode;
      save(state);
      renderStatic();
      if (state.mode === "human") startPolling();
    } catch (e) {
      typing.remove();
      addNotice(T[lang].error, "err");
    } finally {
      busy = false; sendBtn.disabled = false; input.focus();
    }
  }

  async function poll() {
    if (!state.conversationId) return;
    try {
      const res = await fetch(`${API}/api/v1/chat/${state.conversationId}/messages?after_id=${state.lastSeen || 0}`);
      if (!res.ok) return;
      const rows = await res.json();
      rows.forEach((m) => {
        state.lastSeen = Math.max(state.lastSeen || 0, m.id);
        if (m.role === "human") { addBubble("human", m.content); remember("human", m.content); }
      });
      save(state);
    } catch { /* offline: try again next tick */ }
  }

  async function startPolling() {
    if (pollTimer) return;
    if (!state.lastSeen) {
      // Only show team replies written from now on.
      try {
        const res = await fetch(`${API}/api/v1/chat/${state.conversationId}/messages`);
        const rows = res.ok ? await res.json() : [];
        state.lastSeen = rows.reduce((m, r) => Math.max(m, r.id), 0);
      } catch { state.lastSeen = 0; }
    }
    pollTimer = setInterval(poll, 4000);
  }

  function open() { panel.classList.add("open"); $(".launcher").style.display = "none"; input.focus(); }
  function close() { panel.classList.remove("open"); $(".launcher").style.display = ""; }
  // Escape closes the chat and gives the keyboard focus back to the launcher.
  panel.addEventListener("keydown", (e) => { if (e.key === "Escape") { close(); $(".launcher").focus(); } });

  $(".launcher").onclick = open;
  $(".close").onclick = close;
  $(".reset").onclick = () => {
    clearInterval(pollTimer); pollTimer = null;
    state = { lang }; save(state); renderStatic(); renderHistory();
  };
  root.querySelectorAll(".langs button").forEach((b) => (b.onclick = () => {
    lang = b.dataset.l; state.lang = lang; save(state); renderStatic(); renderHistory();
  }));
  $("form").onsubmit = (e) => { e.preventDefault(); send(input.value); };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input.value); }
  });
  input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 110) + "px"; });

  // Let the host page open the chat or send a message: window.aiAssistant.ask("…")
  window.aiAssistant = { open, close, ask: (text) => { open(); send(text); } };

  renderStatic();
  fetch(API + "/api/v1/tenants")
    .then((r) => r.json())
    .then((list) => {
      tenant = list.find((t) => t.slug === TENANT) || list[0];
      $(".name").textContent = tenant.name;
      renderHistory();
      if (state.mode === "human" && state.conversationId) startPolling();
      if (script.dataset.open === "true") open();
    })
    .catch(() => addNotice(T[lang].error, "err"));
})();
