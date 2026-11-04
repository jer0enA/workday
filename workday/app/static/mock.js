// In-browser fake backend, used only when the real API is unreachable
// (e.g. previewing the static files with `python -m http.server`).
// Mirrors the JSON shapes of the FastAPI backend.
(function () {
  "use strict";

  const KEY = "workday-mock-v2";
  const DEFAULT_SETTINGS = { weekly_hours: 40, workdays: [0, 1, 2, 3, 4], balance_start: null, office_km: 42 };

  function iso(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }

  function seed() {
    const categories = [
      { id: 1, name: "Work", color: "#2563eb", archived: false, sort_order: 0 },
      { id: 2, name: "Study", color: "#9333ea", archived: false, sort_order: 1 },
      { id: 3, name: "Sick", color: "#dc2626", archived: false, sort_order: 2 },
      { id: 4, name: "Vacation", color: "#16a34a", archived: false, sort_order: 3 },
      { id: 5, name: "Public holiday", color: "#d97706", archived: false, sort_order: 4 },
    ];
    const entries = [];
    let id = 1;
    const today = new Date();
    for (let back = 1; back <= 40; back++) {
      const d = new Date(today.getFullYear(), today.getMonth(), today.getDate() - back);
      const wd = d.getDay();
      if (wd === 0 || wd === 6) continue;
      const date = iso(d);
      if (back === 9) { entries.push({ id: id++, date, category_id: 3, minutes: 480, note: null }); continue; }
      if (back >= 22 && back <= 24) { entries.push({ id: id++, date, category_id: 4, minutes: 480, note: null }); continue; }
      if (wd === 3) {
        entries.push({ id: id++, date, category_id: 1, minutes: 240, note: "Project X" });
        entries.push({ id: id++, date, category_id: 2, minutes: 240, note: "AWS certification" });
      } else if (wd === 5) {
        entries.push({ id: id++, date, category_id: 1, minutes: 360, note: "Project X" });
        entries.push({ id: id++, date, category_id: 1, minutes: 120, note: "Internal meeting" });
      } else {
        entries.push({ id: id++, date, category_id: 1, minutes: 480, note: back % 7 === 1 ? "Project Y" : "Project X" });
      }
    }
    const days = {};
    for (const e of entries) if (e.category_id === 1) days[e.date] = { location: e.date.endsWith("5") ? "remote" : "office", km: e.date.endsWith("5") ? null : 42 };
    return { categories, entries, days, settings: { ...DEFAULT_SETTINGS }, nextId: id, nextCat: 6 };
  }

  let db;
  function load() {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) return JSON.parse(raw);
    } catch (_) { /* storage unavailable */ }
    return seed();
  }
  function save() {
    try { localStorage.setItem(KEY, JSON.stringify(db)); } catch (_) { /* ignore */ }
  }
  db = load();

  const ok = (data) => ({ status: 200, data });
  const bad = (msg) => ({ status: 422, data: { detail: msg } });

  const settingsOut = () => {
    const st = db.settings;
    return { ...st, daily_minutes: st.workdays.length ? Math.round((st.weekly_hours * 60) / st.workdays.length) : 0 };
  };
  const parse = (k) => { const [y, m, d] = k.split("-").map(Number); return new Date(y, m - 1, d); };
  function* range(from, to) { for (let d = parse(from); iso(d) <= to; d.setDate(d.getDate() + 1)) yield new Date(d); }
  const isWork = (d) => db.settings.workdays.includes((d.getDay() + 6) % 7);
  const targetOf = (from, to) => (from > to ? 0 : [...range(from, to)].filter(isWork).length * settingsOut().daily_minutes);

  function contract(from, to, day_totals) {
    const today = iso(new Date());
    const upto = to < today ? to : today;
    const first = db.entries.reduce((m, e) => (!m || e.date < m ? e.date : m), null);
    const since = db.settings.balance_start || first;
    const on = db.settings.weekly_hours > 0;
    const missing = since ? [...range(from > since ? from : since, upto)].filter((d) => isWork(d) && !day_totals[iso(d)]).map(iso) : [];
    let balance = null;
    if (on && since && since <= upto) {
      balance = db.entries.filter((e) => e.date >= since && e.date <= upto).reduce((s, e) => s + e.minutes, 0) - targetOf(since, upto);
    }
    const day_info = {};
    for (const [k, v] of Object.entries(db.days)) if (k >= from && k <= to) day_info[k] = v;
    return {
      target: on ? targetOf(from, to) : 0,
      target_to_date: on ? targetOf(from, upto) : 0,
      logged_to_date: Object.entries(day_totals).filter(([k]) => k <= upto).reduce((s, [, m]) => s + m, 0),
      balance, balance_since: balance == null ? null : since, missing, day_info,
    };
  }

  const dayOut = (date) => ({
    date,
    entries: db.entries.filter((e) => e.date === date),
    location: (db.days[date] || {}).location || null,
    km: (db.days[date] || {}).km ?? null,
  });

  function report(from, to) {
    const rows = db.entries.filter((e) => e.date >= from && e.date <= to);
    const day_totals = {};
    const byCat = new Map();
    let total = 0;
    for (const e of rows) {
      total += e.minutes;
      day_totals[e.date] = (day_totals[e.date] || 0) + e.minutes;
      if (!byCat.has(e.category_id)) {
        const c = db.categories.find((x) => x.id === e.category_id);
        byCat.set(e.category_id, { ...c, total: 0, days: {}, notes: new Map() });
      }
      const c = byCat.get(e.category_id);
      c.total += e.minutes;
      c.days[e.date] = (c.days[e.date] || 0) + e.minutes;
      const nk = e.note || "";
      if (!c.notes.has(nk)) c.notes.set(nk, { note: e.note, total: 0, days: {} });
      const n = c.notes.get(nk);
      n.total += e.minutes;
      n.days[e.date] = (n.days[e.date] || 0) + e.minutes;
    }
    const categories = [...byCat.values()]
      .sort((a, b) => a.sort_order - b.sort_order)
      .map((c) => ({ ...c, notes: [...c.notes.values()].sort((a, b) => b.total - a.total) }));
    return { from, to, total, day_totals, categories, ...contract(from, to, day_totals) };
  }

  function csv(from, to) {
    const cell = (v) => {
      let s = String(v ?? "");
      if (/^[=+\-@]/.test(s)) s = "'" + s;
      return /[;"\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = ["date;category;hours;note;location;km"];
    db.entries
      .filter((e) => e.date >= from && e.date <= to)
      .sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : a.id - b.id))
      .forEach((e) => {
        const c = db.categories.find((x) => x.id === e.category_id);
        const first = db.entries.find((x) => x.date === e.date) === e;
        const info = (first && db.days[e.date]) || {};
        lines.push([e.date, c.name, String(e.minutes / 60).replace(".", ","), e.note || "", info.location || "", info.km == null ? "" : String(info.km).replace(".", ",")].map(cell).join(";"));
      });
    return "﻿" + lines.join("\r\n") + "\r\n";
  }

  function handle(method, path, body) {
    const url = new URL(path, "http://x/");
    const parts = url.pathname.replace(/^\/+/, "").split("/"); // ["api", ...]
    const q = url.searchParams;
    const res = parts[1];

    if (res === "me") return ok({ username: "demo", display_name: "Demo user" });

    if (res === "categories") {
      if (method === "GET") return ok([...db.categories].sort((a, b) => a.sort_order - b.sort_order));
      if (method === "POST") {
        const name = (body.name || "").trim();
        if (!name) return bad("name required");
        if (db.categories.some((c) => c.name.toLowerCase() === name.toLowerCase())) return { status: 409, data: { detail: "exists" } };
        const c = { id: db.nextCat++, name, color: body.color || "#64748b", archived: false, sort_order: db.categories.length };
        db.categories.push(c); save();
        return ok(c);
      }
      if (method === "PATCH") {
        const c = db.categories.find((x) => x.id === Number(parts[2]));
        if (!c) return { status: 404, data: { detail: "not found" } };
        Object.assign(c, body); save();
        return ok(c);
      }
    }

    if (res === "settings") {
      if (method === "PUT") { db.settings = { ...DEFAULT_SETTINGS, ...body }; save(); }
      return ok(settingsOut());
    }

    if (res === "fill" && method === "POST") {
      const filled = [...range(body.from, body.to)].map(iso)
        .filter((k) => isWork(parse(k)) && !db.entries.some((e) => e.date === k));
      for (const k of filled) db.entries.push({ id: db.nextId++, date: k, category_id: body.category_id, minutes: body.minutes, note: body.note || null });
      save();
      return ok({ filled });
    }

    if (res === "day") {
      const date = parts[2];
      if (parts[3] === "previous") {
        const prev = db.entries.filter((e) => e.date < date).reduce((m, e) => (!m || e.date > m ? e.date : m), null);
        return ok(prev ? dayOut(prev) : null);
      }
      if (method === "GET") return ok(dayOut(date));
      if (method === "PUT") {
        const list = Array.isArray(body) ? body : body.entries;
        if (!Array.isArray(body)) {
          if (body.location == null && body.km == null) delete db.days[date];
          else db.days[date] = { location: body.location, km: body.km };
        }
        let sum = 0;
        for (const e of list) {
          if (e.minutes < 15 || e.minutes % 15 !== 0) return bad("minutes must be a positive multiple of 15");
          sum += e.minutes;
        }
        if (sum > 1440) return bad("day total exceeds 24h");
        db.entries = db.entries.filter((e) => e.date !== date);
        const saved = list.map((e) => ({ id: db.nextId++, date, category_id: e.category_id, minutes: e.minutes, note: (e.note || "").trim() || null }));
        db.entries.push(...saved); save();
        return ok(dayOut(date));
      }
    }

    if (res === "notes") {
      const cat = q.get("category_id") ? Number(q.get("category_id")) : null;
      const term = (q.get("q") || "").toLowerCase();
      const stats = new Map();
      for (const e of db.entries) {
        if (!e.note || (cat && e.category_id !== cat)) continue;
        if (term && !e.note.toLowerCase().includes(term)) continue;
        const s = stats.get(e.note) || { note: e.note, last: "", count: 0 };
        s.count++; if (e.date > s.last) s.last = e.date;
        stats.set(e.note, s);
      }
      const list = [...stats.values()].sort((a, b) => (b.last.localeCompare(a.last)) || b.count - a.count);
      return ok(list.slice(0, 10).map((s) => s.note));
    }

    if (res === "report") return ok(report(q.get("from"), q.get("to")));
    if (res === "export.csv") return { status: 200, text: csv(q.get("from"), q.get("to")) };

    return { status: 404, data: { detail: "not found" } };
  }

  window.WorkdayMock = { handle };
})();
