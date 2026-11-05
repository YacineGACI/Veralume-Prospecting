// Veralume Prospects: router, search palette, keyboard, refresh. No build step.
import { $, $$, api, av, bus, closeModal, esc, fail, KIND, modalOpen, pill } from "./core.js";
import { closeLead, drawer, leadKey, openLead } from "./lead.js";
import { applyTheme, calls, campaigns, insights, leads, pilots, pipeline, settingsView, tests, today, ui } from "./views.js";

const VIEWS = { today, leads, call: calls, pipeline, pilots, campaigns, tests, insights, settings: settingsView };
let current = null;
const viewName = () => (VIEWS[location.hash.slice(1)] ? location.hash.slice(1) : "today");

async function route() {
  const name = viewName();
  if (current && current !== VIEWS[name]) current.leave?.();
  current = VIEWS[name];
  $$(".rail a[data-v]").forEach((a) => a.setAttribute("aria-current", a.dataset.v === name ? "page" : "false"));
  try {
    await current.render();
  } catch (e) {
    $("#canvas").innerHTML = `<div class="view"><div class="note bad">Couldn't load this view: ${esc(e.message)}. Is <span class="mono">prospect serve</span> still running?</div></div>`;
  }
  if (name === "today") setBadge(ui.badge);
}

function setBadge(n) {
  const b = $("#badge");
  b.hidden = !n;
  b.textContent = n > 99 ? "99+" : n;
}
async function refreshBadge() {
  if (viewName() === "today") return;
  try {
    const t = await api("/api/today");
    setBadge(t.replies.length + t.ready.length + t.actions.length);
  } catch {}
}

// Something changed: re-render the view (debounced) and the badge.
let pending;
bus.addEventListener("changed", () => {
  clearTimeout(pending);
  pending = setTimeout(() => { route(); refreshBadge(); }, 120);
});

// --- search palette ------------------------------------------------------------------
const CMDS = [
  ["Today", "#today", "g t"], ["Leads", "#leads", "g l"], ["Call mode", "#call", "g c"], ["Pipeline", "#pipeline", "g p"], ["Pilots and their photos", "#pilots", "g o"],
  ["Campaigns and templates", "#campaigns", "g m"], ["A/B tests", "#tests", "g a"], ["Insights", "#insights", "g i"], ["Settings and mailbox", "#settings", "g s"],
];
let palIdx = 0, palItems = [], palTimer;
async function renderPal() {
  const q = $("#pal-in").value.trim();
  const cmds = CMDS.filter(([t]) => t.toLowerCase().includes(q.toLowerCase()));
  let found = [];
  if (q.length >= 2) {
    try { found = (await api(`/api/leads?q=${encodeURIComponent(q)}&limit=8&hide_dnc=false`)).items; } catch {}
  }
  palItems = [...cmds.map((c) => ({ act: c[1], html: `<span class="lbl">${esc(c[0])}</span>${c[2] ? `<kbd>${c[2]}</kbd>` : ""}` })),
    ...found.map((l) => ({ act: `lead:${l.id}`, html: `${av(l)}<span class="lbl">${esc(l.person || l.name)} <span class="dim">· ${esc(l.person ? l.name : KIND[l.kind])} · ${esc(l.city || "")}</span></span>${pill(l.status)}` }))];
  palIdx = Math.min(palIdx, Math.max(palItems.length - 1, 0));
  $("#pal-list").innerHTML = (cmds.length ? `<div class="grp">Go to</div>` : "") +
    palItems.map((it, i) => (i === cmds.length && found.length ? `<div class="grp">Leads</div>` : "") + `<button class="${i === palIdx ? "kb" : ""}" data-act="${it.act}">${it.html}</button>`).join("") +
    (q.length >= 2 && !found.length ? `<div class="grp">No lead matches “${esc(q)}”</div>` : "");
}
function openPal() {
  $("#palette-m").classList.add("on");
  $("#pal-in").value = "";
  palIdx = 0;
  renderPal();
  setTimeout(() => $("#pal-in").focus(), 20);
}
const closePal = () => $("#palette-m").classList.remove("on");
function runAct(a) {
  closePal();
  if (a.startsWith("#")) location.hash = a;
  else if (a.startsWith("lead:")) openLead(+a.slice(5));
}
$("#pal-in").addEventListener("input", () => { palIdx = 0; clearTimeout(palTimer); palTimer = setTimeout(renderPal, 150); });
$("#pal-list").addEventListener("click", (e) => { const b = e.target.closest("[data-act]"); if (b) runAct(b.dataset.act); });
$("#pal-open").addEventListener("click", (e) => { e.preventDefault(); openPal(); });
document.addEventListener("palette", openPal);
$("#palette-m .bg").addEventListener("click", closePal);
$("#modal .bg").addEventListener("click", closeModal);
$("#scrim").addEventListener("click", closeLead);
$("#lightbox").addEventListener("click", (e) => (e.currentTarget.hidden = true));

// --- keyboard --------------------------------------------------------------------------
let gPending = false;
document.addEventListener("keydown", (e) => {
  const mod = e.ctrlKey || e.metaKey;
  if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); openPal(); return; }
  if (!$("#lightbox").hidden) { if (e.key === "Escape") $("#lightbox").hidden = true; return; }
  if ($("#palette-m").classList.contains("on")) {
    if (e.key === "ArrowDown") { palIdx = Math.min(palIdx + 1, palItems.length - 1); e.preventDefault(); renderPal(); }
    else if (e.key === "ArrowUp") { palIdx = Math.max(palIdx - 1, 0); e.preventDefault(); renderPal(); }
    else if (e.key === "Enter" && palItems[palIdx]) runAct(palItems[palIdx].act);
    else if (e.key === "Escape") closePal();
    return;
  }
  if (modalOpen()) {
    if (e.key === "Escape") closeModal();
    if (mod && e.key === "Enter") { const b = $("#sheet [data-send]:not(:disabled), #sheet [data-send-now]"); if (b) { e.preventDefault(); b.click(); } }
    return;
  }
  const typing = e.target.closest("input, textarea, select, [contenteditable]");
  if (typing) { if (e.key === "Escape") e.target.blur(); return; }
  if (e.altKey || mod) return;
  if (e.key === "Escape") { if (drawer.lead) closeLead(); return; }
  if (drawer.lead) { if (leadKey(e)) e.preventDefault(); return; }
  if (gPending) {
    gPending = false;
    const to = { t: "today", l: "leads", c: "call", p: "pipeline", o: "pilots", m: "campaigns", a: "tests", i: "insights", s: "settings" }[e.key];
    if (to) { location.hash = to; e.preventDefault(); }
    return;
  }
  if (e.key === "g") { gPending = true; setTimeout(() => (gPending = false), 900); return; }
  if (current?.key?.(e)) e.preventDefault();
});

// Today refreshes itself every 90 s while visible and idle, so replies show up.
setInterval(() => {
  const idle = document.visibilityState === "visible" && !modalOpen() && !drawer.lead && !document.activeElement?.closest("input, textarea, select");
  if (idle && viewName() === "today") route();
  else refreshBadge();
}, 90_000);

addEventListener("hashchange", () => { closeLead(); closeModal(); closePal(); route(); });
applyTheme();
route().catch(fail);
