// The lead file (drawer), the email composer, and the review dialog for written emails.
import {
  $, api, at, av, busy, changed, closeModal, confetti, esc, fail, host, I, KIND, leanTxt,
  lightbox, openModal, pill, safeUrl, small, SMALL_NEW, STAGES, STATUS, STEP, store, telHref,
  toast, toLocalInput, fromLocalInput, tomorrowAt, when,
} from "./core.js";

export const drawer = { lead: null };
const D = () => $("#drawer");
const who = (l) => l.person || l.name;

export async function openLead(id) {
  try {
    drawer.lead = await api(`/api/leads/${id}`);
  } catch (e) { return fail(e); }
  renderLead();
  D().classList.add("on");
  $("#scrim").classList.add("on");
}
export function closeLead() {
  drawer.lead = null;
  D().classList.remove("on");
  $("#scrim").classList.remove("on");
}
async function reload() {
  if (drawer.lead) {
    drawer.lead = await api(`/api/leads/${drawer.lead.id}`);
    renderLead();
  }
}
async function patch(body, msg, undoBody) {
  const id = drawer.lead.id;
  drawer.lead = await api(`/api/leads/${id}`, { method: "PATCH", body });
  renderLead();
  changed();
  if (msg) toast(msg, { undo: undoBody ? async () => { await api(`/api/leads/${id}`, { method: "PATCH", body: undoBody }); await reload(); } : null });
}

// Call this lead now: it comes first in call mode.
export function callLead(id) {
  store.set("callFirst", id);
  closeLead();
  closeModal();
  if (location.hash === "#call") changed(); else location.hash = "call";
}

function stepper(l) {
  const i = STAGES.indexOf(l.status);
  return `<div class="stepper" role="group" aria-label="Stage">${STAGES.map((s, j) => `<button class="${i >= 0 && j < i ? "past" : j === i ? "cur" : ""}" data-stage="${s}" title="Move to ${STATUS[s]}">${STATUS[s]}</button>`).join("")}</div>`;
}

function photosSec(l) {
  if (!l.photos.length) {
    const q = encodeURIComponent(`${l.name} ${l.city || ""}`);
    return `<div class="sec"><h4>Listing photos</h4>
      ${l.website ? `<p class="small muted" style="margin:0 0 8px">No photos measured yet.</p><button class="btn sm" data-refresh>${small(I.refresh)} Read their site and measure</button>`
        : `<p class="small muted" style="margin:0 0 8px">No website found. <a href="https://duckduckgo.com/?q=${q}" target="_blank" rel="noopener">Search the web</a>, then paste it here: the app reads it and measures the photos (about 30 s).</p>
           <form class="inline-form" data-form="website"><input class="input" name="website" placeholder="https://their-site.fr" required><button class="btn sm">Read site</button></form>`}</div>`;
  }
  const measured = l.photos.filter((p) => p.lean_deg != null);
  const [best, ...rest] = measured.concat(l.photos.filter((p) => p.lean_deg == null));
  const marked = best.lean_deg >= 1 ? `/api/photos/${best.id}/marked.jpg` : best.src;
  return `<div class="sec"><h4>Their listing photos <span class="dim" style="text-transform:none;letter-spacing:0;font-weight:400">· median lean ${leanTxt(l.lean_median)} on ${l.photos_measured || 0}</span></h4>
    <button class="hero-photo" data-zoom="${esc(marked)}" data-cap="${esc(leanTxt(best.lean_deg))}${best.lean_deg >= 1 ? " · as attached to your email" : " · straight already"}"><img src="${esc(marked)}" alt="Their photo with the measured lean drawn on it" loading="lazy"></button>
    ${rest.length ? `<div class="thumbs">${rest.slice(0, 8).map((p) => `<button data-zoom="${esc(p.src)}" data-cap="${esc(leanTxt(p.lean_deg))}${p.n_lines ? ` · ${p.n_lines} lines` : ""}"><img src="${esc(p.src)}" alt="" loading="lazy"><b>${leanTxt(p.lean_deg)}</b></button>`).join("")}</div>` : ""}</div>`;
}

