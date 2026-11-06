// Shared helpers: API calls, formatting, icons, toast, modal, confetti.

export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export const KIND = { agency: "Agency", independent_agent: "Independent agent", photographer: "Photographer", other: "Other" };
export const KINDS_PL = { photographer: "Photographers", agency: "Agencies", independent_agent: "Independent agents" };
export const STATUS = { new: "New", shortlisted: "Shortlisted", contacted: "Contacted", replied: "Replied", interview: "Interview", pilot: "Pilot", customer: "Customer", lost: "Lost" };
export const STAGES = ["new", "contacted", "replied", "interview", "pilot", "customer"];
export const STEP = { 0: "First email", 1: "Follow-up 1", 2: "Follow-up 2", "-1": "Reply" };
export const SMALL_NEW = { new_business: "new business", sole_trader: "one-person business", recent_profile: "recent profile", few_reviews: "few reviews yet", social_only: "Instagram / Facebook only" };

export async function api(path, opts = {}) {
  const r = await fetch(path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : Array.isArray(j.detail) ? j.detail.map((d) => d.msg).join(", ") : msg;
    } catch {}
    throw new Error(msg);
  }
  return r.json();
}

// Something changed (a send, a status, a call): views and the badge refresh.
export const bus = new EventTarget();
export const changed = () => bus.dispatchEvent(new Event("changed"));

