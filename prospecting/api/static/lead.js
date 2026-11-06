// The lead workspace (everything about one lead: the conversation, the photos they sent and
// our retouched versions, the pilot's steps, notes), the email composer, and the review
// dialog for written emails.
import {
  $, aiBar, api, at, av, busy, changed, closeModal, confetti, COUNTRY, esc, fail, flag, host, I, idChips, LANG, KIND, LABELS, labelChip, labelEditor, leanTxt, loadLabels,
  lightbox, openModal, pill, safeUrl, small, SMALL_NEW, STAGES, STATUS, STEP, store, telHref,
  toast, toLocalInput, fromLocalInput, tomorrowAt, when, wireAiBar,
} from "./core.js";

export const drawer = { lead: null, files: null, tab: "conv", view: store.get("photoView", "side") };
const D = () => $("#drawer");
const who = (l) => l.person || l.name;
const first = (l) => (l.person || "").split(/\s+/)[0] || who(l);
const size = (n) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`);

async function load(id) {
  const [lead, files] = await Promise.all([api(`/api/leads/${id}`), api(`/api/leads/${id}/files`)]);
  drawer.lead = lead;
  drawer.files = files;
}
// tab: "conv" | "photos" | "notes". Without one, a pilot waiting on retouching opens on photos.
export async function openLead(id, tab = null) {
  try { await load(id); } catch (e) { return fail(e); }
  drawer.tab = tab || (["photos", "retouching"].includes(drawer.lead.pilot_stage) ? "photos" : "conv");
  renderLead();
  D().classList.add("on", "ws");
  $("#scrim").classList.add("on");
}
export function closeLead() {
  drawer.lead = null;
  D().classList.remove("on");
  $("#scrim").classList.remove("on");
}
async function reload() {
  if (drawer.lead) {
    await load(drawer.lead.id);
    renderLead();
  }
}
document.addEventListener("change", (e) => {
  if (!e.target.matches?.("[data-lead-lang]") || !drawer.lead) return;
  const before = drawer.lead.language || "fr";
  patch({ language: e.target.value }, `They'll be written to in ${LANG[e.target.value]}`, { language: before }).catch(fail);
});
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

// --- the pilot (free test) --------------------------------------------------------------

const PILOT_STEPS = ["interested", "photos", "retouching", "sent_back", "feedback"];
const PILOT_HINT = {
  interested: "They said yes", photos: "Their photos are here", retouching: "You're on it",
  sent_back: "Waiting for their opinion", feedback: "They answered",
};
function pilotSec(l, f) {
  if (!l.pilot_stage) {
    return `<div class="pilot off"><span class="pl-k">Free test</span><span class="small muted" style="flex:1">Not started. It starts by itself when they reply with photos.</span>
      <button class="btn sm" data-pilot="interested">${small(I.plus)} They said yes</button></div>`;
  }
  const ended = ["customer", "lost"].includes(l.pilot_stage);
  const cur = PILOT_STEPS.indexOf(l.pilot_stage);
  const c = f.counts;
  const label = (s) => f.stages.find((x) => x.key === s).label;
  const sub = {
    photos: c.received ? `${c.received} photo${c.received > 1 ? "s" : ""}` : "",
    retouching: c.received ? `${c.retouched} of ${c.received} done` : "",
    sent_back: c.sent ? `${c.sent} sent` : "",
  };
  const due = l.pilot_due_at ? l.pilot_due_at.slice(0, 10) : "";
  const late = l.pilot_due_at && new Date(l.pilot_due_at) < new Date() && cur >= 1 && cur <= 2;
  return `<div class="pilot"><span class="pl-k">Free test</span>
    <ol class="pl-steps">${PILOT_STEPS.map((s, i) => `<li class="${ended || i < cur ? "done" : i === cur ? "cur" : ""}"><button data-pilot="${s}" title="Move the test to “${esc(label(s))}”"><span class="pl-dot" aria-hidden="true">${ended || i < cur ? small(I.check, 12) : ""}</span><span><b>${esc(label(s))}</b><span class="small muted">${esc(i === cur ? sub[s] || PILOT_HINT[s] : sub[s] || "")}</span></span></button></li>`).join("")}
      <li class="${ended ? "cur end" : ""}"><span class="pl-dot" aria-hidden="true">${l.pilot_stage === "customer" ? small(I.check, 12) : ""}</span><span><b>${l.pilot_stage === "customer" ? "Customer" : l.pilot_stage === "lost" ? "Not now" : "Decision"}</b>
        ${ended ? `<button class="linkish small" data-pilot="feedback">reopen</button>` : `<span class="pl-end"><button class="btn sm" data-pilot="customer">Customer</button><button class="btn sm ghost" data-pilot="lost">Not now</button></span>`}</span></li></ol>
    <label class="pl-due ${late ? "late" : ""}">Promised for<input type="date" class="input" data-due value="${esc(due)}"></label>
  </div>`;
}

// --- conversation -----------------------------------------------------------------------

function attThumbs(m, f) {
  const out = [];
  if (m.attach_photo_id) out.push(`<button class="att" data-zoom="/api/photos/${m.attach_photo_id}/marked.jpg" data-cap="Their photo, marked"><img src="/api/photos/${m.attach_photo_id}/marked.jpg" alt="Their photo, marked" loading="lazy"></button>`);
  for (const id of m.asset_ids || []) out.push(`<button class="att wide" data-zoom="/api/assets/${id}/file" data-cap="Before/after example"><img src="/api/assets/${id}/thumb" alt="Before/after example" loading="lazy"></button>`);
  const byId = Object.fromEntries(f.files.map((x) => [x.id, x]));
  const files = m.direction === "in" ? f.files.filter((x) => x.message_id === m.id) : (m.file_ids || []).map((id) => byId[id]).filter(Boolean);
  for (const x of files) out.push(`<button class="att" data-zoom="${x.src}" data-cap="${esc(x.filename)}"><img src="${x.thumb}" alt="${esc(x.filename)}" loading="lazy"></button>`);
  return { html: out.length ? `<div class="atts">${out.join("")}</div>` : "", n: files.length };
}