function contactSec(l) {
  const row = (k, v, extra, src, bad) => `<span class="k">${k}</span><span class="v ${bad ? "bad" : ""}">${v}${extra}</span><span class="src" title="${esc(src || "")}">${esc(src ? (String(src).startsWith("http") ? host(src) : src) : "")}</span>`;
  const emails = l.emails.map((e) => row("Email", `<span class="mono">${esc(e.value)}</span>`,
    e.problem ? ` <span class="flag">${esc(e.problem)}</span>` : e.value === l.best_email ? ` <span class="tag">best</span>` : "", e.source, !!e.problem)).join("");
  const phones = l.phones.map((p) => row("Phone", `<a class="mono" href="${telHref(p.value)}">${esc(p.value)}</a>`,
    p.problem ? ` <span class="flag">${esc(p.problem)}</span>` : "", p.source, !!p.problem)).join("");
  const people = l.people.map((p) => row("Person", esc(p.name), p.role ? ` <span class="dim">· ${esc(p.role)}</span>` : "", p.source)).join("");
  const site = l.website ? row("Website", `<a href="${esc(safeUrl(l.website))}" target="_blank" rel="noopener">${esc(host(l.website))}</a>`, "", l.website_source) : "";
  const socials = Object.entries(l.socials || {}).map(([k, u]) => row(k[0].toUpperCase() + k.slice(1), `<a href="${esc(safeUrl(u))}" target="_blank" rel="noopener">${esc(host(u))}</a>`, "", "")).join("");
  return `<div class="sec"><h4>Contact</h4><div class="kv">${emails}${phones}${people}${site}${socials}</div>
    ${!l.emails.length && !l.phones.length ? `<p class="small muted" style="margin:0">No email or phone yet.</p>` : ""}
    <form class="inline-form" data-form="contact"><select class="select" name="field" aria-label="What to add" style="flex:0 1 110px"><option value="emails">Email</option><option value="phones">Phone</option><option value="people">Person</option></select><input class="input" name="value" placeholder="Their own address, a mobile, a name" required><button class="btn sm">Add</button></form></div>`;
}

function conversation(l) {
  const msgs = l.messages.filter((m) => m.status !== "cancelled");
  if (!msgs.length) return "";
  const item = (m) => {
    if (m.direction === "in") return { c: "in", i: I.mail, h: `<b>Their reply</b> · ${esc(m.subject)}`, w: when(m.sent_at), b: m.body };
    const label = STEP[m.step] || "Email";
    if (m.status === "sent") return { c: "out", i: I.mail, h: `<b>${label} sent</b> · ${esc(m.subject)}`, w: when(m.sent_at), b: m.body, fold: true };
    if (m.status === "queued") return { c: "sched", i: I.clock, h: `<b>${label}</b> written, waiting for your review`, w: new Date(m.due_at) > new Date() ? `due ${at(m.due_at)}` : "due now", act: `<button class="btn sm" data-review="${m.id}">Review</button>` };
    if (m.status === "approved") return { c: "sched", i: I.send, h: `<b>${label}</b> approved, goes out at the next free slot`, w: at(m.due_at) };
    return { c: "bad", i: I.x, h: `<b>${label} not sent</b> · ${esc(m.error || "")}`, w: at(m.due_at), act: m.error !== "bounced" ? `<button class="btn sm" data-review="${m.id}">Retry</button>` : "" };
  };
  return `<div class="sec"><h4>Emails</h4><div>${msgs.map(item).map((e) => `<div class="ev"><span class="dot ${e.c}">${e.i}</span><div><div class="h">${e.h}</div><div class="w">${e.w}</div>${e.b ? (e.fold ? `<details><summary class="small dim" style="cursor:pointer">Show the email</summary><div class="body">${esc(e.b)}</div></details>` : `<div class="body">${esc(e.b)}</div>`) : ""}</div><div>${e.act || ""}</div></div>`).join("")}</div></div>`;
}

const ACT = { note: "Note", email: "Email", email_in: "Reply", call: "Call", visit: "Visit", interview: "Interview", photos_sent: "Photos sent", status: "Stage" };

