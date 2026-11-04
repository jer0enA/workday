(function () {
  "use strict";

  const STEP = 15;
  const WORKDAY = 8 * 60;
  const DAY_MAX = 24 * 60;
  const LOCALE = "en-GB";

  // ---------- helpers ----------

  const pad = (n) => String(n).padStart(2, "0");
  const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const parseIso = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
  const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
  const today = () => iso(new Date());
  const fmt = (m) => `${Math.floor(m / 60)}:${pad(m % 60)}`;

  function mondayOf(d) {
    const wd = (d.getDay() + 6) % 7; // Monday = 0
    return addDays(d, -wd);
  }

  function isoWeek(d) {
    const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
    const day = t.getUTCDay() || 7;
    t.setUTCDate(t.getUTCDate() + 4 - day);
    const yearStart = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
    return Math.ceil(((t - yearStart) / 86400000 + 1) / 7);
  }

  const dfmt = (d, opts) => d.toLocaleDateString(LOCALE, opts);

  // Tiny DOM builder; text is always set via textContent (no innerHTML).
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "style") Object.assign(el.style, v);
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k in el && typeof v !== "string") el[k] = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  function debounce(fn, ms) {
    let t = null;
    const run = (...a) => { t = null; return fn(...a); };
    const wrapped = (...a) => { clearTimeout(t); t = setTimeout(run, ms, ...a); };
    // Runs a pending call immediately; no-op when nothing is pending.
    wrapped.flush = () => { if (t !== null) { clearTimeout(t); run(); } };
    return wrapped;
  }

  // ---------- API (real backend, falls back to in-browser mock) ----------

  let useMock = false;

  async function call(method, path, body, asText) {
    if (useMock) {
      const r = window.WorkdayMock.handle(method, path, body);
      if (r.status >= 400) throw new Error(r.data.detail || `HTTP ${r.status}`);
      return asText ? r.text : r.data;
    }
    const r = await fetch(path, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : {},
      body: body !== undefined ? JSON.stringify(body) : undefined,
      credentials: "same-origin",
    });
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { const j = await r.json(); if (j.detail) msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (_) { /* not json */ }
      throw new Error(msg);
    }
    return asText ? r.text() : r.json();
  }

  async function detectBackend() {
    try {
      const r = await fetch("api/categories", { credentials: "same-origin" });
      const ct = r.headers.get("content-type") || "";
      if (r.ok && ct.includes("json")) return;
    } catch (_) { /* unreachable */ }
    if (window.WorkdayMock) {
      useMock = true;
      document.getElementById("demo-badge").hidden = false;
    }
  }

  const api = {
    me: () => call("GET", "api/me"),
    categories: () => call("GET", "api/categories"),
    addCategory: (c) => call("POST", "api/categories", c),
    patchCategory: (id, c) => call("PATCH", `api/categories/${id}`, c),
    day: (date) => call("GET", `api/day/${date}`),
    saveDay: (date, entries) => call("PUT", `api/day/${date}`, entries),
    notes: (categoryId, q) => call("GET", `api/notes?category_id=${encodeURIComponent(categoryId)}&q=${encodeURIComponent(q)}`),
    report: (from, to) => call("GET", `api/report?from=${from}&to=${to}`),
    csv: (from, to) => call("GET", `api/export.csv?from=${from}&to=${to}`, undefined, true),
  };

  // ---------- state & routing ----------

  const app = document.getElementById("app");
  let categories = [];
  const catById = (id) => categories.find((c) => c.id === id);
  let current = { view: "day" };
  let renderToken = 0;

  function parseHash() {
    const [, view, arg] = (location.hash || "").replace(/^#\/?/, "/").split("/");
    switch (view) {
      case "week": return { view, start: iso(mondayOf(arg ? parseIso(arg) : new Date())) };
      case "month": return { view, month: /^\d{4}-\d{2}$/.test(arg || "") ? arg : today().slice(0, 7) };
      case "categories": return { view };
      default: return { view: "day", date: /^\d{4}-\d{2}-\d{2}$/.test(arg || "") ? arg : today() };
    }
  }

  function go(route) {
    let hash;
    if (route.view === "day") hash = `#/day/${route.date}`;
    else if (route.view === "week") hash = `#/week/${route.start}`;
    else if (route.view === "month") hash = `#/month/${route.month}`;
    else hash = "#/categories";
    if (location.hash === hash) render(); else location.hash = hash;
  }

  function step(dir) {
    const r = current;
    if (r.view === "day") go({ view: "day", date: iso(addDays(parseIso(r.date), dir)) });
    else if (r.view === "week") go({ view: "week", start: iso(addDays(parseIso(r.start), 7 * dir)) });
    else if (r.view === "month") {
      const [y, m] = r.month.split("-").map(Number);
      const d = new Date(y, m - 1 + dir, 1);
      go({ view: "month", month: `${d.getFullYear()}-${pad(d.getMonth() + 1)}` });
    }
  }

  function setActiveTab() {
    document.querySelectorAll("[data-view]").forEach((b) => {
      b.classList.toggle("active", b.dataset.view === current.view);
    });
  }

  async function render() {
    if (daySave) daySave.flush();
    current = parseHash();
    setActiveTab();
    const token = ++renderToken;
    const views = { day: renderDay, week: renderWeek, month: renderMonth, categories: renderCategories };
    try {
      const node = await views[current.view](current);
      if (token !== renderToken) return; // a newer navigation won
      app.replaceChildren(node);
      const focus = app.querySelector("[data-autofocus]");
      if (focus) focus.focus();
    } catch (e) {
      if (token !== renderToken) return;
      app.replaceChildren(h("div", { class: "empty" }, `Could not load: ${e.message}`));
    }
  }

  function navbar(label, sub, extra) {
    return h("div", { class: "navbar" },
      h("button", { type: "button", class: "nav-btn", "aria-label": "Previous", onclick: () => step(-1) }, "‹"),
      h("div", { class: "label" }, label, sub ? h("span", { class: "sub" }, sub) : null),
      h("button", { type: "button", class: "nav-btn", "aria-label": "Next", onclick: () => step(1) }, "›"),
      extra || null,
    );
  }

  // ---------- day view ----------

  let daySave = null;

  async function renderDay({ date }) {
    const rows = (await api.day(date)).map((e, i) => ({ key: `s${i}`, category_id: e.category_id, minutes: e.minutes, note: e.note || "" }));
    let keySeq = 0;
    const d = parseIso(date);
    const isToday = date === today();

    const status = h("span", { class: "status" });
    const totalEl = h("strong");
    const list = h("div", { class: "entries" });

    const total = () => rows.reduce((s, r) => s + r.minutes, 0);

    const doSave = async () => {
      status.className = "status"; status.textContent = "Saving…";
      try {
        await api.saveDay(date, rows.map((r) => ({ category_id: r.category_id, minutes: r.minutes, note: r.note.trim() || null })));
        status.className = "status ok"; status.textContent = "✓ Saved";
      } catch (e) {
        status.className = "status err"; status.textContent = `Not saved: ${e.message}`;
      }
    };
    daySave = debounce(doSave, 500);

    const changed = () => { refreshTotals(); daySave(); };

    function refreshTotals() {
      totalEl.textContent = fmt(total());
      list.querySelectorAll(".entry").forEach((el) => {
        const r = rows.find((x) => x.key === el.dataset.key);
        if (!r) return;
        el.querySelector(".hours").textContent = fmt(r.minutes);
        el.querySelector(".minus").disabled = r.minutes <= STEP;
        el.querySelector(".plus").disabled = total() >= DAY_MAX;
      });
    }

    function setMinutes(r, m) {
      const others = total() - r.minutes;
      r.minutes = Math.max(STEP, Math.min(m, DAY_MAX - others));
      changed();
    }

    function entryEl(r) {
      const cat = catById(r.category_id) || { color: "#999" };
      const dot = h("span", { class: "dot", style: { background: cat.color } });
      const select = h("select", {
        class: "cat-select",
        "aria-label": "Category",
        onchange: (ev) => { r.category_id = Number(ev.target.value); dot.style.background = catById(r.category_id).color; changed(); },
      }, categories
        .filter((c) => !c.archived || c.id === r.category_id)
        .map((c) => h("option", { value: String(c.id), selected: c.id === r.category_id }, c.name)));

      const note = h("input", {
        class: "note-input",
        type: "text",
        maxlength: "200",
        placeholder: "Note, e.g. project name",
        value: r.note,
        oninput: (ev) => { r.note = ev.target.value; daySave(); },
      });
      if (r.focus) { note.setAttribute("data-autofocus", ""); delete r.focus; }

      const wrap = h("div", { class: "note-wrap" }, note);
      attachSuggest(note, wrap, () => r.category_id, (v) => { r.note = v; daySave(); });

      return h("div", { class: "entry card", "data-key": r.key },
        h("div", { class: "entry-top" },
          h("span", { class: "cat-wrap" }, dot, select),
          h("span", { class: "stepper" },
            h("button", { type: "button", class: "step-btn minus", "aria-label": "Less", onclick: () => setMinutes(r, r.minutes - STEP) }, "−"),
            h("span", { class: "hours" }, fmt(r.minutes)),
            h("button", { type: "button", class: "step-btn plus", "aria-label": "More", onclick: () => setMinutes(r, r.minutes + STEP) }, "+"),
          ),
          h("span", { class: "quick" },
            h("button", { type: "button", onclick: () => setMinutes(r, 240) }, "4h"),
            h("button", { type: "button", onclick: () => setMinutes(r, 480) }, "8h"),
          ),
          h("button", {
            type: "button", class: "remove-btn", title: "Remove", "aria-label": "Remove",
            onclick: () => { rows.splice(rows.indexOf(r), 1); drawList(); changed(); },
          }, "✕"),
        ),
        wrap,
      );
    }

    function drawList() {
      list.replaceChildren(...(rows.length
        ? rows.map(entryEl)
        : [h("div", { class: "empty" }, "Nothing logged yet. Add a category below.")]));
      refreshTotals();
      const f = list.querySelector("[data-autofocus]");
      if (f && app.contains(list)) f.focus();
    }

    function addRow(catId) {
      const left = DAY_MAX - total();
      if (left < STEP) return;
      const remaining = WORKDAY - total();
      const minutes = Math.min(remaining >= STEP ? remaining : 60, left);
      rows.push({ key: `n${keySeq++}`, category_id: catId, minutes, note: "", focus: true });
      drawList();
      changed();
    }

    drawList();

    const addRow_ = h("div", { class: "add-row" },
      categories.filter((c) => !c.archived).map((c) =>
        h("button", { type: "button", class: "add-btn", onclick: () => addRow(c.id) },
          h("span", { class: "dot", style: { background: c.color } }), `+ ${c.name}`)));

    return h("section", {},
      navbar(
        dfmt(d, { weekday: "long", day: "numeric", month: "long" }),
        `${d.getFullYear()} · week ${isoWeek(d)}`,
        h("button", { type: "button", class: "pill-btn", disabled: isToday, onclick: () => go({ view: "day", date: today() }) }, "Today"),
      ),
      list,
      addRow_,
      h("div", { class: "day-footer" },
        h("span", { class: "total" }, "Total", totalEl),
        status,
      ),
    );
  }

  // Autocomplete dropdown for note inputs.
  function attachSuggest(input, wrap, getCat, onPick) {
    let listEl = null;
    let items = [];
    let active = -1;

    const close = () => { if (listEl) listEl.remove(); listEl = null; items = []; active = -1; };

    const pick = (v) => { input.value = v; onPick(v); close(); };

    const draw = () => {
      if (!items.length) { close(); return; }
      if (!listEl) { listEl = h("ul", { class: "suggest", role: "listbox" }); wrap.append(listEl); }
      listEl.replaceChildren(...items.map((v, i) =>
        h("li", {
          class: i === active ? "active" : "",
          role: "option",
          onmousedown: (ev) => { ev.preventDefault(); pick(v); },
        }, v)));
    };

    const fetchSuggest = debounce(async () => {
      if (document.activeElement !== input) return;
      try {
        const q = input.value.trim();
        const res = await api.notes(getCat(), q);
        items = res.filter((v) => v !== input.value);
        active = -1;
        draw();
      } catch (_) { close(); }
    }, 120);

    input.addEventListener("input", fetchSuggest);
    input.addEventListener("focus", fetchSuggest);
    input.addEventListener("blur", () => setTimeout(close, 100));
    input.addEventListener("keydown", (ev) => {
      if (!listEl) return;
      if (ev.key === "ArrowDown") { active = (active + 1) % items.length; draw(); ev.preventDefault(); }
      else if (ev.key === "ArrowUp") { active = (active - 1 + items.length) % items.length; draw(); ev.preventDefault(); }
      else if (ev.key === "Enter" && active >= 0) { pick(items[active]); ev.preventDefault(); }
      else if (ev.key === "Escape") { close(); }
    });
  }

  // ---------- report views (week / month) ----------

  async function exportCsv(from, to) {
    const text = await api.csv(from, to);
    const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
    const a = h("a", { href: url, download: `workday_${from}_${to}.csv` });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // columns: [{key, head, sub, days: [iso...], onclick, cls}]
  function reportTable(report, columns) {
    if (!report.categories.length) {
      return h("div", { class: "card report-empty" }, "No hours logged in this period.");
    }
    const sum = (days, src) => days.reduce((s, d) => s + (src[d] || 0), 0);
    const cell = (m, cls) => h("td", { class: [m ? "" : "zero", cls].filter(Boolean).join(" ") }, m ? fmt(m) : "–");

    const thead = h("thead", {}, h("tr", {},
      h("th", {}, ""),
      columns.map((c) => h("th", { class: [c.cls, c.onclick ? "clickable" : ""].filter(Boolean).join(" "), onclick: c.onclick },
        c.head, h("span", { class: "d" }, c.sub))),
      h("th", { class: "sum" }, "Σ"),
    ));

    const tbody = h("tbody");
    for (const c of report.categories) {
      const notes = c.notes.filter((n) => n.note);
      const expandable = notes.length > 0;
      const noteRows = c.notes.map((n) => h("tr", { class: "note", hidden: true },
        h("td", {}, n.note || "(no note)"),
        columns.map((col) => cell(sum(col.days, n.days))),
        cell(n.total, "sum"),
      ));
      const tr = h("tr", {
        class: `cat${expandable ? " expandable" : ""}`,
        onclick: expandable ? () => {
          const open = tr.classList.toggle("open");
          noteRows.forEach((r) => { r.hidden = !open; });
        } : null,
      },
      h("td", {},
        h("span", { class: "chev" }, expandable ? "▶" : ""), " ",
        h("span", { class: "dot", style: { background: c.color } }), " ",
        c.name),
      columns.map((col) => cell(sum(col.days, c.days))),
      cell(c.total, "sum"),
      );
      tbody.append(tr, ...noteRows);
    }

    const tfoot = h("tfoot", {}, h("tr", {},
      h("td", {}, "Total"),
      columns.map((col) => cell(sum(col.days, report.day_totals))),
      cell(report.total, "sum"),
    ));

    return h("div", { class: "card table-wrap" }, h("table", { class: "report" }, thead, tbody, tfoot));
  }

  function reportSection(nav, from, to, table) {
    return h("section", {},
      nav,
      h("div", { class: "report-actions" },
        h("button", { type: "button", class: "pill-btn", onclick: () => exportCsv(from, to).catch((e) => alert(e.message)) }, "⤓ Export CSV")),
      table,
    );
  }

  async function renderWeek({ start }) {
    const s = parseIso(start);
    const days = [...Array(7)].map((_, i) => addDays(s, i));
    const from = iso(days[0]);
    const to = iso(days[6]);
    const report = await api.report(from, to);
    const t = today();
    const columns = days.map((d) => {
      const k = iso(d);
      const wd = d.getDay();
      return {
        days: [k],
        head: dfmt(d, { weekday: "short" }),
        sub: String(d.getDate()),
        cls: [k === t ? "today" : "", wd === 0 || wd === 6 ? "weekend" : ""].filter(Boolean).join(" "),
        onclick: () => go({ view: "day", date: k }),
      };
    });
    const range = `${dfmt(days[0], { day: "numeric", month: "short" })} – ${dfmt(days[6], { day: "numeric", month: "short", year: "numeric" })}`;
    const thisWeek = from === iso(mondayOf(new Date()));
    return reportSection(
      navbar(`Week ${isoWeek(s)}`, range,
        h("button", { type: "button", class: "pill-btn", disabled: thisWeek, onclick: () => go({ view: "week", start: iso(mondayOf(new Date())) }) }, "This week")),
      from, to, reportTable(report, columns));
  }

  async function renderMonth({ month }) {
    const [y, m] = month.split("-").map(Number);
    const first = new Date(y, m - 1, 1);
    const last = new Date(y, m, 0);
    const from = iso(first);
    const to = iso(last);
    const report = await api.report(from, to);

    const columns = [];
    for (let wk = mondayOf(first); wk <= last; wk = addDays(wk, 7)) {
      const days = [...Array(7)].map((_, i) => addDays(wk, i)).filter((d) => d >= first && d <= last);
      const a = days[0];
      const b = days[days.length - 1];
      const weekStart = iso(wk);
      columns.push({
        days: days.map(iso),
        head: a.getDate() === b.getDate() ? `${a.getDate()}` : `${a.getDate()}–${b.getDate()}`,
        sub: `wk ${isoWeek(a)}`,
        onclick: () => go({ view: "week", start: weekStart }),
      });
    }
    const thisMonth = month === today().slice(0, 7);
    return reportSection(
      navbar(dfmt(first, { month: "long" }), String(y),
        h("button", { type: "button", class: "pill-btn", disabled: thisMonth, onclick: () => go({ view: "month", month: today().slice(0, 7) }) }, "This month")),
      from, to, reportTable(report, columns));
  }

  // ---------- categories ----------

  async function renderCategories() {
    const [cats, me] = await Promise.all([api.categories(), api.me().catch(() => null)]);
    categories = cats;
    const msg = h("div", { class: "hint" }, "Archived categories are hidden when logging, but stay visible in reports.");

    const update = async (c, patch) => {
      try {
        Object.assign(c, await api.patchCategory(c.id, patch));
      } catch (e) { msg.textContent = `Error: ${e.message}`; }
    };

    const item = (c) => {
      const row = h("div", { class: `cat-item${c.archived ? " archived" : ""}` },
        h("input", { type: "color", class: "color-input", value: c.color, "aria-label": "Color", onchange: (ev) => update(c, { color: ev.target.value }) }),
        h("input", {
          type: "text", class: "name-input", value: c.name, maxlength: "50", "aria-label": "Name",
          onchange: (ev) => { const v = ev.target.value.trim(); if (v) update(c, { name: v }); else ev.target.value = c.name; },
          onkeydown: (ev) => { if (ev.key === "Enter") ev.target.blur(); },
        }),
        h("button", {
          type: "button", class: "pill-btn",
          onclick: async () => { await update(c, { archived: !c.archived }); row.replaceWith(item(c)); },
        }, c.archived ? "Restore" : "Archive"),
      );
      return row;
    };

    const listEl = h("div", { class: "card cat-list" }, categories.map(item));
    const nameIn = h("input", { type: "text", placeholder: "New category", maxlength: "50" });
    const palette = ["#2563eb", "#9333ea", "#dc2626", "#16a34a", "#d97706", "#0891b2", "#db2777", "#64748b"];
    const add = async () => {
      const name = nameIn.value.trim();
      if (!name) return;
      try {
        const c = await api.addCategory({ name, color: palette[categories.length % palette.length] });
        categories.push(c);
        listEl.append(item(c));
        nameIn.value = "";
        msg.textContent = "Archived categories are hidden when logging, but stay visible in reports.";
      } catch (e) { msg.textContent = `Error: ${e.message}`; }
    };
    nameIn.addEventListener("keydown", (ev) => { if (ev.key === "Enter") add(); });

    return h("section", {},
      h("h2", { class: "section-title" }, "Categories"),
      listEl,
      h("div", { class: "new-cat" }, nameIn, h("button", { type: "button", class: "pill-btn primary", onclick: add }, "Add")),
      msg,
      me ? h("div", { class: "hint" },
        `Signed in as ${me.display_name}. Your hours and notes are private to you; categories are shared.`) : null,
    );
  }

  // ---------- boot ----------

  document.querySelectorAll("[data-view]").forEach((b) => {
    b.addEventListener("click", () => {
      const v = b.dataset.view;
      const ref = current.date || current.start || (current.month ? `${current.month}-01` : today());
      if (v === "day") go({ view: "day", date: current.view === "day" ? current.date : today() });
      else if (v === "week") go({ view: "week", start: iso(mondayOf(parseIso(ref))) });
      else if (v === "month") go({ view: "month", month: ref.slice(0, 7) });
      else go({ view: "categories" });
    });
  });

  document.addEventListener("keydown", (ev) => {
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    if (["INPUT", "SELECT", "TEXTAREA"].includes(tag) || ev.altKey || ev.ctrlKey || ev.metaKey) return;
    if (ev.key === "ArrowLeft") step(-1);
    else if (ev.key === "ArrowRight") step(1);
  });

  window.addEventListener("hashchange", render);
  window.addEventListener("beforeunload", () => { if (daySave) daySave.flush(); });

  (async () => {
    await detectBackend();
    try { categories = await api.categories(); } catch (e) { app.textContent = `Could not load: ${e.message}`; return; }
    render();
  })();
})();