// --- formatting -----------------------------------------------------------------------
export const leanTxt = (v) => (v == null ? "–" : v.toFixed(1).replace(".", ",") + "°");
export const leanCol = (v) => (v == null ? "var(--line2)" : v >= 3 ? "var(--bad)" : v >= 1.5 ? "var(--warn)" : "var(--emerald)");
export const scoreCls = (s) => (s >= 80 ? "hi" : s >= 50 ? "mid" : "lo");
export const pill = (s) => `<span class="pill st-${esc(s)}">${esc(STATUS[s] || s)}</span>`;
const initials = (n) => String(n || "?").split(/[\s'.·-]+/).filter((w) => /\p{L}/u.test(w[0] || "")).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
// The kind of lead, as an icon: a camera, a shop front, a key. With the avatar's shape (a
// circle for a person, a rounded square for an agency) it reads without colour.
const KIND_SVG = {
  photographer: `<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>`,
  agency: `<path d="M4 10l1.5-5h13L20 10M5 10v9h14v-9M4 10h16M10 19v-5h4v5"/>`,
  independent_agent: `<circle cx="8" cy="15" r="4"/><path d="M11 12l8-8M16 7l2 2M14 9l2 2"/>`,
};
export const kindIcon = (k, size = 14) => KIND_SVG[k]
  ? `<svg class="ki k-${k}" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" role="img" aria-label="${esc(KIND[k])}">${KIND_SVG[k]}</svg>` : "";
export const av = (l, cls = "") => `<span class="avatar k-${esc(l.kind)} ${cls}" aria-hidden="true">${esc(initials(l.person || l.name))}${KIND_SVG[l.kind] ? `<span class="kb"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">${KIND_SVG[l.kind]}</svg></span>` : ""}</span>`;

// The user's own tags, loaded once and refreshed when they change.
export const LABELS = { items: [], byId: {}, colors: [] };
export async function loadLabels() {
  try {
    const r = await api("/api/labels");
    Object.assign(LABELS, { items: r.items, colors: r.colors, byId: Object.fromEntries(r.items.map((x) => [x.id, x])) });
  } catch {}
  return LABELS;
}
export const labelChip = (x, extra = "") => `<span class="lab c-${esc(x.color)}"${extra}><i></i>${esc(x.name)}</span>`;
export const labelChips = (l) => (l.labels || []).map((id) => LABELS.byId[id]).filter(Boolean).map((x) => labelChip(x)).join("");

/** Countries, in the order we prospect them; "CA" leads are all in Quebec. */
export const COUNTRY = { FR: "France", BE: "Belgium", LU: "Luxembourg", CH: "Switzerland", DE: "Germany", CA: "Quebec" };
/** A small drawn flag (emoji flags don't show on Windows). */
export const flag = (c) => { c = c || "FR"; return `<span class="cflag cf-${esc(c)}" role="img" aria-label="${esc(COUNTRY[c] || c)}" title="${esc(COUNTRY[c] || c)}"></span>`; };

/** What a lead is, as small chips: facts in grey (network, size, how new), then what they
 * do in colour, at most `max` of those (rarest first). */
export function idChips(l, { max = 2, facts = true } = {}) {
  const id = l.id_ || {};
  const c = l.country || id.country;
  const where = c && c !== "FR" ? [`<span class="idc ctry">${flag(c)} ${esc(COUNTRY[c] || c)}</span>`] : [];
  const f = facts ? (id.facts || []).map((x) => `<span class="idc ${x.k === "network" ? "net" : x.k === "new" ? "new" : ""}">${esc(x.t)}</span>`) : [];
  const sp = (id.specialties || []).slice(0, max).map((x) => `<span class="idc sp sp-${esc(x.tag)}">${esc(x.t)}</span>`);
  return [...where, ...f, ...sp].join("");
}
/** Create or edit one of the user's tags: a name and a colour. */
export function labelEditor(lab, onDone) {
  let color = lab?.color || LABELS.colors[(LABELS.items.length + 3) % Math.max(LABELS.colors.length, 1)] || "blue";
  const sheet = openModal(`<div class="sheet-h"><h2>${lab ? "Edit the tag" : "New tag"}</h2><button class="btn ghost sm" data-close aria-label="Close">✕</button></div>
    <form class="sheet-b"><label>Name<input class="input" name="name" value="${esc(lab?.name || "")}" maxlength="60" placeholder="e.g. VIP, Met at the fair, Call first" autofocus required></label>
    <div><div class="small muted" style="margin-bottom:6px">Colour</div><div class="swatches">${LABELS.colors.map((c) => `<button type="button" class="c-${c}" data-color="${c}" aria-label="${c}" aria-pressed="${c === color}"></button>`).join("")}</div></div>
    <div class="row"><span class="grow"></span><button class="btn" type="button" data-close>Cancel</button><button class="btn primary">${lab ? "Save" : "Create tag"}</button></div></form>`, { narrow: true });
  sheet.onclick = (e) => {
    if (e.target.closest("[data-close]")) return closeModal();
    const b = e.target.closest("[data-color]");
    if (b) { color = b.dataset.color; sheet.querySelectorAll("[data-color]").forEach((x) => x.setAttribute("aria-pressed", x === b)); }
  };
  sheet.onsubmit = async (e) => {
    e.preventDefault();
    try {
      const body = { name: e.target.name.value, color };
      const r = await api(lab ? `/api/labels/${lab.id}` : "/api/labels", { method: lab ? "PATCH" : "POST", body });
      await loadLabels();
      closeModal();
      toast(lab ? "Tag saved" : `Tag “${r.name}” created`);
      onDone?.(r);
    } catch (err) { toast(err.message, { err: true }); }
  };
}

/** The list of the user's tags, to rename, recolour or delete. */
export function manageLabels(onDone) {
  const draw = () => `<div class="sheet-h"><h2>Your tags</h2><button class="btn ghost sm" data-close aria-label="Close">✕</button></div>
    <div class="sheet-b">${LABELS.items.map((x) => `<div class="row" style="gap:10px">${labelChip(x)}<span class="small muted">${x.n} lead${x.n === 1 ? "" : "s"}</span><span class="grow"></span>
      <button class="btn sm" data-edit="${x.id}">Rename or recolour</button><button class="btn sm ghost danger" data-del="${x.id}">Delete</button></div>`).join("") || `<p class="muted">No tags yet.</p>`}</div>`;
  const sheet = openModal(draw(), { narrow: true });
  sheet.onclick = async (e) => {
    if (e.target.closest("[data-close]")) { closeModal(); return onDone?.(); }
    const ed = e.target.closest("[data-edit]"), del = e.target.closest("[data-del]");
    if (ed) return labelEditor(LABELS.byId[+ed.dataset.edit], onDone);
    if (del) {
      const x = LABELS.byId[+del.dataset.del];
      if (del.dataset.confirm !== "1") { del.dataset.confirm = "1"; del.textContent = x.n ? `Delete, untag ${x.n}` : "Click again to delete"; return; }
      await api(`/api/labels/${x.id}`, { method: "DELETE" });
      await loadLabels();
      sheet.innerHTML = draw();
      toast(`Tag “${x.name}” deleted`);
    }
  };
}

export const leanChip = (v) => (v == null ? "" : `<span class="leanc ${v >= 3 ? "hi" : v >= 1.5 ? "mid" : "lo"}" title="How much their photos lean">${leanTxt(v)}</span>`);
export const who = (l) => l.person || l.name;
export const firstName = (l) => {
  const p = (l.person || "").split(/\s+/);
  return p.length >= 2 && /^\p{Lu}\p{Ll}+/u.test(p[0]) ? p[0] : "";
};
const DAY = 864e5;
export function when(iso) {
  if (!iso) return "";
  const d = new Date(iso), now = new Date();
  const diff = now - d;
  if (diff >= 0 && diff < 60e3) return "just now";
  if (diff >= 0 && diff < 3600e3) return `${Math.round(diff / 60e3)} min ago`;
  if (diff >= 0 && d.toDateString() === now.toDateString()) return `${Math.round(diff / 3600e3)} h ago`;
  if (diff >= 0 && new Date(now - DAY).toDateString() === d.toDateString()) return "yesterday";
  if (diff >= 0 && diff < 7 * DAY) return `${Math.ceil(diff / DAY)} d ago`;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}
export function at(iso) {
  if (!iso) return "";
  const d = new Date(iso), now = new Date();
  const t = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return `today ${t}`;
  if (d.toDateString() === new Date(+now + DAY).toDateString()) return `tomorrow ${t}`;
  return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" }) + ` ${t}`;
}
export const dayName = () => new Date().toLocaleDateString("en-GB", { weekday: "long" });
// <input type="datetime-local"> value <-> ISO
export const toLocalInput = (d) => new Date(d - d.getTimezoneOffset() * 60e3).toISOString().slice(0, 16);
export const fromLocalInput = (v) => (v ? new Date(v).toISOString() : null);
export function tomorrowAt(h, m = 0, days = 1) {
  const d = new Date(Date.now() + days * DAY);
  d.setHours(h, m, 0, 0);
  return d;
}
export const telHref = (p) => "tel:" + String(p || "").replace(/[\s.]/g, "").replace(/^0/, "+33");
export const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u || ""; } };
export const safeUrl = (u) => (/^https?:\/\//i.test(u || "") ? u : "#");

// --- icons ------------------------------------------------------------------------------
const svg = (d) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
export const I = {
  mail: svg('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/>'),
  tel: svg('<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>'),
  web: svg('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.5 3 14.5 0 18M12 3c-3 3.5-3 14.5 0 18"/>'),
  clock: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
  note: svg('<path d="M5 7h14M5 12h14M5 17h8"/>'),
  arrow: svg('<path d="M5 12h14M13 6l6 6-6 6"/>'),
  send: svg('<path d="M4 12l16-8-6 16-3-6z"/>'),
  search: svg('<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>'),
  check: svg('<path d="M5 12l5 5 9-10"/>'),
  x: svg('<path d="M6 6l12 12M18 6L6 18"/>'),
  copy: svg('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>'),
  refresh: svg('<path d="M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7"/>'),
  plus: svg('<path d="M12 5v14M5 12h14"/>'),
  spark: svg('<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"/>'),
};
export const small = (icon, px = 13) => icon.replace("<svg", `<svg width="${px}" height="${px}" style="vertical-align:-2px;flex:none"`);

export function ring(cls, v, max) {
  const r = 24, c = 2 * Math.PI * r, off = c * (1 - Math.min(v / Math.max(max, 1), 1));
  return `<svg class="ring ${cls}" viewBox="0 0 58 58" aria-hidden="true"><circle class="track" cx="29" cy="29" r="${r}"/><circle class="val" cx="29" cy="29" r="${r}" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"/></svg>`;
}

// --- toast with undo --------------------------------------------------------------------
let toastTimer, undoFn = null;
export function toast(text, { undo = null, err = false, ms = 3800 } = {}) {
  undoFn = undo;
  $("#toast-t").textContent = text;
  $("#toast-u").hidden = !undo;
  $("#toast").classList.toggle("err", err);
  $("#toast").classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("#toast").classList.remove("on"); undoFn = null; }, err ? 7000 : ms);
}
export const fail = (e) => toast(e.message || String(e), { err: true });
$("#toast-u").addEventListener("click", async () => {
  const f = undoFn;
  undoFn = null;
  $("#toast").classList.remove("on");
  if (f) { try { await f(); changed(); } catch (e) { fail(e); } }
});