function renderLead() {
  const l = drawer.lead;
  if (!l) return;
  const lost = l.status === "lost";
  const sn = l.small_new.map((t) => `<span class="newb">${esc(SMALL_NEW[t] || t)}</span>`).join(" ");
  const snoozed = l.snoozed_until && new Date(l.snoozed_until) > new Date();
  const replied = l.messages.some((m) => m.direction === "in");
  D().innerHTML = `<div class="dr-h">
    <div class="row">${av(l)}<div style="flex:1;min-width:0"><h3>${esc(who(l))}</h3>
      <div class="meta">${l.person ? `${esc(l.name)} · ` : ""}${esc(KIND[l.kind] || l.kind)} · ${esc(l.city || "town unknown")}${l.dep ? ` <span class="tag">${esc(l.dep)}</span>` : ""} ${pill(l.status)} ${l.do_not_contact ? `<span class="flag">do not contact</span>` : ""}</div></div>
      <button class="btn ghost sm" data-close-lead aria-label="Close">${small(I.x)} <kbd>Esc</kbd></button></div>
    ${lost ? `<div class="note bad" style="margin:12px 0">Marked lost. <button class="btn sm" data-stage="contacted">Reopen</button></div>` : stepper(l)}
    <div class="acts-row">
      <button class="btn primary" data-email ${l.do_not_contact ? "disabled" : ""}>${I.mail} ${replied ? "Reply" : "Email"} <kbd>e</kbd></button>
      <button class="btn" data-call ${l.do_not_contact || !l.best_phone ? "disabled" : ""} title="${l.best_phone ? "" : "No phone that reaches them"}">${I.tel} Call <kbd>c</kbd></button>
      <button class="btn" data-snooze>${I.clock} ${snoozed ? `Until ${esc(at(l.snoozed_until))}` : "Snooze"} <kbd>s</kbd></button>
      <button class="btn" data-advance ${lost || l.status === "customer" ? "disabled" : ""}>Next stage <kbd>→</kbd></button>
      ${lost ? "" : `<button class="btn ghost" data-stage="lost">Lost</button>`}
      <button class="btn ghost danger" data-dnc>${l.do_not_contact ? "Allow contact again" : "Do not contact"}</button>
    </div>
    <div class="acts-row" data-snooze-menu hidden style="margin-top:8px;align-items:center"><span class="small muted">Hide from Today until</span>
      <button class="btn sm" data-snooze-to="1">Tomorrow</button><button class="btn sm" data-snooze-to="7">Next week</button><button class="btn sm" data-snooze-to="30">In a month</button>${snoozed ? `<button class="btn sm ghost" data-snooze-to="0">Now</button>` : ""}</div>
  </div>
  <div class="dr-b">
    ${l.next_action ? `<div class="next">${small(I.clock, 16)}<span style="flex:1"><b>${l.next_action === "call" ? "Call them" : esc(l.next_action)}</b> · ${esc(at(l.next_action_at))}</span><button class="btn sm" data-done-action>${small(I.check)} Done</button></div>`
      : `<details><summary class="small dim" style="cursor:pointer">+ Add a reminder</summary><form class="inline-form" data-form="reminder"><input class="input" name="text" placeholder="Send the corrected photos, book the interview…" required><input class="input" type="datetime-local" name="at" value="${toLocalInput(tomorrowAt(9))}" style="flex:0 1 200px" aria-label="When"><button class="btn sm">Save</button></form></details>`}
    ${l.flags.map((f) => `<div class="note bad">${esc(f)}. Find their own${f.startsWith("Phone") ? " number" : " address"}${l.website ? ` on <a href="${esc(safeUrl(l.website))}" target="_blank" rel="noopener">their site</a>` : ""} and add it under Contact.</div>`).join("")}
    ${sn ? `<div class="row" style="gap:6px">${sn}</div>` : ""}
    ${photosSec(l)}
    ${contactSec(l)}
    ${conversation(l)}
    <div class="sec"><h4>Why score ${l.score}</h4><div class="reasons">${(l.score_reasons || []).map((r) => `<span>${esc(r)}</span>`).join("")}</div></div>
    <div class="sec"><h4>Notes <span class="dim" data-saved hidden style="text-transform:none;letter-spacing:0;font-weight:400">saved</span></h4><textarea class="input" data-notes rows="3" placeholder="Saved as you type" aria-label="Notes">${esc(l.notes)}</textarea></div>
    <div class="sec"><h4>Activity</h4>
      <form class="inline-form" data-form="log" style="margin:0 0 10px"><select class="select" name="kind" style="flex:0 1 160px" aria-label="Kind"><option value="note">Note</option><option value="call">Call</option><option value="email">Email sent elsewhere</option><option value="visit">Visit</option><option value="interview">Interview</option><option value="photos_sent">Photos sent</option></select><input class="input" name="text" placeholder="What happened" aria-label="What happened"><button class="btn sm">Log</button></form>
      <div>${l.activities.length ? l.activities.map((a) => `<div class="ev"><span class="dot ${a.kind === "email_in" ? "in" : a.kind === "email" || a.kind === "call" ? "out" : ""}">${a.kind === "call" ? I.tel : a.kind.startsWith("email") ? I.mail : I.note}</span><div><div class="h"><b>${esc(ACT[a.kind] || a.kind)}</b> ${esc(a.text)}</div><div class="w">${esc(at(a.at))}</div></div><button class="x" data-del-act="${a.id}" aria-label="Delete">${small(I.x)}</button></div>`).join("") : `<p class="small muted" style="margin:0">Nothing yet.</p>`}</div></div>
    <div class="sec"><h4>Company</h4><div class="kv">
      ${l.legal_name ? `<span class="k">Legal name</span><span class="v">${esc(l.legal_name)}</span><span></span>` : ""}
      ${l.siren ? `<span class="k">SIREN</span><span class="v"><a href="https://annuaire-entreprises.data.gouv.fr/entreprise/${esc(l.siren)}" target="_blank" rel="noopener" class="mono">${esc(l.siren)}</a></span><span></span>` : ""}
      ${l.headcount ? `<span class="k">Team</span><span class="v">${esc(l.headcount)}</span><span></span>` : ""}
      ${l.founded ? `<span class="k">Since</span><span class="v">${esc(l.founded.slice(0, 4))}</span><span></span>` : ""}
      ${l.address ? `<span class="k">Address</span><span class="v">${esc(l.address)}${l.postcode ? `, ${esc(l.postcode)}` : ""} ${esc(l.city || "")}</span><span></span>` : ""}
      <span class="k">Found on</span><span class="v small">${(l.sources || []).map((s) => s.url ? `<a href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener">${esc(host(s.url) || s.kind)}</a>` : esc(s.kind)).join(", ") || "–"}</span><span></span>
    </div>${l.website ? `<button class="btn sm" data-refresh style="margin-top:10px">${small(I.refresh)} Read their site again</button>` : ""}</div>
  </div>`;
}

let notesTimer;
D().addEventListener("input", (e) => {
  if (!e.target.matches("[data-notes]") || !drawer.lead) return;
  clearTimeout(notesTimer);
  const id = drawer.lead.id, text = e.target.value;
  notesTimer = setTimeout(async () => {
    try {
      await api(`/api/leads/${id}`, { method: "PATCH", body: { notes: text } });
      if (drawer.lead?.id === id) drawer.lead.notes = text;
      const s = $("[data-saved]", D());
      if (s) { s.hidden = false; setTimeout(() => (s.hidden = true), 1200); }
    } catch (err) { fail(err); }
  }, 600);
});

D().addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = e.target, l = drawer.lead, btn = f.querySelector("button");
  const kind = f.dataset.form;
  await busy(btn, async () => {
    if (kind === "contact") drawer.lead = await api(`/api/leads/${l.id}/contacts`, { method: "POST", body: { field: f.field.value, value: f.value.value } });
    else if (kind === "log") drawer.lead = await api(`/api/leads/${l.id}/activities`, { method: "POST", body: { kind: f.kind.value, text: f.text.value } });
    else if (kind === "website") drawer.lead = await api(`/api/leads/${l.id}/website`, { method: "POST", body: { website: f.website.value } });
    else if (kind === "reminder") drawer.lead = await api(`/api/leads/${l.id}`, { method: "PATCH", body: { next_action: f.text.value, next_action_at: fromLocalInput(f.at.value) } });
    renderLead();
    changed();
  });
});

D().addEventListener("click", async (e) => {
  const t = e.target.closest("button, [data-zoom]");
  if (!t || !drawer.lead) return;
  const l = drawer.lead, d = t.dataset;
  try {
    if (d.closeLead !== undefined) closeLead();
    else if (d.email !== undefined) openComposer(l.id);
    else if (d.call !== undefined) callLead(l.id);
    else if (d.zoom) lightbox(d.zoom, d.cap);
    else if (d.stage) await moveTo(d.stage);
    else if (d.advance !== undefined) await advance();
    else if (d.snooze !== undefined) { const m = D().querySelector("[data-snooze-menu]"); m.hidden = !m.hidden; }
    else if (d.snoozeTo !== undefined) {
      const days = +d.snoozeTo;
      const until = days ? tomorrowAt(8, 0, days).toISOString() : "";
      await patch({ snoozed_until: until }, days ? `${who(l)} hidden until ${at(until)}` : "Back in Today", { snoozed_until: l.snoozed_until || "" });
    } else if (d.dnc !== undefined) {
      if (!l.do_not_contact && d.confirm !== "1") { d.confirm = "1"; t.textContent = "Confirm: never contact them"; return; }
      await patch({ do_not_contact: !l.do_not_contact }, l.do_not_contact ? "Contact allowed again" : `${who(l)} won't be contacted. Pending emails cancelled.`);
    } else if (d.doneAction !== undefined) await patch({ clear_next_action: true }, "Done", { next_action: l.next_action, next_action_at: l.next_action_at });
    else if (d.refresh !== undefined) await busy(t, async () => { drawer.lead = await api(`/api/leads/${l.id}/refresh`, { method: "POST" }); renderLead(); changed(); toast("Their site was read again"); });
    else if (d.review) openReview(+d.review);
    else if (d.delAct) { await api(`/api/activities/${d.delAct}`, { method: "DELETE" }); await reload(); }
  } catch (err) { fail(err); }
});