function conversation(l, f) {
  const msgs = l.messages.filter((m) => m.status !== "cancelled").slice().reverse();
  const unsent = f.files.filter((x) => x.kind === "retouched" && !x.sent_at).length;
  const replied = l.messages.some((m) => m.direction === "in");
  const card = (m) => {
    const label = STEP[m.step] || "Email";
    if (m.direction === "in") {
      const a = attThumbs(m, f);
      return `<article class="msg in"><div class="mh"><b>${esc(who(l))}</b><span>replied</span><span class="grow"></span><span>${esc(at(m.sent_at))}</span></div>
        ${m.subject ? `<div class="ms">${esc(m.subject)}</div>` : ""}<div class="mb">${esc(m.body)}</div>${a.html}
        ${a.n ? `<div class="mf">${small(I.check)} ${a.n} photo${a.n > 1 ? "s" : ""} saved · <button class="linkish" data-tab="photos">Open</button> · <a href="/api/leads/${l.id}/files.zip">Download all (.zip)</a></div>` : ""}
        ${(m.links || []).map((u) => `<div class="mf link">${small(I.web)} They sent a link: <a href="${esc(safeUrl(u))}" target="_blank" rel="noopener">${esc(host(u))}</a>. Download the photos from it, then add them in <button class="linkish" data-tab="photos">Their photos</button>.</div>`).join("")}</article>`;
    }
    if (m.status === "sent") {
      const a = attThumbs(m, f);
      return `<article class="msg out"><div class="mh"><b>You</b><span>→ ${esc(m.to || "")}</span><span class="tag">${esc(label)}</span><span class="grow"></span><span>${esc(at(m.sent_at))}</span></div>
        <div class="ms">${esc(m.subject)}</div><details class="mb-fold"><summary>${esc(m.body.replace(/\s+/g, " ").slice(0, 160))}…</summary><div class="mb">${esc(m.body)}</div></details>${a.html}</article>`;
    }
    if (m.status === "queued") return `<div class="msg sched"><span>${small(I.clock)} <b>${esc(label)}</b> written, waiting for your review · ${new Date(m.due_at) > new Date() ? `due ${esc(at(m.due_at))}` : "due now"}</span><button class="btn sm" data-review="${m.id}">Review</button></div>`;
    if (m.status === "approved") return `<div class="msg sched"><span>${small(I.send)} <b>${esc(label)}</b> approved, goes out at the next free slot</span></div>`;
    return `<div class="msg sched bad"><span>${small(I.x)} <b>${esc(label)} not sent</b> · ${esc(m.error || "")}</span>${m.error !== "bounced" ? `<button class="btn sm" data-review="${m.id}">Retry</button>` : ""}</div>`;
  };
  return `<div class="conv">${msgs.length ? msgs.map(card).join("") : `<p class="muted small">No email yet with ${esc(who(l))}.</p>`}
    <div class="conv-act">${unsent ? `<button class="btn primary" data-send-back>${small(I.send, 15)} Send ${unsent} retouched photo${unsent > 1 ? "s" : ""} back</button>` : ""}
      <button class="btn ${unsent ? "" : "primary"}" data-email ${l.do_not_contact ? "disabled" : ""}>${I.mail} ${replied ? `Reply to ${esc(first(l))}` : `Email ${esc(first(l))}`} <kbd>e</kbd></button></div></div>`;
}

// --- their photos -----------------------------------------------------------------------

function photosTab(l, f) {
  const rec = f.files.filter((x) => x.kind === "received");
  const ret = f.files.filter((x) => x.kind === "retouched");
  const byOrig = {};
  for (const r of ret) if (r.original_id) byOrig[r.original_id] = r;
  const loose = ret.filter((r) => !r.original_id || !rec.some((x) => x.id === r.original_id));
  const v = drawer.view;
  const unsent = ret.filter((x) => !x.sent_at);
  const done = rec.filter((x) => byOrig[x.id]).length;
  const img = (x, alt) => `<button class="ph-img" data-zoom="${x.src}" data-cap="${esc(alt)}"><img src="${x.thumb}" alt="${esc(alt)}" loading="lazy" onerror="this.parentNode.classList.add('none')"><span class="ph-none">No preview · ${esc(x.filename.split(".").pop().toUpperCase())}</span></button>`;
  const tile = (x) => {
    const r = byOrig[x.id];
    const pic = v === "after" ? (r ? img(r, `${r.filename} (retouched)`) : `<div class="ph-todo">Not retouched yet<span class="small">drop the file above</span></div>`)
      : v === "side" && r ? `<div class="ph-pair">${img(x, `${x.filename} (theirs)`)}${img(r, `${r.filename} (retouched)`)}</div>`
      : img(x, x.filename);
    const st = r ? (r.sent_at ? ["Sent back", "ok"] : ["Retouched", "ok"]) : ["To do", "todo"];
    return `<figure class="ph">${pic}<figcaption><span class="mono fn" title="${esc(x.filename)}">${esc(x.filename)}</span><span class="chip-s ${st[1]}">${st[0]}</span>
      <a href="${x.src}?download=true" aria-label="Download ${esc(x.filename)}" title="Download">↓</a><button class="x" data-del-file="${x.id}" aria-label="Delete ${esc(x.filename)}" title="Delete">${small(I.x)}</button></figcaption></figure>`;
  };
  const looseTile = (r) => `<figure class="ph">${img(r, r.filename)}<figcaption><span class="mono fn" title="${esc(r.filename)}">${esc(r.filename)}</span><span class="chip-s ${r.sent_at ? "ok" : ""}">${r.sent_at ? "Sent back" : "Retouched"}</span>
    <a href="${r.src}?download=true" aria-label="Download ${esc(r.filename)}">↓</a><button class="x" data-del-file="${r.id}" aria-label="Delete ${esc(r.filename)}">${small(I.x)}</button></figcaption></figure>`;
  const links = f.links.map((k) => `<div class="note info">${small(I.web)} <span>On ${esc(when(k.at))} they sent a link: <a href="${esc(safeUrl(k.url))}" target="_blank" rel="noopener">${esc(host(k.url))}</a>. Download the photos there, then add them below as theirs.</span></div>`).join("");
  const accept = "image/*,.dng,.cr2,.cr3,.nef,.arw,.raf,.heic,.tif,.tiff";
  return `<div class="photos">
    ${links}
    <div class="ph-bar"><div style="flex:1;min-width:200px"><h3>${rec.length ? `${rec.length} photo${rec.length > 1 ? "s" : ""} from ${esc(first(l))}` : "No photos yet"}</h3>
      <div class="small muted">${rec.length ? `${done} retouched, ${rec.length - done} to do${unsent.length ? ` · ${unsent.length} not sent back yet` : ""}` : "Photos attached to their emails land here by themselves."}</div></div>
      ${rec.length ? `<div class="seg" role="group" aria-label="Show"><button data-view="before" aria-pressed="${v === "before"}">Theirs</button><button data-view="after" aria-pressed="${v === "after"}">Retouched</button><button data-view="side" aria-pressed="${v === "side"}">Side by side</button></div>
      <a class="btn sm" href="/api/leads/${l.id}/files.zip" download>Their photos (.zip)</a>` : ""}
      ${ret.length ? `<a class="btn sm" href="/api/leads/${l.id}/files.zip?kind=retouched" download>Retouched (.zip)</a>` : ""}</div>
    <label class="drop"><input type="file" multiple accept="${accept}" data-upload="retouched" hidden>
      ${small(I.arrow, 18)}<span><b>Drop your retouched photos here</b>, or click to choose. They're matched to their originals by file name.</span></label>
    ${rec.length ? `<div class="ph-grid">${rec.map(tile).join("")}</div>` : ""}
    ${loose.length ? `<h4 class="ph-h">Retouched, not matched to one of their photos</h4><div class="ph-grid">${loose.map(looseTile).join("")}</div>` : ""}
    <p class="small muted" style="margin:0">Photos they sent another way (WeTransfer, Drive, USB key)? <label class="linkish">Add them as theirs<input type="file" multiple accept="${accept}" data-upload="received" hidden></label>.</p>
    ${unsent.length ? `<div class="ph-send"><span>${unsent.length} retouched photo${unsent.length > 1 ? "s" : ""} ready. The email goes into your conversation with ${esc(first(l))}, photos attached.</span><button class="btn primary" data-send-back>${small(I.send, 15)} Send them back to ${esc(first(l))}</button></div>` : ""}
  </div>`;
}