// Run an action with the button disabled while it works. Errors become a red toast.
export async function busy(btn, fn) {
  const html = btn?.innerHTML;
  if (btn) { btn.disabled = true; btn.textContent = "Working…"; }
  try { return await fn(); } catch (e) { fail(e); } finally { if (btn && btn.isConnected) { btn.disabled = false; btn.innerHTML = html; } }
}

// --- modal ----------------------------------------------------------------------------
export function openModal(html, { narrow = false, cls = "" } = {}) {
  const sheet = $("#sheet");
  sheet.onclick = sheet.oninput = sheet.onchange = sheet.onsubmit = null;
  sheet.className = `sheet ${narrow ? "narrow" : ""} ${cls}`;
  sheet.innerHTML = html;
  $("#modal").classList.add("on");
  setTimeout(() => $("[autofocus], textarea, input:not([type=checkbox])", sheet)?.focus(), 30);
  return sheet;
}
export function closeModal() {
  $("#modal").classList.remove("on");
}
export const modalOpen = () => $("#modal").classList.contains("on");

export function lightbox(src, cap = "") {
  const lb = $("#lightbox");
  $("img", lb).src = src;
  $("p", lb).textContent = cap;
  lb.hidden = false;
}

export function confetti() {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const c = $("#confetti"), x = c.getContext("2d");
  c.width = innerWidth; c.height = innerHeight;
  const cols = ["#20D47A", "#0E9F63", "#C4F5D8", "#F3B64E", "#7DB7FF"];
  const ps = Array.from({ length: 130 }, () => ({ x: innerWidth / 2, y: innerHeight * 0.45, vx: (Math.random() - 0.5) * 15, vy: -Math.random() * 13 - 3, s: 4 + Math.random() * 5, c: cols[(Math.random() * 5) | 0], r: Math.random() * 6 }));
  let f = 0;
  (function tick() {
    x.clearRect(0, 0, c.width, c.height);
    for (const p of ps) { p.vy += 0.38; p.x += p.vx; p.y += p.vy; p.r += 0.2; x.save(); x.translate(p.x, p.y); x.rotate(p.r); x.fillStyle = p.c; x.fillRect(-p.s / 2, -p.s / 4, p.s, p.s / 2); x.restore(); }
    if (++f < 120) requestAnimationFrame(tick); else x.clearRect(0, 0, c.width, c.height);
  })();
}