export async function moveTo(status) {
  const l = drawer.lead;
  const prev = l.status;
  if (prev === status) return;
  await patch({ status }, `${who(l)} → ${STATUS[status]}`, { status: prev });
  if (status === "pilot" || status === "customer") confetti();
}
async function advance() {
  const l = drawer.lead;
  if (l.status === "lost" || l.status === "customer") return;
  const i = STAGES.indexOf(l.status);
  await moveTo(STAGES[i + 1] || "contacted");
}
export function leadKey(e) {
  if (!drawer.lead) return false;
  const k = e.key;
  if (k === "e" && !drawer.lead.do_not_contact) { openComposer(drawer.lead.id); return true; }
  if (k === "c" && drawer.lead.best_phone) { callLead(drawer.lead.id); return true; }
  if (k === "s") { D().querySelector("[data-snooze]")?.click(); return true; }
  if (k === "ArrowRight") { advance().catch(fail); return true; }
  return false;
}

// --- composer ---------------------------------------------------------------------------

const MODE = { first: "First email", followup: "Follow-up", reply: "Reply to their message" };

export async function openComposer(leadId, templateId = null) {
  let c;
  try {
    c = await api(`/api/compose?lead_id=${leadId}${templateId ? `&template_id=${templateId}` : ""}`);
  } catch (e) { return fail(e); }
  const l = c.lead, mb = c.mailbox;
  const canSend = mb.configured;
  const noAddr = !c.to_options.length;
  const opts = [...c.usable, ...c.to_options.filter((a) => !c.usable.includes(a))];
  const steps = c.mode === "reply" ? [-1] : c.mode === "followup" ? [1, 2, 0] : [0];
  const tpls = c.templates.filter((t) => steps.includes(t.step));
  const sheet = openModal(`<div class="composer">
    <div class="cp-l">
      <div class="row" style="flex-wrap:nowrap">${av(l)}<div style="flex:1;min-width:0"><b>${esc(who(l))}</b><div class="small muted">${l.person ? esc(l.name) + " · " : ""}${esc(l.city || "")} · ${MODE[c.mode]}</div></div><button class="btn ghost sm" data-close aria-label="Close">${small(I.x)}</button></div>
      ${c.last_in ? `<div class="quote"><b>They wrote</b> (${esc(when(c.last_in.at))}):\n${esc(c.last_in.body)}</div>` : ""}
      ${tpls.length > 1 ? `<div class="tpls" role="group" aria-label="Template">${tpls.map((t) => `<button aria-pressed="${t.id === c.template_id}" data-tpl="${t.id}">${esc(t.name)}</button>`).join("")}</div>` : ""}
      <div class="field"><span class="k">To</span>${noAddr ? `<span class="flag">No address yet. Add one in the lead file.</span>` : `<select id="cp-to" aria-label="To">${opts.map((a) => `<option value="${esc(a)}" ${a === c.to ? "selected" : ""}>${esc(a)}${c.usable.includes(a) ? "" : " (not theirs)"}</option>`).join("")}</select>`}</div>
      <div class="field"><span class="k">From</span><span>${esc(mb.sender || "your mailbox (not connected yet)")}</span></div>
      <div class="field"><span class="k">Subject</span><input id="cp-subject" value="${esc(c.subject)}" aria-label="Subject"></div>
      <textarea class="input mailbody" id="cp-body" aria-label="Message" lang="fr" autofocus>${esc(c.body)}</textarea>
      <div class="optout">--\n${esc(c.footer)}</div>
    </div>
    <div class="cp-r">
      ${c.photo ? `<h4>Attached: their photo, marked</h4><img src="${esc(c.photo.src)}" alt="Their photo with the lean drawn on it"><label class="check"><input type="checkbox" class="chk" id="cp-attach" ${c.attach ? "checked" : ""}> Attach it (verticals lean ${leanTxt(c.photo.lean)})</label>`
        : `<h4>No photo to attach</h4><p class="small muted" style="margin:0">None of their photos is measured. The email works without one.</p>`}
      ${c.mode === "first" ? `<h4 style="margin-top:6px">Then, if they don't reply</h4>
        <div class="seq">${c.followups.map((f, i) => `<div><span class="d">Day ${esc(f.days)}</span><span>Follow-up ${i + 1}</span><span class="tag">you review it</span></div>`).join("")}</div>
        <label class="check"><input type="checkbox" class="chk" id="cp-fu" checked> Write these follow-ups</label>
        <p class="small dim" style="margin:0">Each waits in Today on its day until you approve it. A reply cancels them.</p>` : ""}
      ${!canSend ? `<div class="note warn">Your mailbox isn't connected yet, so the app can't send this. Use "Copy and log as sent" to send it from your mail app. <a href="#settings" data-close>Connect it</a></div>` : mb.dry_run ? `<div class="note warn">Test mode: the email is saved in data/outbox, not sent.</div>` : ""}
    </div>
    <div class="cp-foot"><span class="caps">${mb.sent_today} / ${mb.cap} sent today</span><span class="grow"></span>
      <button class="btn" data-copy ${noAddr ? "disabled" : ""}>${small(I.copy)} Copy and log as sent</button>
      ${canSend ? `<button class="btn" data-queue ${noAddr ? "disabled" : ""} title="Goes out at the next free slot, spaced from your other emails">Send later</button>` : ""}
      <button class="btn primary" data-send ${!canSend || noAddr ? "disabled" : ""}>${small(I.send, 15)} Send <kbd>Ctrl ↵</kbd></button></div>
  </div>`);

  const payload = (whenTo) => ({
    lead_id: l.id, to: sheet.querySelector("#cp-to")?.value || "", subject: sheet.querySelector("#cp-subject").value,
    body: sheet.querySelector("#cp-body").value, template_id: c.template_id,
    attach_photo_id: sheet.querySelector("#cp-attach")?.checked && c.photo ? c.photo.id : null,
    in_reply_to: c.in_reply_to, step: c.step, followups: sheet.querySelector("#cp-fu")?.checked ?? false, when: whenTo,
  });
  sheet.onclick = async (e) => {
    const t = e.target.closest("button, a");
    if (!t) return;
    const d = t.dataset;
    if (d.close !== undefined) { closeModal(); return; }
    if (d.tpl) { openComposer(leadId, +d.tpl); return; }
    if (d.send !== undefined || d.queue !== undefined) {
      const later = d.queue !== undefined;
      await busy(t, async () => {
        const p = payload(later ? "queue" : "now");
        const r = await api("/api/send", { method: "POST", body: p });
        closeModal();
        changed();
        if (drawer.lead?.id === l.id) await reload();
        if (later) toast(`Queued for ${who(l)}: goes out at the next free slot`);
        else if (r.sent_today >= mb.cap) toast(`Sent to ${who(l)}. That's today's cap: the rest goes out tomorrow.`);
        else toast(c.mode === "reply" ? `Reply sent to ${who(l)}` : p.followups && c.mode === "first" ? `Sent to ${who(l)} · follow-ups on day ${c.followups.map((f) => f.days).join(" and ")}` : `Sent to ${who(l)}`);
      });
    }
    if (d.copy !== undefined) {
      const p = payload("now");
      try { await navigator.clipboard.writeText(`${p.subject}\n\n${p.body}\n\n--\n${c.footer}`); } catch { sheet.querySelector("#cp-body").select(); }
      await busy(t, async () => {
        await api(`/api/leads/${l.id}/activities`, { method: "POST", body: { kind: "email", text: `${p.subject} (sent from your mail app to ${p.to})` } });
        closeModal();
        changed();
        if (drawer.lead?.id === l.id) await reload();
        toast(`Copied. Paste it into your mail app, to ${p.to}. Logged as sent.`);
      });
    }
  };
}