async function upload(files, kind) {
  const l = drawer.lead;
  if (!files.length) return;
  let ok = 0;
  for (const [i, file] of [...files].entries()) {
    toast(`Adding ${i + 1} of ${files.length}: ${file.name}`, { ms: 60000 });
    const r = await fetch(`/api/leads/${l.id}/files?kind=${kind}&name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
    if (!r.ok) { const j = await r.json().catch(() => ({})); fail(new Error(`${file.name}: ${j.detail || r.statusText}`)); continue; }
    ok++;
  }
  await reload();
  changed();
  const unmatched = kind === "retouched" ? drawer.files.files.filter((x) => x.kind === "retouched" && !x.original_id).length : 0;
  if (ok) toast(`${ok} photo${ok > 1 ? "s" : ""} added${unmatched ? ` · ${unmatched} not matched to one of theirs (different file name)` : ""}`);
}

// --- notes and activity -------------------------------------------------------------------

const ACT = { note: "Note", email: "Email", email_in: "Reply", call: "Call", visit: "Visit", interview: "Interview", photos_sent: "Photos", status: "Stage" };
function notesTab(l) {
  return `<div class="sec"><h4>Notes <span class="dim" data-saved hidden style="text-transform:none;letter-spacing:0;font-weight:400">saved</span></h4><textarea class="input" data-notes rows="4" placeholder="What they shoot, their tools, their price, what they said. Saved as you type." aria-label="Notes">${esc(l.notes)}</textarea></div>
    <div class="sec"><h4>Activity</h4>
      <form class="inline-form" data-form="log" style="margin:0 0 10px"><select class="select" name="kind" style="flex:0 1 160px" aria-label="Kind"><option value="note">Note</option><option value="call">Call</option><option value="email">Email sent elsewhere</option><option value="visit">Visit</option><option value="interview">Interview</option><option value="photos_sent">Photos</option></select><input class="input" name="text" placeholder="What happened" aria-label="What happened"><button class="btn sm">Log</button></form>
      <div>${l.activities.length ? l.activities.map((a) => `<div class="ev"><span class="dot ${a.kind === "email_in" ? "in" : a.kind === "email" || a.kind === "call" ? "out" : ""}">${a.kind === "call" ? I.tel : a.kind.startsWith("email") ? I.mail : I.note}</span><div><div class="h"><b>${esc(ACT[a.kind] || a.kind)}</b> ${esc(a.text)}</div><div class="w">${esc(at(a.at))}</div></div><button class="x" data-del-act="${a.id}" aria-label="Delete">${small(I.x)}</button></div>`).join("") : `<p class="small muted" style="margin:0">Nothing yet.</p>`}</div></div>`;
}

// --- side panel -------------------------------------------------------------------------

function photosSec(l) {
  if (!l.photos.length && l.lean_median != null) {
    return `<div class="sec"><h4>Their website photos</h4><p class="small" style="margin:0">Verticals lean <b>${leanTxt(l.lean_median)}</b> on average, measured on ${l.photos_measured ? `${l.photos_measured} photo${l.photos_measured > 1 ? "s" : ""}` : "their photos"}. The photos themselves weren't kept.</p></div>`;
  }
  if (!l.photos.length) {
    const q = encodeURIComponent(`${l.name} ${l.city || ""}`);
    return `<div class="sec"><h4>Their website photos</h4>
      ${l.website ? `<p class="small muted" style="margin:0 0 8px">No photos measured yet.</p><button class="btn sm" data-refresh>${small(I.refresh)} Read their site again</button>`
        : `<p class="small muted" style="margin:0 0 8px">No website found. <a href="https://duckduckgo.com/?q=${q}" target="_blank" rel="noopener">Search the web</a>, then paste it here: the app reads it and measures the photos (about 30 s).</p>
           <form class="inline-form" data-form="website"><input class="input" name="website" placeholder="https://their-site.fr" required><button class="btn sm">Read site</button></form>`}</div>`;
  }
  const measured = l.photos.filter((p) => p.lean_deg != null);
  const [best, ...rest] = measured.concat(l.photos.filter((p) => p.lean_deg == null));
  const marked = best.lean_deg >= 1 ? `/api/photos/${best.id}/marked.jpg` : best.src;
  return `<div class="sec"><h4>Their website photos <span class="dim" style="text-transform:none;letter-spacing:0;font-weight:400">· lean ${leanTxt(l.lean_median)} on ${l.photos_measured || 0}</span></h4>
    <button class="hero-photo" data-zoom="${esc(marked)}" data-cap="${esc(leanTxt(best.lean_deg))}${best.lean_deg >= 1 ? " · as attached to your email" : " · straight already"}"><img src="${esc(marked)}" alt="Their photo with the measured lean drawn on it" loading="lazy"></button>
    ${rest.length ? `<div class="thumbs">${rest.slice(0, 4).map((p) => `<button data-zoom="${esc(p.src)}" data-cap="${esc(leanTxt(p.lean_deg))}${p.n_lines ? ` · ${p.n_lines} lines` : ""}"><img src="${esc(p.src)}" alt="" loading="lazy"><b>${leanTxt(p.lean_deg)}</b></button>`).join("")}</div>` : ""}</div>`;
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

function companySec(l) {
  return `<details class="sec"><summary class="small dim" style="cursor:pointer">Company details and why score ${l.score}</summary><div class="kv" style="margin-top:8px">
      ${l.legal_name ? `<span class="k">Legal name</span><span class="v">${esc(l.legal_name)}</span><span></span>` : ""}
      ${l.siren ? `<span class="k">SIREN</span><span class="v"><a href="https://annuaire-entreprises.data.gouv.fr/entreprise/${esc(l.siren)}" target="_blank" rel="noopener" class="mono">${esc(l.siren)}</a></span><span></span>` : ""}
      ${l.headcount ? `<span class="k">Team</span><span class="v">${esc(l.headcount)}</span><span></span>` : ""}
      ${l.founded ? `<span class="k">Since</span><span class="v">${esc(l.founded.slice(0, 4))}</span><span></span>` : ""}
      ${l.address ? `<span class="k">Address</span><span class="v">${esc(l.address)}${l.postcode ? `, ${esc(l.postcode)}` : ""} ${esc(l.city || "")}</span><span></span>` : ""}
      <span class="k">Found on</span><span class="v small">${(l.sources || []).map((s) => s.url ? `<a href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener">${esc(host(s.url) || s.kind)}</a>` : esc(s.kind)).join(", ") || "–"}</span><span></span>
    </div><div class="reasons" style="margin-top:10px">${(l.score_reasons || []).map((r) => `<span>${esc(r)}</span>`).join("")}</div>
    ${l.website ? `<button class="btn sm" data-refresh style="margin-top:10px">${small(I.refresh)} Read their site again</button>` : ""}</details>`;
}

function nextCard(l, f) {
  const due = l.pilot_due_at && ["photos", "retouching"].includes(l.pilot_stage) ? `You promised ${esc(at(l.pilot_due_at).replace(/ \d\d:\d\d$/, ""))}.` : "";
  const actions = ["photos", "retouching"].includes(l.pilot_stage) ? `<button class="btn sm primary" data-tab="photos">Open their photos</button>`
    : ["sent_back", "interested"].includes(l.pilot_stage) ? `<button class="btn sm primary" data-email>${l.pilot_stage === "sent_back" ? "Ask for their opinion" : "Nudge them"}</button>` : "";
  return `<section class="next-card">
    ${f.next ? `<h4>Next step</h4><p class="big">${esc(f.next)}</p>${due ? `<p class="small muted">${due}</p>` : ""}${actions ? `<div class="row">${actions}</div>` : ""}` : ""}
    ${l.next_action ? `<div class="next">${small(I.clock, 16)}<span style="flex:1"><b>${l.next_action === "call" ? "Call them" : esc(l.next_action)}</b> · ${esc(at(l.next_action_at))}</span><button class="btn sm" data-done-action>${small(I.check)} Done</button></div>`
      : `<details><summary class="small dim" style="cursor:pointer">+ Add a reminder</summary><form class="inline-form" data-form="reminder"><input class="input" name="text" placeholder="Send the corrected photos, book a call…" required><input class="input" type="datetime-local" name="at" value="${toLocalInput(tomorrowAt(9))}" style="flex:0 1 200px" aria-label="When"><button class="btn sm">Save</button></form></details>`}
  </section>`;
}

function renderLead() {
  const l = drawer.lead, f = drawer.files;
  if (!l) return;
  const lost = l.status === "lost";
  const sn = l.small_new.map((t) => `<span class="newb">${esc(SMALL_NEW[t] || t)}</span>`).join(" ");
  const snoozed = l.snoozed_until && new Date(l.snoozed_until) > new Date();
  const replied = l.messages.some((m) => m.direction === "in");
  const nMsg = l.messages.filter((m) => m.status !== "cancelled").length;
  const nRec = f.files.filter((x) => x.kind === "received").length;
  const tab = (k, label, n, key) => `<button role="tab" aria-selected="${drawer.tab === k}" data-tab="${k}" title="Key ${key}">${label}${n ? ` <span class="n">${n}</span>` : ""}</button>`;
  D().innerHTML = `<div class="dr-h">
    <div class="row">${av(l, "lg")}<div style="flex:1;min-width:0"><h3>${esc(who(l))}</h3>
      <div class="meta">${l.person ? `${esc(l.name)} · ` : ""}${esc(KIND[l.kind] || l.kind)} · ${flag(l.country)} ${esc(l.city || "town unknown")}${l.country && l.country !== "FR" ? `, ${esc(COUNTRY[l.country] || l.country)}` : ""}${l.dep ? ` <span class="tag">${esc(l.dep)}</span>` : ""}${l.id_?.dep_name ? ` ${esc(l.id_.dep_name)}` : ""}${l.distance_km != null ? ` · ${Math.round(l.distance_km)} km` : ""} ${pill(l.status)} ${l.do_not_contact ? `<span class="flag">do not contact</span>` : ""}</div>
      <div class="idrow">${(l.labels || []).map((id) => LABELS.byId[id]).filter(Boolean).map((x) => `<button class="lab c-${esc(x.color)}" data-lab-off="${x.id}" aria-label="Remove the tag ${esc(x.name)}"><i></i>${esc(x.name)} ×</button>`).join("")}<button class="chip" data-lab-add style="padding:0 9px;font-size:12px">+ tag</button>
        <label class="langsel" title="${l.language_source === "you" ? "You chose it" : l.language_source === "site" ? "Their website is in this language" : "Guessed from where they are"}">Write in <select data-lead-lang aria-label="Language to write to them in">${Object.entries(LANG).map(([k, v]) => `<option value="${k}" ${k === (l.language || "fr") ? "selected" : ""}>${v}</option>`).join("")}</select>${l.language_source === "you" ? "" : `<span class="dim">${l.language_source === "site" ? " · from their site" : " · guessed"}</span>`}</label>
        ${idChips(l, { max: 99 })}${sn ? ` ${sn}` : ""}</div>
      <div class="labmenu" data-lab-menu hidden></div></div>
      <div class="acts-row">
        <button class="btn primary" data-email ${l.do_not_contact ? "disabled" : ""}>${I.mail} ${replied ? "Reply" : "Email"} <kbd>e</kbd></button>
        <button class="btn" data-call ${l.do_not_contact || !l.best_phone ? "disabled" : ""} title="${l.best_phone ? "" : "No phone that reaches them"}">${I.tel} Call <kbd>c</kbd></button>
        <button class="btn" data-snooze>${I.clock} ${snoozed ? `Until ${esc(at(l.snoozed_until))}` : "Snooze"} <kbd>s</kbd></button>
        <details class="more-menu"><summary class="btn" aria-label="More actions">⋯</summary><div class="menu">
          <button class="btn ghost" data-to-camp>Add to a campaign…</button>
          <button class="btn ghost" data-advance ${lost || l.status === "customer" ? "disabled" : ""}>Next stage <kbd>→</kbd></button>
          ${lost ? "" : `<button class="btn ghost" data-stage="lost">Mark lost</button>`}
          <button class="btn ghost danger" data-dnc>${l.do_not_contact ? "Allow contact again" : "Do not contact"}</button>
          ${l.pilot_stage ? `<button class="btn ghost" data-pilot-clear>Remove from pilots</button>` : ""}</div></details>
        <button class="btn ghost sm" data-close-lead aria-label="Close">${small(I.x)} <kbd>Esc</kbd></button></div></div>
    ${lost ? `<div class="note bad" style="margin:12px 0 0">Marked lost. <button class="btn sm" data-stage="contacted">Reopen</button></div>` : stepper(l)}
    <div class="acts-row" data-snooze-menu hidden style="margin-top:8px;align-items:center"><span class="small muted">Hide from Today until</span>
      <button class="btn sm" data-snooze-to="1">Tomorrow</button><button class="btn sm" data-snooze-to="7">Next week</button><button class="btn sm" data-snooze-to="30">In a month</button>${snoozed ? `<button class="btn sm ghost" data-snooze-to="0">Now</button>` : ""}</div>
    <div class="acts-row" data-camp-menu hidden style="margin-top:8px;align-items:center"></div>
  </div>
  <div class="dr-b ws-b">
    ${pilotSec(l, f)}
    <div class="ws-cols">
      <div class="ws-main">
        <div class="tabs" role="tablist" aria-label="About this lead">${tab("conv", "Conversation", nMsg, 1)}${tab("photos", "Their photos", nRec, 2)}${tab("notes", "Notes &amp; activity", 0, 3)}</div>
        ${drawer.tab === "photos" ? photosTab(l, f) : drawer.tab === "notes" ? notesTab(l) : conversation(l, f)}
      </div>
      <aside class="ws-side">
        ${nextCard(l, f)}
        ${l.flags.map((x) => `<div class="note bad">${esc(x)}. Find their own${x.startsWith("Phone") ? " number" : " address"}${l.website ? ` on <a href="${esc(safeUrl(l.website))}" target="_blank" rel="noopener">their site</a>` : ""} and add it under Contact.</div>`).join("")}
        ${contactSec(l)}
        ${photosSec(l)}
        ${companySec(l)}
      </aside>
    </div>
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

D().addEventListener("change", async (e) => {
  const t = e.target;
  if (!drawer.lead) return;
  try {
    if (t.matches("[data-upload]")) { const files = [...t.files]; t.value = ""; await upload(files, t.dataset.upload); }
    else if (t.matches("[data-due]")) {
      drawer.files = await api(`/api/leads/${drawer.lead.id}/pilot`, { method: "PUT", body: { due_at: t.value ? new Date(`${t.value}T18:00`).toISOString() : "" } });
      drawer.lead.pilot_due_at = drawer.files.due_at;
      renderLead();
      changed();
      toast(t.value ? `Promised for ${t.value}` : "No date set");
    }
  } catch (err) { fail(err); }
});

// Drag and drop photos anywhere on the photos tab: they're your retouched versions.
D().addEventListener("dragover", (e) => {
  if (!e.target.closest(".photos") || !e.dataTransfer?.types?.includes("Files")) return;
  e.preventDefault();
  $(".drop", D())?.classList.add("over");
});
D().addEventListener("dragleave", (e) => { if (!e.relatedTarget || !e.relatedTarget.closest?.(".photos")) $(".drop", D())?.classList.remove("over"); });
D().addEventListener("drop", (e) => {
  if (!e.target.closest(".photos") || !e.dataTransfer?.files?.length) return;
  e.preventDefault();
  $(".drop", D())?.classList.remove("over");
  upload([...e.dataTransfer.files], "retouched").catch(fail);
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
    else if (d.tab) { drawer.tab = d.tab; renderLead(); }
    else if (d.view) { drawer.view = d.view; store.set("photoView", d.view); renderLead(); }
    else if (d.email !== undefined) openComposer(l.id);
    else if (d.sendBack !== undefined) openComposer(l.id, null, { fileIds: drawer.files.files.filter((x) => x.kind === "retouched" && !x.sent_at).map((x) => x.id) });
    else if (d.call !== undefined) callLead(l.id);
    else if (d.zoom) lightbox(d.zoom, d.cap);
    else if (d.pilot) await setPilot({ stage: d.pilot });
    else if (d.pilotClear !== undefined) await setPilot({ clear: true });
    else if (d.delFile) {
      if (d.confirm !== "1") { d.confirm = "1"; t.classList.add("armed"); toast("Click the × again to delete this photo"); return; }
      drawer.files = await api(`/api/files/${d.delFile}`, { method: "DELETE" });
      renderLead();
      changed();
    }
    else if (d.stage) await moveTo(d.stage);
    else if (d.advance !== undefined) await advance();
    else if (d.snooze !== undefined) { const m = D().querySelector("[data-snooze-menu]"); m.hidden = !m.hidden; }
    else if (d.labAdd !== undefined) {
      await loadLabels();
      const m = D().querySelector("[data-lab-menu]");
      const free = LABELS.items.filter((x) => !(l.labels || []).includes(x.id));
      m.innerHTML = `<span class="small muted">Tag with</span>${free.map((x) => `<button class="lab c-${esc(x.color)}" data-lab-on="${x.id}"><i></i>${esc(x.name)}</button>`).join("")}<button class="chip" data-lab-new style="font-size:12px">+ New tag</button>`;
      m.hidden = !m.hidden;
    } else if (d.labNew !== undefined) {
      labelEditor(null, async (r) => { await setLabels({ add: [r.id] }); });
    } else if (d.labOn || d.labOff) {
      await setLabels(d.labOn ? { add: [+d.labOn] } : { remove: [+d.labOff] });
    }
    else if (d.toCamp !== undefined) {
      t.closest("details")?.removeAttribute("open");
      const m = D().querySelector("[data-camp-menu]");
      const r = await api(`/api/leads/${l.id}/campaigns`);
      const open = r.items.filter((c) => !c.inside);
      m.innerHTML = r.why ? `<span class="small muted">Can't be added to a campaign: ${esc(r.why)}.</span>`
        : !r.items.length ? `<span class="small muted">No campaign yet. <a href="#campaigns">Create one</a>.</span>`
        : `<span class="small muted">Add to</span>${r.items.map((c) => c.inside ? `<span class="small" style="color:var(--ink)">✓ ${esc(c.name)}</span>` : `<button class="btn sm" data-camp-add="${c.id}">${esc(c.name)}${c.status === "draft" ? " · draft" : ""}</button>`).join("")}${open.length ? "" : ""}`;
      m.hidden = false;
    } else if (d.campAdd) {
      const r = await api(`/api/campaigns/${d.campAdd}/leads`, { method: "POST", body: { lead_ids: [l.id] } });
      toast(r.added ? `${who(l)} added to the campaign.` : r.notes[0] || "Not added.");
      if (r.added) { t.replaceWith(Object.assign(document.createElement("span"), { className: "small", textContent: `✓ ${t.textContent.replace(" · draft", "")}`, style: "color:var(--ink)" })); changed(); }
    }
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

async function setLabels({ add = [], remove = [] }) {
  const l = drawer.lead;
  const r = await api("/api/leads/labels", { method: "POST", body: { lead_ids: [l.id], add, remove } });
  l.labels = r.labels[l.id] || [];
  await loadLabels();
  renderLead();
  changed();
}

async function setPilot(body) {
  const l = drawer.lead, prev = l.pilot_stage;
  await api(`/api/leads/${l.id}/pilot`, { method: "PUT", body });
  await reload();
  changed();
  const now = drawer.lead.pilot_stage;
  if (now === "customer") confetti();
  toast(body.clear ? "Removed from pilots" : `Free test → ${drawer.files.stages.find((x) => x.key === now)?.label || now}`,
    { undo: async () => { await api(`/api/leads/${l.id}/pilot`, { method: "PUT", body: prev ? { stage: prev } : { clear: true } }); await reload(); } });
}

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
  if (k === "1" || k === "2" || k === "3") { drawer.tab = ["conv", "photos", "notes"][+k - 1]; renderLead(); return true; }
  return false;
}

// --- composer ---------------------------------------------------------------------------

const MODE = { first: "First email", followup: "Follow-up", reply: "Reply to their message" };

// fileIds: their retouched photos to attach (from "Send them back").
export async function openComposer(leadId, templateId = null, { fileIds = null } = {}) {
  let c, ai;
  try {
    [c, ai] = await Promise.all([
      api(`/api/compose?lead_id=${leadId}${templateId ? `&template_id=${templateId}` : ""}`),
      api("/api/ai").catch(() => null),
    ]);
  } catch (e) { return fail(e); }
  const l = c.lead, mb = c.mailbox;
  const canSend = mb.configured;
  const noAddr = !c.to_options.length;
  const opts = [...c.usable, ...c.to_options.filter((a) => !c.usable.includes(a))];
  const steps = c.mode === "reply" ? [-1] : c.mode === "followup" ? [1, 2, 0] : [0];
  const tpls = c.templates.filter((t) => steps.includes(t.step));
  const ret = c.files.filter((x) => x.kind === "retouched");
  const pick = new Set(fileIds || []);
  const sendingBack = pick.size > 0;
  let body = c.body;
  if (sendingBack) {
    const fn = (l.person || "").split(/\s+/).length >= 2 ? l.person.split(/\s+/)[0] : "";
    const sig = c.body.split("\n").slice(-2).join("\n").trim();
    body = `Bonjour${fn ? " " + fn : ""},\n\nVoici vos ${pick.size} photo${pick.size > 1 ? "s" : ""} retouchée${pick.size > 1 ? "s" : ""} par Veralume, en pièces jointes. Qu'en pensez-vous ? Vos remarques, même rapides, m'aident beaucoup.\n\n${sig}`;
  }
  const attRow = (kind, x, checked, label) => `<label class="att-pick"><input type="checkbox" class="chk" data-att="${kind}" value="${x.id}" data-size="${x.size}" ${checked ? "checked" : ""}><img src="${x.thumb}" alt="" loading="lazy"><span><span class="small">${esc(label)}</span><span class="small dim">${size(x.size)}</span></span></label>`;
  const sheet = openModal(`<div class="composer">
    <div class="cp-l">
      <div class="row" style="flex-wrap:nowrap">${av(l)}<div style="flex:1;min-width:0"><b>${esc(who(l))}</b><div class="small muted">${l.person ? esc(l.name) + " · " : ""}${esc(l.city || "")} · ${sendingBack ? "Their retouched photos" : MODE[c.mode]}</div></div><button class="btn ghost sm" data-close aria-label="Close">${small(I.x)}</button></div>
      ${c.last_in ? `<div class="quote"><b>They wrote</b> (${esc(when(c.last_in.at))}):\n${esc(c.last_in.body)}</div>` : ""}
      ${c.test && !sendingBack ? `<div class="ab-tag">${c.test.in_test
        ? `A/B test “${esc(c.test.name)}”: this lead gets version ${c.test.letter} of ${c.test.versions}. Small edits are fine.`
        : `You picked another template, so this lead is out of the test “${esc(c.test.name)}”.`}</div>` : ""}
      ${tpls.length > 1 && !sendingBack ? `<div class="tpls" role="group" aria-label="Template">${tpls.map((t) => `<button aria-pressed="${t.id === c.template_id}" data-tpl="${t.id}">${c.test?.template_id === t.id ? `${c.test.letter} · ` : ""}${esc(t.name)}</button>`).join("")}</div>` : ""}
      <div class="field"><span class="k">To</span>${noAddr ? `<span class="flag">No address yet. Add one in the lead file.</span>` : `<select id="cp-to" aria-label="To">${opts.map((a) => `<option value="${esc(a)}" ${a === c.to ? "selected" : ""}>${esc(a)}${c.usable.includes(a) ? "" : " (not theirs)"}</option>`).join("")}</select>`}</div>
      <div class="field"><span class="k">From</span><span>${esc(mb.sender || "your mailbox (not connected yet)")}</span></div>
      <div class="field"><span class="k">Subject</span><input id="cp-subject" value="${esc(c.subject)}" aria-label="Subject"></div>
      <textarea class="input mailbody" id="cp-body" aria-label="Message" lang="fr" autofocus>${esc(body)}</textarea>
      ${aiBar(ai)}
      <div class="optout">--\n${esc(c.footer)}</div>
    </div>
    <div class="cp-r">
      ${ret.length ? `<h4>Their photos, retouched</h4><div class="att-list">${ret.map((x) => attRow("file", x, pick.has(x.id), `${x.filename}${x.sent_at ? " · already sent" : ""}`)).join("")}</div>` : ""}
      ${c.assets.length ? `<h4>Before/after examples</h4><div class="att-list">${c.assets.map((a) => attRow("asset", a, !sendingBack, a.name)).join("")}</div>` : ""}
      ${c.photo ? `<h4>Their photo, marked</h4><img src="${esc(c.photo.src)}" alt="Their photo with the lean drawn on it"><label class="check"><input type="checkbox" class="chk" id="cp-attach" ${c.attach && !sendingBack ? "checked" : ""}> Attach it (verticals lean ${leanTxt(c.photo.lean)})</label>`
        : !c.assets.length && !ret.length ? `<h4>No photo to attach</h4><p class="small muted" style="margin:0">None of their photos is measured. The email works without one. Before/after examples are added to a template in Campaigns.</p>` : ""}
      <p class="small muted" data-att-size style="margin:0"></p>
      ${c.mode === "first" && !sendingBack ? `<h4 style="margin-top:6px">Then, if they don't reply</h4>
        <div class="seq">${c.followups.map((f, i) => `<div><span class="d">Day ${esc(f.days)}</span><span>Follow-up ${i + 1}</span><span class="tag">you review it</span></div>`).join("")}</div>
        <label class="check"><input type="checkbox" class="chk" id="cp-fu" checked> Write these follow-ups</label>
        <p class="small dim" style="margin:0">Each waits in Today on its day until you approve it. A reply cancels them.</p>` : ""}
      ${!canSend ? `<div class="note warn">Your mailbox isn't connected yet, so the app can't send this. Use "Copy and log as sent" to send it from your mail app. <a href="#settings" data-close>Connect it</a></div>` : mb.dry_run ? `<div class="note warn">Test mode: the email is saved in data/outbox, not sent.</div>` : ""}
    </div>
    <div class="cp-foot"><span class="caps">${mb.sent_today} / ${mb.cap} sent today</span><span class="grow"></span>
      <button class="btn" data-copy ${noAddr ? "disabled" : ""} title="Attachments aren't copied: add them in your mail app">${small(I.copy)} Copy and log as sent</button>
      ${canSend ? `<button class="btn" data-queue ${noAddr ? "disabled" : ""} title="Goes out at the next free slot, spaced from your other emails">Send later</button>` : ""}
      <button class="btn primary" data-send ${!canSend || noAddr ? "disabled" : ""}>${small(I.send, 15)} Send <kbd>Ctrl ↵</kbd></button></div>
  </div>`);

  const checked = (kind) => [...sheet.querySelectorAll(`[data-att="${kind}"]:checked`)].map((x) => +x.value);
  const sizeNote = () => {
    const boxes = [...sheet.querySelectorAll("[data-att]:checked")];
    const total = boxes.reduce((a, x) => a + +x.dataset.size, 0);
    const n = boxes.length + (sheet.querySelector("#cp-attach")?.checked ? 1 : 0);
    const el = sheet.querySelector("[data-att-size]");
    el.textContent = n ? `${n} attachment${n > 1 ? "s" : ""}, about ${size(total * 1.37)} once sent${total > 18e6 ? ". Too big for Gmail: untick some." : ""}` : "";
    el.classList.toggle("bad", total > 18e6);
  };
  sizeNote();
  sheet.onchange = (e) => { if (e.target.matches("[data-att], #cp-attach")) sizeNote(); };
  const payload = (whenTo) => ({
    lead_id: l.id, to: sheet.querySelector("#cp-to")?.value || "", subject: sheet.querySelector("#cp-subject").value,
    body: sheet.querySelector("#cp-body").value, template_id: sendingBack ? null : c.template_id,
    attach_photo_id: sheet.querySelector("#cp-attach")?.checked && c.photo ? c.photo.id : null,
    in_reply_to: c.in_reply_to, step: c.step, followups: sheet.querySelector("#cp-fu")?.checked ?? false, when: whenTo,
    experiment_id: c.test?.in_test && !sendingBack ? c.test.id : null,
    edited: sheet.querySelector("#cp-subject").value !== c.subject || sheet.querySelector("#cp-body").value !== c.body,
    asset_ids: checked("asset"), file_ids: checked("file"),
  });
  wireAiBar(sheet.querySelector(".ai-bar"), {
    subject: sheet.querySelector("#cp-subject"), body: sheet.querySelector("#cp-body"),
    extra: () => ({ lead_id: l.id, step: c.step }),
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
        else if (p.file_ids.length) toast(`${p.file_ids.length} retouched photo${p.file_ids.length > 1 ? "s" : ""} sent to ${who(l)}`);
        else toast(c.mode === "reply" ? `Reply sent to ${who(l)}` : p.followups && c.mode === "first" ? `Sent to ${who(l)} · follow-ups on day ${c.followups.map((f) => f.days).join(" and ")}` : `Sent to ${who(l)}`);
      });
    }
    if (d.copy !== undefined) {
      // Recorded as a sent email (no follow-ups: the app can't send them for you), so it
      // counts in goals, campaigns and A/B tests like any other.
      const p = { ...payload("external"), followups: false };
      try { await navigator.clipboard.writeText(`${p.subject}\n\n${p.body}\n\n--\n${c.footer}`); } catch { sheet.querySelector("#cp-body").select(); }
      await busy(t, async () => {
        await api("/api/send", { method: "POST", body: p });
        closeModal();
        changed();
        if (drawer.lead?.id === l.id) await reload();
        toast(`Copied. Paste it into your mail app, to ${p.to}${p.asset_ids.length + p.file_ids.length ? ", and attach the images yourself" : ""}. Logged as sent.`);
      });
    }
  };
}

// --- review a written email (follow-up, campaign batch, after a voicemail) --------------

/** Read a written email before it goes. `manual` (you send each email yourself): the main
 * button sends it now and `next` opens the following one, so a morning's batch is a
 * series of "read, Send". Otherwise you can also approve it for the scheduled sending. */
export async function openReview(messageId, item = null, { manual = null, next = null, left = 0 } = {}) {
  if (manual === null) manual = (await api("/api/settings").catch(() => ({}))).send_mode !== "scheduled";
  let m = item?.message, l = item?.lead;
  if (!m || !l) {
    l = drawer.lead ? await api(`/api/leads/${drawer.lead.id}`).catch(() => null) : null;
    m = l?.messages.find((x) => x.id === messageId);
    if (!m) return fail(new Error("This email isn't waiting any more."));
  }
  const atts = [
    ...(m.attach_photo_id ? [`<img src="/api/photos/${m.attach_photo_id}/marked.jpg" alt="Their photo, marked">`] : []),
    ...(m.asset_ids || []).map((id) => `<img src="/api/assets/${id}/thumb" alt="Before/after example">`),
    ...(m.file_ids || []).map((id) => `<img src="/api/files/${id}/thumb" alt="Their retouched photo">`),
  ];
  const sheet = openModal(`<div class="sheet-h">${av(l)}<h2>${esc(STEP[m.step] || "Email")} to ${esc(who(l))}</h2><button class="btn ghost sm" data-close aria-label="Close">${small(I.x)}</button></div>
    <div class="sheet-b">
      <div class="small muted">${esc(l.name)} · ${esc(l.city || "")} · ${pill(l.status)} ${m.error && m.status === "failed" ? `<span class="flag">${esc(m.error)}</span>` : ""}</div>
      <div class="field"><span class="k">To</span><input id="rv-to" value="${esc(m.to || "")}" aria-label="To"></div>
      <div class="field"><span class="k">Subject</span><input id="rv-subject" value="${esc(m.subject)}" aria-label="Subject"></div>
      <textarea class="input mailbody" id="rv-body" aria-label="Message" lang="fr" autofocus>${esc(m.body)}</textarea>
      ${atts.length ? `<div class="rv-atts"><span class="small muted">${atts.length} attachment${atts.length > 1 ? "s" : ""}</span>${atts.join("")}</div>` : ""}
      <div class="row"><button class="btn ghost danger" data-cancel-msg>Don't send</button><button class="btn ghost" data-open-lead>Open lead file</button><span class="grow"></span>
        ${next && left ? `<button class="btn ghost" data-skip-next>Later</button>` : ""}
        ${manual ? "" : `<button class="btn" data-approve>Approve</button>`}<button class="btn primary" data-send-now>${small(I.send, 15)} Send${next && left ? ` and next <span class="small" style="opacity:.8">(${left} left)</span>` : manual ? "" : " now"} <kbd>Ctrl ↵</kbd></button></div>
    </div>`, { narrow: true });
  const save = () => api(`/api/messages/${m.id}`, { method: "PATCH", body: { to: sheet.querySelector("#rv-to").value, subject: sheet.querySelector("#rv-subject").value, body: sheet.querySelector("#rv-body").value } });
  sheet.onclick = async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    const d = t.dataset;
    if (d.close !== undefined) return closeModal();
    if (d.openLead !== undefined) { closeModal(); return openLead(l.id); }
    if (d.skipNext !== undefined) { closeModal(); return next(); }
    let sent = false;
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
      sent = true;
      closeModal();
      changed();
      if (drawer.lead?.id === l.id) await reload();
    });
    if (sent && next && left && (d.sendNow !== undefined || d.cancelMsg !== undefined)) next();
  };
}