// Per-viewer conveniences (remembered tab, filters). Never required.
export const store = {
  get(k, d) { try { const v = localStorage.getItem("vp:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("vp:" + k, JSON.stringify(v)); } catch {} },
};

// --- write with AI ---------------------------------------------------------------------
// A prompt bar under a subject + body pair. The AI rewrites what is there (or writes a new
// draft); every change can be undone, and the text stays yours to edit.
const AI_CHIPS = ["Plus court", "Plus chaleureux", "Plus direct", "Plus formel", "Termine par une question simple", "Corrige le français"];
export function aiBar(ai) {
  if (!ai?.current) return `<div class="ai-bar off small muted">${small(I.spark, 14)} Write from a prompt: set up an AI in <a href="#settings" data-close>Settings</a> (free options there).</div>`;
  const p = ai.providers.find((x) => x.key === ai.current);
  return `<div class="ai-bar" role="group" aria-label="Write with AI">
    <div class="ai-in"><span class="ai-ic" aria-hidden="true">${small(I.spark, 15)}</span><input class="ai-prompt" placeholder="Tell the AI what to write or change, e.g. plus court, parle de leurs photos d'intérieur" autocomplete="off" aria-label="Prompt for the AI">
      <button class="btn sm primary" type="button" data-ai="edit" title="Change the email above as you say (Enter)">Rewrite</button><button class="btn sm" type="button" data-ai="new" title="Write a fresh draft from your prompt">New draft</button></div>
    <div class="ai-chips">${AI_CHIPS.map((c) => `<button type="button" class="chip" data-chip="${esc(c)}">${esc(c)}</button>`).join("")}
      <span class="grow"></span><button type="button" class="chip" data-undo hidden>↶ Undo</button><span class="small dim ai-st" title="${esc(p.label)} · ${esc(p.model)}">${esc(p.label)}</span></div></div>`;
}
export function wireAiBar(bar, { subject, body, extra }) {
  if (!bar || bar.classList.contains("off")) return;
  const input = $(".ai-prompt", bar), st = $(".ai-st", bar), undoB = $("[data-undo]", bar);
  const history = [];
  const label = st.textContent;
  const run = async (mode, instruction, btn) => {
    if (bar.dataset.busy) return;
    if (mode === "edit" && !body.value.trim()) mode = "new";
    bar.dataset.busy = "1";
    bar.querySelectorAll("button").forEach((b) => (b.disabled = true));
    const t0 = Date.now();
    const tick = setInterval(() => (st.textContent = `Writing… ${Math.round((Date.now() - t0) / 1000)} s`), 500);
    try {
      const r = await api("/api/ai/write", { method: "POST", body: { ...extra(), mode, instruction, subject: subject.value, body: body.value } });
      history.push({ s: subject.value, b: body.value });
      if (r.subject) subject.value = r.subject;
      body.value = r.body;
      body.dispatchEvent(new Event("input", { bubbles: true }));
      input.value = "";
      undoB.hidden = false;
      st.textContent = `${label} · ${r.seconds} s`;
    } catch (e) { st.textContent = label; fail(e); } finally {
      clearInterval(tick);
      delete bar.dataset.busy;
      bar.querySelectorAll("button").forEach((b) => (b.disabled = false));
    }
  };
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    e.stopPropagation(); // Ctrl+Enter here rewrites; it must not send the email
    run("edit", input.value);
  });
  bar.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    e.stopPropagation();
    if (b.dataset.ai) run(b.dataset.ai, input.value);
    else if (b.dataset.chip) run("edit", b.dataset.chip);
    else if (b.dataset.undo !== undefined) {
      const last = history.pop();
      if (last) { subject.value = last.s; body.value = last.b; body.dispatchEvent(new Event("input", { bubbles: true })); }
      undoB.hidden = !history.length;
    }
  });
}