// --- review a written email (follow-up, campaign batch, after a voicemail) --------------

export async function openReview(messageId, item = null) {
  let m = item?.message, l = item?.lead;
  if (!m || !l) {
    l = drawer.lead ? await api(`/api/leads/${drawer.lead.id}`).catch(() => null) : null;
    m = l?.messages.find((x) => x.id === messageId);
    if (!m) return fail(new Error("This email isn't waiting any more."));
  }
  const sheet = openModal(`<div class="sheet-h">${av(l)}<h2>${esc(STEP[m.step] || "Email")} to ${esc(who(l))}</h2><button class="btn ghost sm" data-close aria-label="Close">${small(I.x)}</button></div>
    <div class="sheet-b">
      <div class="small muted">${esc(l.name)} · ${esc(l.city || "")} · ${pill(l.status)} ${m.error && m.status === "failed" ? `<span class="flag">${esc(m.error)}</span>` : ""}</div>
      <div class="field"><span class="k">To</span><input id="rv-to" value="${esc(m.to || "")}" aria-label="To"></div>
      <div class="field"><span class="k">Subject</span><input id="rv-subject" value="${esc(m.subject)}" aria-label="Subject"></div>
      <textarea class="input mailbody" id="rv-body" aria-label="Message" lang="fr" autofocus>${esc(m.body)}</textarea>
      ${m.attach_photo_id ? `<div class="row small muted"><img src="/api/photos/${m.attach_photo_id}/marked.jpg" alt="" style="width:120px;border-radius:8px"> Their photo, marked, is attached.</div>` : ""}
      <div class="row"><button class="btn ghost danger" data-cancel-msg>Don't send</button><button class="btn ghost" data-open-lead>Open lead file</button><span class="grow"></span>
        <button class="btn" data-approve>Approve</button><button class="btn primary" data-send-now>${small(I.send, 15)} Send now</button></div>
    </div>`, { narrow: true });
  const save = () => api(`/api/messages/${m.id}`, { method: "PATCH", body: { to: sheet.querySelector("#rv-to").value, subject: sheet.querySelector("#rv-subject").value, body: sheet.querySelector("#rv-body").value } });
  sheet.onclick = async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    const d = t.dataset;
    if (d.close !== undefined) return closeModal();
    if (d.openLead !== undefined) { closeModal(); return openLead(l.id); }
    await busy(t, async () => {
      if (d.cancelMsg !== undefined) {
        await api(`/api/messages/${m.id}`, { method: "DELETE" });
        toast(`Won't be sent to ${who(l)}`);
      } else if (d.approve !== undefined) {
        await save();
        const r = await api("/api/messages/approve", { method: "POST", body: { ids: [m.id] } });
        toast(r.mailbox.configured ? "Approved. It goes out at the next free slot." : "Approved. It goes out once your mailbox is connected.");
      } else if (d.sendNow !== undefined) {
        await save();
        await api(`/api/messages/${m.id}/send`, { method: "POST" });
        toast(`Sent to ${who(l)}`);
      }
      closeModal();
      changed();
      if (drawer.lead?.id === l.id) await reload();
    });
  };
}
