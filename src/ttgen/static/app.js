/* Shared by every page: talking to the server, the top bar, dialogs, toasts.
   Exposed as the global `TT`. No framework, no build step. */
"use strict";

const TT = (() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

  /* Any API call that finds the session gone sends the person to sign in,
     and one that is refused for an owed password change sends them to set it.
     Wrapping fetch itself covers the builder's own calls too. */
  const rawFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const r = await rawFetch(input, init);
    const url = typeof input === "string" ? input : input.url;
    const path = url.startsWith("http") ? new URL(url).pathname : url;
    if (path.startsWith("/api/") && !path.startsWith("/api/auth/")) {
      if (r.status === 401) {
        location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search);
      } else if (r.status === 403) {
        const d = await r.clone().json().catch(() => ({}));
        if (/new password/.test(d.detail || "")) location.href = "/login?change=1";
      }
    }
    return r;
  };

  async function api(path, {method = "GET", body} = {}) {
    const init = {method, headers: {}};
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const r = await fetch(path, init);
    const data = await r.json().catch(() => ({detail: "the server sent something unreadable"}));
    if (!r.ok) {
      const msg = typeof data.detail === "string" ? data.detail
        : Array.isArray(data.detail) ? data.detail.map(d => d.msg).join("; ")
        : "something went wrong";
      const err = new Error(msg);
      err.status = r.status;
      throw err;
    }
    return data;
  }

  /* ---------------------------------------------------------- formatting */
  const initials = (name) => String(name || "?").replace(/^(dr|mr|ms|mrs|prof)\.?\s+/i, "")
    .split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join("").toUpperCase() || "?";
  const firstName = (name) => String(name || "").replace(/^(dr|mr|ms|mrs|prof)\.?\s+/i, "")
    .split(/\s+/)[0] || name;
  const date = (ts) => ts ? new Date(ts * 1000).toLocaleDateString(undefined,
    {day: "numeric", month: "short", year: "numeric"}) : "—";
  const dateTime = (ts) => ts ? new Date(ts * 1000).toLocaleString(undefined,
    {day: "numeric", month: "short", hour: "numeric", minute: "2-digit"}) : "never";
  const ROLE = {admin: "Administrator", teacher: "Teacher", student: "Student"};

  /* A stable, gentle colour per subject code, so a subject looks the same in
     every grid and on every device. */
  function colour(key) {
    let h = 0;
    for (const ch of String(key)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    const hue = [212, 262, 330, 18, 152, 190, 42, 286, 0, 170, 230, 96][h % 12];
    return {bg: `hsl(${hue} 85% 96%)`, line: `hsl(${hue} 60% 82%)`, ink: `hsl(${hue} 55% 32%)`};
  }

  /* -------------------------------------------------------------- toast */
  function toast(msg, bad) {
    let el = $("tt-toast");
    if (!el) { el = document.createElement("div"); el.id = "tt-toast"; document.body.append(el); }
    el.textContent = msg;
    el.className = bad ? "bad" : "";
    el.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.hidden = true; }, bad ? 6000 : 3400);
  }

  /* ------------------------------------------------------------- dialog
     dialog({title, text, body, wide, actions:[{label, kind, value, run}]})
     resolves with the clicked action's value (or its run() result); null on
     Escape. A run() that throws keeps the dialog open and shows the error. */
  const KEEP = Symbol("keep open");
  function dialog({title, text = "", body = "", wide = false, actions = [{label: "Close"}],
                   onOpen} = {}) {
    return new Promise((resolve) => {
      const d = document.createElement("dialog");
      d.className = "dlg" + (wide ? " wide" : "");
      d.innerHTML = `<form method="dialog">
        <div class="dh"><h3>${esc(title)}</h3>${text ? `<p>${esc(text)}</p>` : ""}</div>
        <div class="db">${body}<div class="note bad" data-err hidden style="margin-top:12px"></div></div>
        <div class="df">${actions.map((a, i) =>
          `<button class="btn ${a.kind || ""}" value="${i}" type="${/\bprimary\b/.test(a.kind || "") ? "submit" : "button"}">${esc(a.label)}</button>`).join("")}</div>
      </form>`;
      document.body.append(d);
      const form = d.querySelector("form");
      const err = d.querySelector("[data-err]");
      let settled = false;
      const finish = (v) => { if (settled) return; settled = true; d.close(); d.remove(); resolve(v); };
      const act = async (btn) => {
        const a = actions[+btn.value];
        if (!a.run) return finish(a.value ?? null);
        err.hidden = true;
        btn.disabled = true;
        try { const v = await a.run(d); if (v !== KEEP) finish(v); }
        catch (e) { err.textContent = e.message; err.hidden = false; }
        finally { btn.disabled = false; }
      };
      form.addEventListener("submit", (ev) => {
        ev.preventDefault();
        const btn = ev.submitter || d.querySelector('.df [type="submit"]');
        if (btn) act(btn);
      });
      d.querySelectorAll('.df [type="button"]').forEach(b => b.addEventListener("click", () => act(b)));
      d.addEventListener("cancel", (ev) => { ev.preventDefault(); finish(null); });
      d.showModal();
      onOpen && onOpen(d);
      const first = d.querySelector(".db input,.db select");
      if (first) first.focus();
    });
  }

  function changePassword() {
    return dialog({
      title: "Change your password",
      text: "You'll stay signed in here; other devices are signed out.",
      body: `<label class="field"><span>Current password</span>
          <input class="input" type="password" name="cur" autocomplete="current-password" required></label>
        <label class="field"><span>New password</span>
          <input class="input" type="password" name="new" autocomplete="new-password" minlength="8" required>
          <small>At least 8 characters.</small></label>
        <label class="field"><span>New password, again</span>
          <input class="input" type="password" name="again" autocomplete="new-password" required></label>`,
      actions: [{label: "Cancel"}, {label: "Change password", kind: "primary", run: async (d) => {
        const f = d.querySelector("form");
        if (f.new.value !== f.again.value) throw new Error("The two new passwords do not match.");
        await api("/api/auth/password", {method: "POST", body: {current: f.cur.value, new: f.new.value}});
        toast("Password changed.");
        return true;
      }}],
    });
  }

  async function signOut() {
    await api("/api/auth/logout", {method: "POST"}).catch(() => {});
    location.href = "/login";
  }

  /* ------------------------------------------------------------ top bar */
  const MARK = `<svg class="tb-mark" viewBox="0 0 32 32" aria-hidden="true"><defs><linearGradient id="tbg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4f46e5"/><stop offset="1" stop-color="#7c3aed"/></linearGradient></defs><rect width="32" height="32" rx="8" fill="url(#tbg)"/><g fill="#fff"><rect x="7" y="7" width="8" height="8" rx="2" opacity=".55"/><rect x="17" y="7" width="8" height="8" rx="2"/><rect x="7" y="17" width="8" height="8" rx="2"/><rect x="17" y="17" width="8" height="8" rx="2" opacity=".55"/></g></svg>`;

  const NAV = {
    admin: [["build", "/admin", "Build"], ["published", "/admin/published", "Published"],
            ["people", "/admin/people", "People"], ["my", "/my", "Preview"]],
    teacher: [["my", "/my", "My timetable"]],
    student: [["my", "/my", "My timetable"]],
  };

  function topbar(active, status) {
    const u = status.user;
    const el = document.createElement("div");
    el.className = "tb";
    el.innerHTML = `<div class="tb-in">
      <button class="tb-burger" aria-label="Menu"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 7h16M4 12h16M4 17h16"/></svg></button>
      <a class="tb-brand" href="${u.home}">${MARK}<span class="tb-name"><b>${esc(status.product)}</b>
        <span>${esc(status.org)}</span></span></a>
      <nav class="tb-nav">${(NAV[u.role] || []).map(([k, href, label]) =>
        `<a href="${href}" class="${k === active ? "on" : ""}">${label}</a>`).join("")}</nav>
      <div class="tb-sp"></div>
      <div class="tb-user">
        <button class="tb-me" aria-haspopup="menu"><span class="avatar">${esc(initials(u.name))}</span>
          <span class="who">${esc(u.name)}</span>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="m6 9 6 6 6-6"/></svg></button>
        <div class="menu" role="menu" hidden>
          <div class="mhead"><span class="avatar lg">${esc(initials(u.name))}</span>
            <div><b>${esc(u.name)}</b><span>${ROLE[u.role]} · ${esc(u.username)}</span></div></div>
          <button data-act="pw">Change password</button>
          <button data-act="out" class="danger">Sign out</button>
        </div>
      </div></div>`;
    document.body.prepend(el);
    const menu = el.querySelector(".menu");
    el.querySelector(".tb-me").onclick = (ev) => { ev.stopPropagation(); menu.hidden = !menu.hidden; };
    el.querySelector(".tb-burger").onclick = () => el.querySelector(".tb-nav").classList.toggle("open");
    document.addEventListener("click", () => { menu.hidden = true; });
    menu.onclick = (ev) => {
      const act = ev.target.closest("button")?.dataset.act;
      if (act === "pw") changePassword();
      if (act === "out") signOut();
    };
  }

  /* Every signed-in page starts here: who am I, and draw the bar. */
  async function boot(active, roles) {
    let status;
    try { status = await api("/api/auth/status"); }
    catch { document.body.innerHTML = `<div class="loading">The server cannot be reached. Try again in a moment.</div>`; throw new Error("offline"); }
    const u = status.user;
    if (!u) { location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search); throw new Error("signed out"); }
    if (u.must_change) { location.href = "/login?change=1"; throw new Error("password owed"); }
    if (roles && !roles.includes(u.role)) { location.href = u.home; throw new Error("wrong role"); }
    topbar(active, status);
    return status;
  }

  /* A small CSV reader (quoted fields, commas and newlines inside quotes). */
  function parseCSV(text) {
    const rows = [];
    let row = [], cell = "", q = false;
    text = String(text).replace(/^﻿/, "");
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (q) {
        if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
        else if (c === '"') q = false;
        else cell += c;
      } else if (c === '"') q = true;
      else if (c === ",") { row.push(cell); cell = ""; }
      else if (c === "\n" || c === "\r") {
        if (c === "\r" && text[i + 1] === "\n") i++;
        row.push(cell); rows.push(row); row = []; cell = "";
      } else cell += c;
    }
    if (cell || row.length) { row.push(cell); rows.push(row); }
    const nonEmpty = rows.filter(r => r.some(x => x.trim()));
    if (!nonEmpty.length) return [];
    const head = nonEmpty[0].map(h => h.trim().toLowerCase().replace(/\s+/g, "_"));
    return nonEmpty.slice(1).map(r => Object.fromEntries(head.map((h, i) => [h, (r[i] || "").trim()])));
  }

  function csvText(rows, cols) {
    const q = (v) => /[",\n]/.test(String(v ?? "")) ? `"${String(v).replace(/"/g, '""')}"` : String(v ?? "");
    return [cols.join(","), ...rows.map(r => cols.map(c => q(r[c])).join(","))].join("\n") + "\n";
  }

  function download(filename, text, mime = "text/csv") {
    const url = URL.createObjectURL(new Blob([text], {type: mime}));
    const a = Object.assign(document.createElement("a"), {href: url, download: filename});
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return {$, esc, api, boot, toast, dialog, changePassword, signOut, initials, firstName,
          date, dateTime, colour, parseCSV, csvText, download, ROLE, MARK, KEEP};
})();
