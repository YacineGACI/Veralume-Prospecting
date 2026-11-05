// The views. Each one renders into the canvas and may handle its own keys.
import {
  $, $$, aiBar, api, at, av, busy, changed, closeModal, confetti, dayName, esc, fail, firstName, I,
  idChips, kindIcon, KINDS_PL, LABELS, labelChip, labelChips, labelEditor, leanChip, leanCol, leanTxt, loadLabels, manageLabels, openModal, pill, ring, scoreCls, small, SMALL_NEW, STAGES, STATUS,
  STEP, store, telHref, toast, toLocalInput, fromLocalInput, tomorrowAt, when, wireAiBar,
} from "./core.js";
import { callLead, openComposer, openLead, openReview } from "./lead.js";

const C = () => $("#canvas");
const SEGS = ["photographer", "agency", "independent_agent"];
const segButtons = (cur) => `<div class="seg" role="group" aria-label="Segment">${SEGS.map((k) => `<button data-seg="${k}" aria-pressed="${cur === k}">${KINDS_PL[k]}</button>`).join("")}</div>`;
export const ui = { badge: 0 };

// Keyboard list navigation shared by Today and Leads: j/k move, Enter opens, e emails.
function listKeys(e, sel) {
  const rows = $$(sel, C());
  if (!rows.length) return false;
  const i = rows.findIndex((r) => r.classList.contains("kb"));
  const go = (n) => { rows.forEach((r) => r.classList.remove("kb")); const r = rows[Math.max(0, Math.min(n, rows.length - 1))]; r.classList.add("kb"); r.scrollIntoView({ block: "nearest" }); };
  if (e.key === "j") { go(i + 1); return true; }
  if (e.key === "k") { go(i < 0 ? 0 : i - 1); return true; }
  if (i < 0) return false;
  if (e.key === "Enter") { openLead(+rows[i].dataset.open); return true; }
  if (e.key === "e") { openComposer(+rows[i].dataset.open); return true; }
  return false;
}

// --- Today -------------------------------------------------------------------------------
export const today = {
  async render() {
    const seg = store.get("segment", null);
    const t = await api(`/api/today${seg ? `?segment=${seg}` : ""}`);
    ui.badge = t.replies.length + t.ready.length + t.actions.length;
    const g = t.goals, d = t.done, mb = t.mailbox;
    const nR = t.replies.length, nW = t.ready.length;
    const head = nR ? `${dayName()}. <em>${nR} ${nR > 1 ? "replies are" : "reply is"}</em> waiting for you.`
      : nW ? `${dayName()}. <em>${nW} ${nW > 1 ? "emails are" : "email is"}</em> written, ready for you.`
      : d.email + d.call === 0 ? `${dayName()}. Let's find your first <em>pilot customers</em>.`
      : `${dayName()}. <em>${d.email + d.call} touches</em> today. Keep going.`;
    const q = (l, sub, acts, extra = "", lean = false) => `<div class="qitem" data-open="${l.id}" ${extra}>${av(l)}<div style="min-width:0"><div class="t">${esc(l.person || l.name)}${l.person ? ` <span>· ${esc(l.name)}</span>` : ""}</div><div class="s">${sub}</div><div class="idrow">${labelChips(l)}${idChips(l)}${lean ? leanChip(l.lean_median) : ""}</div></div><div class="acts">${acts}</div></div>`;
    const days = t.week.map((w) => w.emails + w.calls > 0);
    const maxW = Math.max(10, ...t.week.map((w) => w.emails + w.calls));
    const f = t.funnel;
    const reached = (i) => STAGES.slice(i).reduce((a, s) => a + (f[s] || 0), 0);
    C().innerHTML = `<div class="view">
      <div class="topline"><div><h1>${head}</h1><div class="sub row" style="gap:10px;margin-top:8px">${segButtons(t.segment)}<span class="small muted">the segment you're working on today</span></div></div><span class="grow"></span>
        <button class="btn" data-pal>${small(I.search, 15)} Search <kbd>Ctrl K</kbd></button></div>
      ${!mb.configured ? `<div class="note warn" style="margin-bottom:16px">Your mailbox isn't connected, so the app can't send emails yet. You can still write them and copy them into your mail app. <a href="#settings">Connect your mailbox</a></div>`
        : mb.dry_run ? `<div class="note warn" style="margin-bottom:16px">Test mode: emails are saved in data/outbox instead of being sent.</div>` : ""}
      ${mb.approved_waiting ? `<div class="note ok" style="margin-bottom:16px">${small(I.send, 15)} ${mb.approved_waiting} approved ${mb.approved_waiting > 1 ? "emails go" : "email goes"} out ${!mb.configured ? "once your mailbox is connected" : mb.in_window ? "over the next minutes, a few minutes apart" : `from ${mb.hours[0]}:00 on the next working day`}.</div>` : ""}
      <div class="today">
        <div class="col-stack">
          <section class="card"><div class="goals">
            <div class="goal">${ring("", d.email, g.email)}<div><b>${d.email}<small>/${g.email}</small></b><span>emails sent</span></div></div>
            <div class="goal">${ring("calls", d.call, g.call)}<div><b>${d.call}<small>/${g.call}</small></b><span>calls made</span></div></div>
            <div class="goal">${ring("replies", d.reply, g.reply)}<div><b>${d.reply}<small>/${g.reply}</small></b><span>replies</span></div></div>
            <div class="streak"><span class="big">${t.streak}</span><div><span class="small muted">${t.streak === 1 ? "day" : "days"} in a row</span><div class="days" aria-hidden="true">${days.map((on, i) => `<i class="${on ? "on" : ""} ${i === days.length - 1 ? "today" : ""}"></i>`).join("")}</div></div></div>
          </div></section>
          <section class="card">
            <div class="card-h"><h2>Up next</h2><span class="grow"></span><span class="small dim"><kbd>j</kbd> <kbd>k</kbd> move · <kbd>Enter</kbd> open · <kbd>e</kbd> email</span></div>
            ${nR ? `<div class="queue-group"><div class="qg-h"><b>Replies</b> ${nR}<span class="grow"></span><span>answer them today</span></div>
              ${t.replies.map((r) => q(r.lead, `<span class="snip">“${esc(r.message.snippet)}”</span> · ${esc(when(r.message.at))}`, `<button class="btn sm primary" data-email="${r.lead.id}">Reply</button>`)).join("")}</div>` : ""}
            ${nW ? `<div class="queue-group"><div class="qg-h"><b>Written, ready for your review</b> ${nW}<span class="grow"></span><button class="btn sm primary" data-approve-all>${small(I.check)} Approve all ${nW}</button></div>
              ${t.ready.map((r, i) => q(r.lead, `${esc(STEP[r.message.step] || "Email")} · ${esc(r.message.subject)}`, `<button class="btn sm" data-review="${i}">Review</button>`)).join("")}</div>` : ""}
            ${t.actions.length ? `<div class="queue-group"><div class="qg-h"><b>Call-backs and reminders</b> ${t.actions.length}<span class="grow"></span>${t.actions.some((a) => a.next_action === "call") ? `<a class="btn sm" href="#call">${small(I.tel)} Start call mode</a>` : ""}</div>
              ${t.actions.map((l) => q(l, `${small(I.clock, 12)} ${l.next_action === "call" ? "Call them" : esc(l.next_action)} · ${esc(at(l.next_action_at))}`, l.next_action === "call" ? `<button class="btn sm" data-call="${l.id}">Call</button>` : `<button class="btn sm" data-done="${l.id}">${small(I.check)} Done</button>`)).join("")}</div>` : ""}
            <div class="queue-group"><div class="qg-h"><b>Fresh leads</b> · ${esc(KINDS_PL[t.segment].toLowerCase())} not contacted yet, new and small first<span class="grow"></span><span>${Math.max(g.email - d.email, 0)} emails left for today's goal</span></div>
              ${t.fresh.length ? t.fresh.map((l) => q(l, `${esc(l.city || "town unknown")}${l.dep ? ` ${esc(l.dep)}` : ""} · score ${l.score}${l.lean_median == null ? " · photos not measured" : ""}`, `<button class="btn sm primary" data-email="${l.id}">Email</button><button class="btn sm" data-skip="${l.id}" title="Hide until tomorrow">Skip</button>`, "", true)).join("")
                : `<p class="empty">No ${esc(KINDS_PL[t.segment].toLowerCase())} left with a usable email. Switch segment, or open Leads to find their addresses.</p>`}</div>
          </section>
        </div>
        <div class="col-stack">
          ${t.opener ? `<section class="card opener"><div class="card-h"><h2>Your opener</h2><span class="grow"></span><button class="tag" data-open="${t.opener.lead_id}" style="cursor:pointer;background:none">${esc(t.opener.name)} · ${leanTxt(t.opener.lean)}</button></div>
            <div style="padding:0 14px 14px"><img src="/api/photos/${t.opener.photo_id}/marked.jpg" alt="A lead's own listing photo with its lean drawn on it" data-open="${t.opener.lead_id}" style="cursor:pointer">
            <p class="small muted" style="margin:10px 4px 0">This is what your emails attach: the lead's own photo, the true vertical in green, their walls in red. The problem is obvious before they read a word.</p></div></section>` : ""}
          <section class="card"><div class="card-h"><h2>This week</h2><span class="grow"></span><div class="legend"><span><i style="background:var(--emerald)"></i>emails</span><span><i style="background:var(--blue)"></i>calls</span></div></div>
            <div class="bars">${t.week.map((w) => `<div title="${w.emails} emails, ${w.calls} calls, ${w.replies} replies"><span style="height:${(w.emails / maxW) * 80}px"></span><span class="c" style="height:${(w.calls / maxW) * 80}px"></span></div>`).join("")}</div>
            <div class="bars-x">${t.week.map((w, i) => `<span>${i === t.week.length - 1 ? "Today" : new Date(w.day + "T12:00").toLocaleDateString("en-GB", { weekday: "short" })}</span>`).join("")}</div></section>
          <section class="card"><div class="card-h"><h2>Pipeline</h2><span class="grow"></span><a class="btn sm ghost" href="#pipeline">Open ${small(I.arrow)}</a></div>
            <div class="funnel">${[["Contacted", 1, "var(--blue)"], ["Replied", 2, "var(--warn)"], ["Interview", 3, "var(--violet)"], ["Pilot", 4, "var(--emerald)"], ["Customer", 5, "var(--deep)"]].map(([k, i, c]) => { const n = reached(i), top = Math.max(reached(1), 1); return `<div class="frow"><span>${k}</span><div class="b"><span style="width:${n ? Math.max((n / top) * 100, 2) : 0}%;background:${c}"></span></div><span class="n">${n}</span></div>`; }).join("")}</div>
            <p class="small dim" style="margin:-6px 18px 14px">Leads that reached each stage.</p></section>
        </div>
      </div></div>`;
    C().onclick = async (e) => {
      const b = e.target.closest("button, [data-open]");
      if (!b) return;
      const dd = b.dataset;
      if (dd.seg) { store.set("segment", dd.seg); api("/api/settings", { method: "PUT", body: { segment: dd.seg } }).catch(() => {}); return this.render(); }
      if (dd.email) return openComposer(+dd.email);
      if (dd.call) return callLead(+dd.call);
      if (dd.review !== undefined) return openReview(null, t.ready[+dd.review]);
      if (dd.approveAll !== undefined) return busy(b, async () => {
        const r = await api("/api/messages/approve", { method: "POST", body: { ids: t.ready.map((x) => x.message.id) } });
        toast(!r.mailbox.configured ? `${r.approved} approved. They go out once your mailbox is connected.` : `${r.approved} approved. They go out ${r.mailbox.in_window ? "over the next minutes, spaced out" : "from 9:00 on the next working day"}.`);
        changed();
      });
      if (dd.skip) {
        const id = +dd.skip;
        await api(`/api/leads/${id}`, { method: "PATCH", body: { snoozed_until: tomorrowAt(8).toISOString() } }).catch(fail);
        toast("Skipped until tomorrow", { undo: () => api(`/api/leads/${id}`, { method: "PATCH", body: { snoozed_until: "" } }) });
        return changed();
      }
      if (dd.done) {
        const id = +dd.done, l = t.actions.find((x) => x.id === id);
        await api(`/api/leads/${id}`, { method: "PATCH", body: { clear_next_action: true } }).catch(fail);
        toast("Done", { undo: () => api(`/api/leads/${id}`, { method: "PATCH", body: { next_action: l.next_action, next_action_at: l.next_action_at } }) });
        return changed();
      }
      if (dd.pal !== undefined) return document.dispatchEvent(new CustomEvent("palette"));
      if (dd.open) return openLead(+dd.open);
    };
  },
  key(e) { return listKeys(e, ".qitem[data-open]"); },
};

// --- Leads -------------------------------------------------------------------------------
const LQ0 = { kind: "photographer", q: "", reach: "email", contacted: "no", small_first: true, measured: false, min_lean: false, needs_check: false, dep: "", sort: "score" };
export const leads = {
  qs: { ...LQ0, ...store.get("leadsQ", {}) }, items: [], total: 0, sel: new Set(), meta: null,
  params(offset = 0) {
    const q = this.qs, p = new URLSearchParams({ kind: q.kind, sort: q.sort, offset, limit: 100 });
    if (q.q) p.set("q", q.q);
    if (q.reach) p.set("reach", q.reach);
    if (q.contacted) p.set("contacted", q.contacted);
    if (q.small_first) p.set("small_first", "true");
    if (q.measured) p.set("measured", "true");
    if (q.min_lean) p.set("min_lean", "3");
    if (q.needs_check) p.set("needs_check", "true");
    if (q.dep) p.set("dep", q.dep);
    if (q.label) p.set("label", q.label);
    return p;
  },
  async load(more = false) {
    const r = await api(`/api/leads?${this.params(more ? this.items.length : 0)}`);
    this.items = more ? this.items.concat(r.items) : r.items;
    this.total = r.total;
  },
  async render() {
    store.set("leadsQ", this.qs);
    const [meta] = await Promise.all([this.meta || api("/api/meta"), this.load(), loadLabels()]);
    if (this.qs.label && !LABELS.byId[this.qs.label]) { this.qs.label = null; await this.load(); }
    this.meta = meta;
    this.draw();
  },
  draw() {
    const q = this.qs, m = this.meta;
    const chip = (key, label, on) => `<button class="chip" data-chip="${key}" aria-pressed="${!!on}">${label}</button>`;
    const reachN = m.by_kind[q.kind] || { total: 0, email: 0, phone: 0 };
    C().innerHTML = `<div class="view">
      <div class="topline"><h1>Leads</h1><span class="sub">${m.total.toLocaleString("en")} in the database</span><span class="grow"></span>
        <label class="search">${small(I.search, 15)}<input id="lq" type="search" placeholder="Name, town, postcode, website, notes" value="${esc(q.q)}" aria-label="Search leads"><kbd>/</kbd></label>
        <button class="btn" data-add>${small(I.plus)} Lead</button></div>
      <div class="segs">${SEGS.map((k) => `<button aria-pressed="${q.kind === k}" data-kind="${k}">${kindIcon(k, 15)} ${KINDS_PL[k]} <b>${(m.by_kind[k]?.total || 0).toLocaleString("en")}</b></button>`).join("")}</div>
      <div class="labbar"><span class="lbl">Your tags</span>
        ${LABELS.items.map((x) => `<button class="lab c-${esc(x.color)}" data-label="${x.id}" aria-pressed="${q.label === x.id}" title="Show only leads tagged ${esc(x.name)}"><i></i>${esc(x.name)} <span class="mono small">${x.n}</span></button>`).join("")}
        <button class="chip" data-new-label>+ New tag</button>${LABELS.items.length ? `<button class="linkish small" data-manage-labels>Rename or delete</button>` : `<span class="small dim">e.g. VIP, Met at the fair, Call first. Tag leads from their file or several at once here.</span>`}</div>
      <div class="chips">
        ${chip("email", `Has their email · ${reachN.email}`, q.reach === "email")}${chip("phone", `Has their phone · ${reachN.phone}`, q.reach === "phone")}
        ${chip("not", "Not contacted", q.contacted === "no")}${chip("yes", "Contacted", q.contacted === "yes")}
        ${chip("small_first", "New or small first", q.small_first)}${chip("measured", "Photos measured", q.measured)}${chip("min_lean", "Lean ≥ 3°", q.min_lean)}${chip("needs_check", "Contact to fix", q.needs_check)}
        <span class="grow"></span>
        <select class="select" id="ldep" aria-label="Département" style="padding:4px 8px"><option value="">All départements</option>${m.departements.map((d) => `<option ${d === q.dep ? "selected" : ""}>${esc(d)}</option>`).join("")}</select>
        <select class="select" id="lsort" aria-label="Sort" style="padding:4px 8px">${[["score", "Best score"], ["lean", "Most lean"], ["distance", "Nearest to Strasbourg"], ["contacted", "Last contacted"], ["updated", "Recently changed"], ["name", "Name"]].map(([v, t]) => `<option value="${v}" ${v === q.sort ? "selected" : ""}>${t}</option>`).join("")}</select>
      </div>
      <div class="tablewrap"><table><thead><tr><th style="width:34px"><input type="checkbox" class="chk" id="selall" aria-label="Select all shown"></th><th>Score</th><th>Lead</th><th>Town</th><th>Reach</th><th>Lean</th><th>Stage</th><th>Contacted</th></tr></thead>
      <tbody>${this.items.map((l) => this.row(l)).join("") || `<tr><td colspan="8" class="empty" style="padding:24px">No leads match. Remove a filter.</td></tr>`}</tbody></table></div>
      <div class="more">${this.items.length < this.total ? `<button class="btn" data-more>Show more · ${this.items.length} of ${this.total.toLocaleString("en")}</button>` : `<span class="small dim">${this.total.toLocaleString("en")} leads</span>`}</div>
      <div id="bulk"></div>
      <p class="small dim"><kbd>j</kbd> <kbd>k</kbd> move · <kbd>x</kbd> select · <kbd>Enter</kbd> open · <kbd>e</kbd> email · <kbd>/</kbd> search</p></div>`;
    this.bulk();
    let timer;
    $("#lq").oninput = (e) => { clearTimeout(timer); timer = setTimeout(() => { this.qs.q = e.target.value.trim(); this.render().then(() => { const i = $("#lq"); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }); }, 300); };
    $("#ldep").onchange = (e) => { this.qs.dep = e.target.value; this.render(); };
    $("#lsort").onchange = (e) => { this.qs.sort = e.target.value; this.render(); };
    $("#selall").onchange = (e) => { this.items.forEach((l) => (e.target.checked ? this.sel.add(l.id) : this.sel.delete(l.id))); this.draw(); };
    C().onclick = (e) => this.click(e);
  },
  row(l) {
    const mail = l.email_ok ? "y" : l.emails ? "x" : "";
    const tel = l.phone_ok ? "y" : l.phones ? "x" : "";
    return `<tr data-open="${l.id}" class="${this.sel.has(l.id) ? "sel" : ""}">
      <td><input type="checkbox" class="chk" data-sel="${l.id}" ${this.sel.has(l.id) ? "checked" : ""} aria-label="Select ${esc(l.name)}"></td>
      <td><span class="score ${scoreCls(l.score)}">${l.score}</span></td>
      <td class="who"><div class="row" style="gap:12px;flex-wrap:nowrap;align-items:flex-start">${av(l)}<div style="min-width:0"><b>${esc(l.person || l.name)}</b>${l.person ? `<span class="bn">${esc(l.name)}</span>` : ""}
        <div class="idrow">${labelChips(l)}${idChips(l)}${l.flags.map((f) => `<span class="flag">${esc(f)}</span>`).join("")}</div></div></div></td>
      <td>${esc(l.city || "–")} ${l.dep ? `<span class="tag">${esc(l.dep)}</span>` : ""}${l.id_?.dep_name ? `<div class="small muted">${esc(l.id_.dep_name)}${l.distance_km != null && l.distance_km < 60 ? ` · ${Math.round(l.distance_km)} km` : ""}</div>` : ""}</td>
      <td><span class="ci"><span class="${mail}" title="${mail === "y" ? "Their email" : mail === "x" ? "Email found, but not theirs" : "No email"}">${I.mail}</span><span class="${tel}" title="${tel === "y" ? "Their phone" : tel === "x" ? "Phone found, but a switchboard" : "No phone"}">${I.tel}</span><span class="${l.website ? "y" : ""}" title="${l.website ? "Website" : "No website"}">${I.web}</span></span></td>
      <td><span class="leanbar"><span class="tr"><span style="width:${l.lean_median == null ? 0 : Math.min((l.lean_median / 8) * 100, 100)}%;background:${leanCol(l.lean_median)}"></span></span><span class="num small">${leanTxt(l.lean_median)}</span></span></td>
      <td>${pill(l.status)}</td>
      <td>${l.last_contacted_at ? `<span class="done">✓</span> <span class="small muted">${esc(when(l.last_contacted_at))}</span>` : `<span class="dim">–</span>`}</td></tr>`;
  },
  bulk() {
    const n = this.sel.size;
    $("#bulk").innerHTML = n ? `<div class="bulk"><span>${n} selected</span>
      <button class="btn sm" data-bulk="call">${small(I.tel)} Add to call queue</button>
      <button class="btn sm" data-bulk="contacted">${small(I.check)} Mark contacted</button>
      <select data-bulk-status aria-label="Move to stage"><option value="">Move to…</option>${[...STAGES, "lost"].map((s) => `<option value="${s}">${STATUS[s]}</option>`).join("")}</select>
      ${LABELS.items.length ? `<select data-bulk-label aria-label="Tag the selected leads"><option value="">Tag…</option>${LABELS.items.map((x) => `<option value="+${x.id}">Add “${esc(x.name)}”</option>`).join("")}${LABELS.items.map((x) => `<option value="-${x.id}">Remove “${esc(x.name)}”</option>`).join("")}</select>` : ""}
      <button class="btn sm" data-bulk="dnc">Do not contact</button>
      <button class="btn sm" data-clear aria-label="Clear selection">${small(I.x)}</button></div>` : "";
    const s = $("[data-bulk-status]");
    if (s) s.onchange = () => s.value && this.doBulk("status", s.value);
    const lb = $("[data-bulk-label]");
    if (lb) lb.onchange = async () => {
      if (!lb.value) return;
      const id = +lb.value.slice(1), add = lb.value[0] === "+", ids = [...this.sel];
      await api("/api/leads/labels", { method: "POST", body: { lead_ids: ids, add: add ? [id] : [], remove: add ? [] : [id] } }).catch(fail);
      toast(`${add ? "Tagged" : "Untagged"} ${ids.length} lead${ids.length > 1 ? "s" : ""} “${LABELS.byId[id]?.name}”`);
      this.sel.clear();
      this.render();
    };
  },
  async doBulk(action, value = null, btn = null) {
    const ids = [...this.sel];
    if (action === "dnc" && btn && btn.dataset.confirm !== "1") { btn.dataset.confirm = "1"; btn.textContent = `Confirm: never contact ${ids.length}`; return; }
    await busy(btn, async () => {
      await api("/api/leads/bulk", { method: "POST", body: { ids, action, value } });
      this.sel.clear();
      toast({ call: `${ids.length} added to the call queue`, contacted: `${ids.length} marked contacted`, status: `${ids.length} moved to ${STATUS[value] || value}`, dnc: `${ids.length} won't be contacted` }[action]);
      this.meta = null;
      await this.render();
    });
  },
  async click(e) {
    const t = e.target.closest("button, input[data-sel], tr[data-open]");
    if (!t) return;
    const d = t.dataset;
    if (d.sel) { const id = +d.sel; this.sel.has(id) ? this.sel.delete(id) : this.sel.add(id); t.closest("tr").classList.toggle("sel", this.sel.has(id)); return this.bulk(); }
    if (d.kind) { this.qs.kind = d.kind; this.qs.small_first = d.kind === "photographer"; this.sel.clear(); return this.render(); }
    if (d.label) { this.qs.label = this.qs.label === +d.label ? null : +d.label; return this.render(); }
    if (d.newLabel !== undefined) return labelEditor(null, () => this.render());
    if (d.manageLabels !== undefined) return manageLabels(() => this.render());
    if (d.chip) {
      const q = this.qs, k = d.chip;
      if (k === "email" || k === "phone") q.reach = q.reach === k ? "" : k;
      else if (k === "not") q.contacted = q.contacted === "no" ? "" : "no";
      else if (k === "yes") q.contacted = q.contacted === "yes" ? "" : "yes";
      else q[k] = !q[k];
      return this.render();
    }
    if (d.more !== undefined) return busy(t, async () => { await this.load(true); this.draw(); });
    if (d.bulk) return this.doBulk(d.bulk, null, t);
    if (d.clear !== undefined) { this.sel.clear(); return this.draw(); }
    if (d.add !== undefined) return addLead();
    if (d.open) return openLead(+d.open);
  },
  key(e) {
    if (e.key === "/") { $("#lq")?.focus(); return true; }
    if (e.key === "x") {
      const r = $("tr.kb", C());
      if (r) { r.querySelector("[data-sel]").click(); return true; }
    }
    return listKeys(e, "tbody tr[data-open]");
  },
};

function addLead() {
  const sheet = openModal(`<div class="sheet-h"><h2>Add a lead</h2><button class="btn ghost sm" data-close aria-label="Close">${small(I.x)}</button></div>
    <form class="sheet-b"><div class="form-grid">
      <label class="full">Name<input class="input" name="name" required autofocus></label>
      <label>Kind<select class="select" name="kind"><option value="photographer">Photographer</option><option value="agency">Agency</option><option value="independent_agent">Independent agent</option><option value="other">Other</option></select></label>
      <label>Town<input class="input" name="city"></label>
      <label>Email<input class="input" name="email" type="email"></label>
      <label>Phone<input class="input" name="phone"></label>
      <label class="full">Website<input class="input" name="website" placeholder="https://"></label></div>
      <p class="small dim" style="margin:0">With a website, the app reads it and measures its photos (about 30 s).</p>
      <div class="row"><span class="grow"></span><button class="btn" type="button" data-close>Cancel</button><button class="btn primary">Add</button></div></form>`, { narrow: true });
  sheet.onclick = (e) => { if (e.target.closest("[data-close]")) closeModal(); };
  sheet.onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    await busy(f.querySelector(".primary"), async () => {
      const body = Object.fromEntries([...new FormData(f)].map(([k, v]) => [k, v || null]));
      const l = await api("/api/leads", { method: "POST", body });
      closeModal();
      leads.meta = null;
      changed();
      openLead(l.id);
    });
  };
}

// --- Call mode ---------------------------------------------------------------------------
const OUTCOMES = ["no_answer", "voicemail", "callback", "interested", "not_interested", "wrong_number"];
export const calls = {
  kind: store.get("callKind", "agency"), items: [], idx: 0, done: new Set(), cur: null, session: { calls: 0, booked: 0 }, timer: null, secs: 0,
  async render() {
    this.items = (await api(`/api/calls?kind=${this.kind}`)).items;
    this.done.clear();
    const first = store.get("callFirst", null);
    if (first) {
      store.set("callFirst", null);
      const i = this.items.findIndex((x) => x.id === first);
      if (i > 0) this.items.unshift(...this.items.splice(i, 1));
      if (i < 0) {
        const l = await api(`/api/leads/${first}`).catch(() => null);
        if (l?.best_phone) this.items.unshift({ ...l, phone: l.best_phone, email: l.best_email, why: "you picked them" });
      }
      this.idx = 0;
    }
    this.idx = Math.min(this.idx, Math.max(this.items.length - 1, 0));
    await this.show();
  },
  async show() {
    const l = this.items[this.idx];
    this.stopTimer();
    this.cur = l ? await api(`/api/leads/${l.id}`).catch(() => null) : null;
    this.draw();
  },
  stopTimer() { clearInterval(this.timer); this.timer = null; this.secs = 0; },
  draw() {
    const l = this.items[this.idx], d = this.cur, s = this.session;
    const left = this.items.filter((x) => !this.done.has(x.id)).length;
    const head = `<div class="topline"><div><h1>Call mode</h1><div class="sub row" style="gap:10px;margin-top:8px">${segButtons(this.kind)}<span class="small muted">${left} to call · 9:30–12:00 and 14:00–17:30 work best</span></div></div><span class="grow"></span>
        <div class="session"><span><b>${s.calls}</b>calls this session</span><span><b>${s.booked}</b>interested</span></div></div>`;
    if (!l) {
      C().innerHTML = `<div class="view">${head}<section class="card"><p class="empty" style="padding:24px">${this.items.length ? "Everyone in this queue is done. Well played." : "Nobody to call in this segment: no phone that reaches them directly. Try another segment."}</p></section></div>`;
      C().onclick = (e) => this.click(e);
      return;
    }
    const first = firstName(l), ph = l.kind === "photographer";
    const photo = d?.photos?.find((p) => p.lean_deg != null && p.lean_deg >= 1);
    C().innerHTML = `<div class="view" style="max-width:1500px">${head}
      <div class="call">
        <section class="card cq"><div class="card-h" style="padding:10px 10px 6px"><h2>Queue</h2></div>${this.items.map((q, i) => `<div class="qitem ${i === this.idx ? "cur" : ""} ${this.done.has(q.id) ? "gone" : ""}" data-go="${i}">${av(q)}<div style="min-width:0"><div class="t">${esc(q.person || q.name)}</div><div class="s">${esc(q.why)} · ${esc(q.city || "")}</div></div></div>`).join("")}</section>
        <section class="card dial">
          <div class="row" style="flex-wrap:nowrap">${av(l)}<div style="min-width:0"><h2 class="big">${esc(l.person || l.name)}</h2><div class="meta">${l.person ? esc(l.name) + " · " : ""}${esc(l.city || "")} ${pill(l.status)} <span class="tag">${esc(l.why)}</span></div></div></div>
          ${l.next_action === "call" && l.last_contacted_at ? `<div class="next">${small(I.clock, 16)}<span>They asked you to call back · ${esc(at(l.next_action_at))}</span></div>` : ""}
          <div class="phone"><a class="dialbtn" href="${telHref(l.phone)}" data-dial aria-label="Dial ${esc(l.phone)}">${I.tel}</a><div><div class="n">${esc(l.phone)}</div><div class="timer" id="timer">Rings through your phone app (Phone Link on Windows)</div></div></div>
          ${photo ? `<img src="/api/photos/${photo.id}/marked.jpg" alt="Their photo with the lean drawn on it" style="width:100%;border-radius:12px;display:block">` : d ? `<div class="reasons">${(d.score_reasons || []).slice(0, 4).map((r) => `<span>${esc(r)}</span>`).join("")}</div>` : ""}
          <input class="input" id="call-note" placeholder="A note about the call (optional)" aria-label="Note">
          <div><h2 style="margin-bottom:8px">How did it go?</h2><div class="outcomes">
            <button data-out="no_answer"><kbd>1</kbd><b>No answer</b><span>back tomorrow morning</span></button>
            <button data-out="voicemail"><kbd>2</kbd><b>Voicemail</b><span>writes a short email</span></button>
            <button data-out="callback"><kbd>3</kbd><b>Call back</b><span>pick a time</span></button>
            <button class="good" data-out="interested"><kbd>4</kbd><b>Interested</b><span>moves to Interview</span></button>
            <button data-out="not_interested"><kbd>5</kbd><b>Not interested</b><span>marks lost</span></button>
            <button data-out="wrong_number"><kbd>6</kbd><b>Wrong number</b><span>flags the phone</span></button>
          </div>
          <form class="inline-form" id="cb-form" hidden><input class="input" type="datetime-local" name="at" value="${toLocalInput(tomorrowAt(14))}" aria-label="Call back at"><button class="btn sm primary">Set call-back</button></form></div>
          <div class="row"><button class="btn ghost sm" data-open="${l.id}">Open lead file</button><button class="btn ghost sm" data-skipcall>Skip for now</button></div>
        </section>
        <section class="card script"><h2>Script · ${ph ? "photographer" : "agency"}</h2>
          <span class="step">Open</span><p>Bonjour${first ? ` ${esc(first)}` : ""}, Yacine de Veralume, à Strasbourg. Je vous appelle parce que j'ai regardé les photos de ${ph ? "votre site" : "vos annonces"}. Vous avez deux minutes ?</p>
          <span class="step">Hook</span><p>${l.lean_median != null && l.lean_median >= 1 ? `J'ai mesuré ${l.photos_measured ? `${l.photos_measured} de` : "quelques-unes de"} vos photos : les verticales penchent en moyenne de <span class="var">${leanTxt(l.lean_median)}</span>. C'est l'effet grand-angle, les murs ont l'air de tomber.` : "Je travaille sur la retouche des photos immobilières : redresser les verticales, corriger la perspective."}</p>
          <span class="step">Offer</span><p>Je lance un service qui corrige ça en 24 h. Je cherche ${ph ? "une dizaine de photographes" : "quelques agences de la région"} pour un pilote gratuit : vous m'envoyez des photos, je vous les renvoie corrigées.</p>
          <span class="step">Ask</span><p>On se voit 20 minutes cette semaine, que je vous montre le résultat sur vos propres photos ?</p>
          <div class="obj"><b>« On a déjà un photographe. »</b>Très bien, je travaille aussi avec les photographes : c'est lui qui gagne du temps.</div>
          <div class="obj"><b>« Envoyez-moi un mail. »</b>Avec plaisir, je vous envoie votre photo corrigée cet après-midi.${l.email ? ` C'est bien <span class="mono">${esc(l.email)}</span> ?` : " À quelle adresse ?"}</div>
          <div class="obj"><b>« C'est combien ? »</b>Le pilote est gratuit. Ensuite, un abonnement mensuel selon votre volume ; d'abord, voyons si le résultat vous plaît.</div>
        </section>
      </div></div>`;
    C().onclick = (e) => this.click(e);
    $("#cb-form").onsubmit = (e) => { e.preventDefault(); this.outcome("callback", fromLocalInput(e.target.at.value)); };
  },
  async outcome(o, callbackAt = null) {
    const l = this.items[this.idx];
    if (!l) return;
    if (o === "callback" && !callbackAt) { const f = $("#cb-form"); f.hidden = false; f.at.focus(); return; }
    try {
      const r = await api(`/api/leads/${l.id}/call`, { method: "POST", body: { outcome: o, note: $("#call-note")?.value || "", callback_at: callbackAt } });
      this.session.calls++;
      if (o === "interested") { this.session.booked++; confetti(); }
      this.done.add(l.id);
      toast(r.message);
      const next = this.items.findIndex((x, i) => i > this.idx && !this.done.has(x.id));
      const any = this.items.findIndex((x) => !this.done.has(x.id));
      this.idx = next >= 0 ? next : any >= 0 ? any : this.items.length;
      await this.show();
    } catch (e) { fail(e); }
  },
  async click(e) {
    const t = e.target.closest("button, a, [data-go]");
    if (!t) return;
    const d = t.dataset;
    if (d.seg) { this.kind = d.seg; store.set("callKind", d.seg); this.idx = 0; return this.render(); }
    if (d.go !== undefined) { this.idx = +d.go; return this.show(); }
    if (d.out) return this.outcome(d.out);
    if (d.open) return openLead(+d.open);
    if (d.skipcall !== undefined) { this.idx = (this.idx + 1) % this.items.length; return this.show(); }
    if (d.dial !== undefined && !this.timer) {
      this.secs = 0;
      this.timer = setInterval(() => { this.secs++; const el = $("#timer"); if (el) el.textContent = `on the call · ${Math.floor(this.secs / 60)}:${String(this.secs % 60).padStart(2, "0")}`; }, 1000);
    }
  },
  key(e) {
    if (/^[1-6]$/.test(e.key)) { this.outcome(OUTCOMES[+e.key - 1]); return true; }
    return false;
  },
  leave() { this.stopTimer(); },
};

// --- Pipeline ----------------------------------------------------------------------------
export const pipeline = {
  kind: store.get("pipeKind", ""),
  async render() {
    const p = await api(`/api/pipeline${this.kind ? `?kind=${this.kind}` : ""}`);
    const cols = [...STAGES, "lost"], cnt = p.counts;
    const reached = (i) => STAGES.slice(i).reduce((a, s) => a + (cnt[s] || 0), 0);
    C().innerHTML = `<div class="view wide">
      <div class="topline"><div><h1>Pipeline</h1><div class="sub">Drag a card to move it. Emails, replies and calls move leads on their own.</div></div><span class="grow"></span>
        <div class="seg" role="group" aria-label="Segment"><button data-pk="" aria-pressed="${!this.kind}">All</button>${SEGS.map((k) => `<button data-pk="${k}" aria-pressed="${this.kind === k}">${KINDS_PL[k]}</button>`).join("")}</div></div>
      <div class="board">${cols.map((s, i) => {
        const n = cnt[s] || 0;
        const conv = i >= 2 && i < STAGES.length && reached(i - 1) ? Math.round((reached(i) / reached(i - 1)) * 100) : null;
        return `<div class="col" data-col="${s}"><div class="col-h">${pill(s)} <span class="n">${n.toLocaleString("en")}</span>${conv != null ? `<span class="conv" title="Share of the previous stage that got this far">${conv}%</span>` : ""}</div>
          ${p.columns[s].map((l) => `<div class="kc k-${esc(l.kind)}" draggable="true" data-card="${l.id}" data-from="${s}" data-open="${l.id}"><b>${kindIcon(l.kind)} ${esc(l.person || l.name)}</b>${l.person ? `<div class="m">${esc(l.name)}</div>` : ""}${(l.labels || []).length || l.id_ ? `<div class="idrow">${labelChips(l)}${idChips(l, { max: 1, facts: false })}</div>` : ""}<div class="m"><span class="score ${scoreCls(l.score)}" style="width:30px;height:20px;font-size:11px">${l.score}</span><span>${esc(l.city || "")}</span><span class="grow"></span><span>${esc(when(l.last_contacted_at))}</span></div></div>`).join("")}
          ${s === "new" && n > p.columns.new.length ? `<a class="small dim" href="#leads" style="padding:4px">+ ${(n - p.columns.new.length).toLocaleString("en")} more in Leads</a>` : ""}
          ${!p.columns[s].length && s !== "new" ? `<p class="small dim" style="padding:4px;margin:0">Nobody here yet.</p>` : ""}</div>`;
      }).join("")}</div></div>`;
    C().onclick = (e) => {
      const b = e.target.closest("[data-pk], [data-open]");
      if (!b) return;
      if (b.dataset.pk !== undefined) { this.kind = b.dataset.pk; store.set("pipeKind", this.kind); return this.render(); }
      openLead(+b.dataset.open);
    };
    $$(".kc").forEach((k) => k.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/plain", `${k.dataset.card}|${k.dataset.from}`)));
    $$(".col").forEach((c) => {
      c.addEventListener("dragover", (e) => { e.preventDefault(); c.classList.add("over"); });
      c.addEventListener("dragleave", () => c.classList.remove("over"));
      c.addEventListener("drop", async (e) => {
        e.preventDefault();
        c.classList.remove("over");
        const [id, from] = e.dataTransfer.getData("text/plain").split("|");
        const to = c.dataset.col;
        if (!id || from === to) return;
        try {
          const l = await api(`/api/leads/${id}`, { method: "PATCH", body: { status: to } });
          toast(`${l.person || l.name} → ${STATUS[to]}`, { undo: () => api(`/api/leads/${id}`, { method: "PATCH", body: { status: from } }) });
          if (to === "pilot" || to === "customer") confetti();
          changed();
        } catch (err) { fail(err); }
      });
    });
  },
};

// --- Pilots: every free test, from "send me photos" to the decision --------------------------
const PILOT_HINTS = {
  interested: "Waiting for their photos", photos: "Download and retouch", retouching: "Upload the results",
  sent_back: "Waiting for their opinion", feedback: "Decide together", customer: "Paying, or about to", lost: "Not now, and why",
};
export const pilots = {
  async render() {
    const p = await api("/api/pilots");
    const card = (l) => `<div class="kc pc ${l.urgent ? "urgent" : ""}" draggable="true" data-card="${l.id}" data-from="${l.pilot_stage}" data-open="${l.id}">
      <div class="row" style="gap:6px;flex-wrap:nowrap"><b style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(l.person || l.name)}</b><span class="small dim">${esc(l.city || "")}</span></div>
      ${l.person ? `<div class="m">${esc(l.name)}</div>` : ""}
      ${l.thumbs.length ? `<div class="pc-th">${l.thumbs.map((t) => `<img src="${t}" alt="" loading="lazy">`).join("")}</div>` : ""}
      ${l.next ? `<div class="pc-next">${l.urgent ? `<span class="dot-u" aria-label="Needs you"></span>` : ""}${esc(l.next)}</div>` : ""}
      <div class="m"><span>${l.counts.received ? `${l.counts.retouched}/${l.counts.received} retouched` : ""}</span><span class="grow"></span><span>${l.pilot_due_at && ["photos", "retouching"].includes(l.pilot_stage) ? `due ${esc(at(l.pilot_due_at).replace(/ \d\d:\d\d$/, ""))}` : l.waiting_days ? `${l.waiting_days} d` : ""}</span></div></div>`;
    C().innerHTML = `<div class="view wide">
      <div class="topline"><div><h1>Pilots</h1><div class="sub">Everyone who agreed to a free test. Their reply with photos brings them here, and the steps move on their own as you work.</div></div><span class="grow"></span>
        <div class="pl-stats"><div class="hot"><b>${p.need}</b><span>need you now</span></div><div><b>${p.running}</b><span>running</span></div><div><b>${p.photos.received}</b><span>photos received</span></div><div><b>${p.photos.sent}</b><span>sent back</span></div><div><b>${p.customers}</b><span>customer${p.customers === 1 ? "" : "s"}</span></div></div></div>
      ${p.running || p.customers || p.columns.lost.length ? `<div class="board pl">${p.stages.map((s) => `<div class="col" data-col="${s.key}"><div class="col-h"><b>${esc(s.label)}</b><span class="n">${p.columns[s.key].length}</span></div>
          <p class="small dim" style="margin:-4px 4px 4px">${esc(PILOT_HINTS[s.key])}</p>
          ${p.columns[s.key].map(card).join("") || `<p class="small dim" style="padding:4px;margin:0">Nobody here.</p>`}</div>`).join("")}</div>`
      : `<section class="card"><h3 style="margin-top:0">No free test yet</h3><p class="muted" style="margin:0 0 10px">When a lead replies with photos attached, they land here on their own, with their photos saved in their file. You can also open a lead and click <b>They said yes</b>.</p>
          <p class="small muted" style="margin:0">Then: download their photos, retouch them with Veralume, drop the results in their file, and send them back in one click. Each step shows here.</p></section>`}</div>`;
    C().onclick = (e) => {
      const b = e.target.closest("[data-open]");
      if (b) openLead(+b.dataset.open);
    };
    $$(".kc").forEach((k) => k.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/plain", `${k.dataset.card}|${k.dataset.from}`)));
    $$(".col").forEach((c) => {
      c.addEventListener("dragover", (e) => { e.preventDefault(); c.classList.add("over"); });
      c.addEventListener("dragleave", () => c.classList.remove("over"));
      c.addEventListener("drop", async (e) => {
        e.preventDefault();
        c.classList.remove("over");
        const [id, from] = e.dataTransfer.getData("text/plain").split("|");
        const to = c.dataset.col;
        if (!id || from === to) return;
        try {
          await api(`/api/leads/${id}/pilot`, { method: "PUT", body: { stage: to } });
          toast(`Moved to ${p.stages.find((s) => s.key === to).label}`, { undo: () => api(`/api/leads/${id}/pilot`, { method: "PUT", body: { stage: from } }) });
          if (to === "customer") confetti();
          changed();
        } catch (err) { fail(err); }
      });
    });
  },
};

// --- Campaigns and templates ---------------------------------------------------------------
export const campaigns = {
  async render() {
    const [c, t] = await Promise.all([api("/api/campaigns"), api("/api/templates")]);
    this.c = c; this.t = t;
    const mb = c.mailbox;
    const tplName = (id) => t.items.find((x) => x.id === id)?.name || "the best template for each lead";
    const card = (x) => {
      const s = x.stats, total = Math.max(s.matching + s.emailed, 1);
      const label = { running: "Running", paused: "Paused", draft: "Draft" }[x.status] || x.status;
      return `<section class="card camp">
        <div class="row"><h3>${esc(x.name)}</h3><span class="pill ${x.status === "running" ? "st-pilot" : "st-new"}">${label}</span><span class="grow"></span>
          <button class="btn sm" data-edit-camp="${x.id}">Edit</button>
          <button class="btn sm ${x.status === "running" ? "" : "primary"}" data-camp-status="${x.id}" data-to="${x.status === "running" ? "paused" : "running"}">${x.status === "running" ? "Pause" : "Start"}</button></div>
        <div class="small muted">${esc(KINDS_PL[x.kind])}${x.filters.dep ? ` in ${esc(x.filters.dep)}` : ""}${x.filters.min_lean ? `, lean ≥ ${esc(x.filters.min_lean)}°` : ""}${x.filters.measured ? ", photos measured" : ""} · first email: ${x.test ? `<a href="#tests">A/B test “${esc(x.test)}”</a>` : esc(tplName(x.template_id))} · ${x.daily_cap} a day</div>
        <div class="prog" title="${s.replied} replied, ${s.emailed - s.replied} no reply yet"><span style="width:${(s.replied / total) * 100}%;background:var(--warn)"></span><span style="width:${((s.emailed - s.replied) / total) * 100}%;background:var(--emerald)"></span></div>
        <div class="stats"><div><b>${s.emailed}</b><span>emailed</span></div><div><b>${s.replied}</b><span>replied</span></div><div><b>${s.emailed ? Math.round((s.replied / s.emailed) * 100) : 0}%</b><span>reply rate</span></div><div><b>${s.matching}</b><span>with their email</span></div></div>
        ${s.steps.length ? `<div class="steps"><div class="h"><span>Step</span><span>Template</span><span class="num">Sent</span><span class="num">Replies</span></div>${s.steps.map((st) => `<div><span class="mono">${esc(STEP[st.step] || st.step)}</span><span>${esc(st.template)}</span><span class="num">${st.sent}</span><span class="num">${st.replies}</span></div>`).join("")}</div>` : ""}
        <div class="row"><span class="small muted" style="flex:1;min-width:200px">${x.status === "running" ? `Each working morning, the next ${x.daily_cap} first emails are written for your review in Today.` : "Start it, or write a batch now and review it in Today."}${s.waiting ? ` ${s.waiting} waiting.` : ""}</span><button class="btn sm" data-prepare="${x.id}">Write ${x.daily_cap} now</button></div></section>`;
    };
    C().innerHTML = `<div class="view">
      <div class="topline"><div><h1>Campaigns</h1><div class="sub">One segment, one first email, its follow-ups, and a daily cap that keeps your mailbox healthy.</div></div><span class="grow"></span><button class="btn primary" data-new-camp>${small(I.plus)} New campaign</button></div>
      <div class="grid2">
        ${c.items.map(card).join("") || `<section class="card camp"><h3>No campaign yet</h3><p class="small muted" style="margin:0">A campaign writes the next batch of first emails every working morning, new and small businesses first. You review them in Today, approve, and they go out a few minutes apart.</p><div><button class="btn primary" data-new-camp data-preset="photographer">Start with real-estate photographers</button></div></section>`}
        <section class="card camp"><h3>Mailbox</h3>
          <div class="kv"><span class="k">Sends from</span><span class="v">${esc(mb.sender || "not connected")}</span>${mb.configured ? `<span class="pill st-pilot">${mb.dry_run ? "test mode" : "connected"}</span>` : `<a class="btn sm" href="#settings">Connect</a>`}
            <span class="k">Today</span><span class="v">${mb.sent_today} of ${mb.cap} sent</span><span></span>
            <span class="k">Approved</span><span class="v">${mb.approved_waiting} waiting${mb.approved_waiting ? (!mb.configured ? ", until the mailbox is connected" : mb.in_window ? ", going out now, spaced" : `, from ${mb.hours[0]}:00 next working day`) : ""}</span><span></span>
            <span class="k">Replies</span><span class="v">${mb.imap ? (mb.inbox.last_error ? `<span class="flag">${esc(mb.inbox.last_error)}</span>` : `checked ${esc(when(mb.inbox.last_poll) || "soon")}`) : "not read (no IMAP set)"}</span><span></span></div>
          <p class="small muted" style="margin:0">Every email names you and Veralume, says where the address came from, and offers a one-word opt-out. A "stop" reply marks the lead Do not contact.</p></section>
      </div>
      <section class="card" style="margin-top:18px"><div class="card-h"><h2>Templates</h2><span class="grow"></span><button class="btn sm" data-new-tpl>${small(I.plus)} Template</button></div>
        ${t.items.map((x) => `<div class="tpl-row" data-tpl="${x.id}" ${x.archived ? 'style="opacity:.5"' : ""}><div style="min-width:0"><b>${esc(x.name)}</b><div class="small muted" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(x.subject)} · ${esc(x.body.replace(/\s+/g, " ").slice(0, 100))}…</div></div><span class="tag">${esc(STEP[x.step] || x.step)} · ${esc(x.segment === "any" ? "all" : KINDS_PL[x.segment] || x.segment)}</span><span class="small mono">${x.sent ? `${x.sent} sent · ${Math.round((x.replied / x.sent) * 100)}%` : "not used yet"}</span></div>`).join("")}
      </section></div>`;
    C().onclick = (e) => this.click(e);
  },
  async click(e) {
    const b = e.target.closest("button, [data-tpl]");
    if (!b) return;
    const d = b.dataset;
    if (d.newCamp !== undefined) return this.editCamp(null, d.preset);
    if (d.editCamp) return this.editCamp(this.c.items.find((x) => x.id === +d.editCamp));
    if (d.campStatus) return busy(b, async () => { await api(`/api/campaigns/${d.campStatus}`, { method: "PATCH", body: { status: d.to } }); toast(d.to === "running" ? "Started. Today's batch is written within a minute, inside working hours." : "Paused. Approved first emails went back to review."); this.render(); });
    if (d.prepare) return busy(b, async () => { const x = this.c.items.find((y) => y.id === +d.prepare); const r = await api(`/api/campaigns/${x.id}/prepare`, { method: "POST", body: { n: x.daily_cap } }); toast(r.written ? `${r.written} emails written. Review them in Today.` : "Nobody left to write to in this campaign."); changed(); });
    if (d.newTpl !== undefined) return this.editTpl(null);
    if (d.tpl) return this.editTpl(this.t.items.find((x) => x.id === +d.tpl));
  },
  editCamp(x, preset) {
    const tpls = this.t.items.filter((t) => t.step === 0 && !t.archived);
    const v = x || { name: preset === "photographer" ? "Real-estate photographers · France" : "", kind: preset || "photographer", filters: {}, template_id: null, daily_cap: 20, status: "draft" };
    const sheet = openModal(`<div class="sheet-h"><h2>${x ? "Edit campaign" : "New campaign"}</h2><button class="btn ghost sm" data-close aria-label="Close">${small(I.x)}</button></div>
      <form class="sheet-b"><div class="form-grid">
        <label class="full">Name<input class="input" name="name" value="${esc(v.name)}" required autofocus></label>
        <label>Segment<select class="select" name="kind" ${x ? "disabled" : ""}>${SEGS.map((k) => `<option value="${k}" ${k === v.kind ? "selected" : ""}>${KINDS_PL[k]}</option>`).join("")}</select></label>
        <label>First email<select class="select" name="template_id"><option value="">The best template for each lead</option>${tpls.map((t) => `<option value="${t.id}" ${t.id === v.template_id ? "selected" : ""}>${esc(t.name)}</option>`).join("")}</select></label>
        <label>Département (optional)<input class="input" name="dep" value="${esc(v.filters.dep || "")}" placeholder="67"></label>
        <label>Lean at least, in degrees (optional)<input class="input" name="min_lean" type="number" step="0.5" min="0" value="${esc(v.filters.min_lean ?? "")}"></label>
        <label>Emails a day<input class="input" name="daily_cap" type="number" min="1" max="60" value="${v.daily_cap}"></label>
        <label class="check" style="align-self:end"><input type="checkbox" class="chk" name="measured" ${v.filters.measured ? "checked" : ""}> Only leads with measured photos</label></div>
        <p class="small dim" style="margin:0">Only leads with their own email, never contacted, not marked do-not-contact. New and small businesses come first, then by score.</p>
        <div class="row"><span class="grow"></span><button class="btn" type="button" data-close>Cancel</button><button class="btn primary">${x ? "Save" : "Create"}</button></div></form>`, { narrow: true });
    sheet.onclick = (e) => { if (e.target.closest("[data-close]")) closeModal(); };
    sheet.onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target;
      const filters = {};
      if (f.dep.value.trim()) filters.dep = f.dep.value.trim();
      if (f.min_lean.value) filters.min_lean = +f.min_lean.value;
      if (f.measured.checked) filters.measured = true;
      const body = { name: f.name.value, filters, template_id: f.template_id.value ? +f.template_id.value : null, daily_cap: +f.daily_cap.value };
      if (!x) body.kind = f.kind.value;
      await busy(f.querySelector(".primary"), async () => {
        await api(x ? `/api/campaigns/${x.id}` : "/api/campaigns", { method: x ? "PATCH" : "POST", body });
        closeModal();
        this.render();
      });
    };
  },
  // x: the template to edit (null for a new one). copy: a template to start a new one from.
  // onSaved: called with the saved template instead of re-rendering Campaigns.
  async editTpl(x, { copy = null, segment = null, step = null, onSaved = null } = {}) {
    const [ai, lib] = await Promise.all([api("/api/ai").catch(() => null), api("/api/assets").catch(() => ({ items: [] }))]);
    const v = x || (copy ? { ...copy, name: `${copy.name} (new version)`, archived: false }
      : { name: "", segment: segment || "any", step: step ?? 0, subject: "", body: "Bonjour {prenom},\n\n\n\n{signature}", attach_photo: true, asset_ids: [], archived: false });
    let picked = [...(v.asset_ids || [])];
    const library = lib.items;
    let previewLead = null;
    const VARS = ["prenom", "agence", "ville", "lean", "nb_images", "signature"];
    const sheet = openModal(`<div class="sheet-h"><h2>${x ? "Edit template" : "New template"}</h2><button class="btn ghost sm" data-close aria-label="Close">${small(I.x)}</button></div>
      <form class="tpl-ed">
        <div class="tpl-l"><div class="form-grid">
          <label class="full">Name<input class="input" name="name" value="${esc(v.name)}" required></label>
          <label>For<select class="select" name="segment"><option value="any">All segments</option>${SEGS.map((k) => `<option value="${k}" ${k === v.segment ? "selected" : ""}>${KINDS_PL[k]}</option>`).join("")}</select></label>
          <label>Used as<select class="select" name="step">${[[0, "First email"], [1, "Follow-up 1"], [2, "Follow-up 2"], [-1, "Reply"]].map(([s, t]) => `<option value="${s}" ${s === v.step ? "selected" : ""}>${t}</option>`).join("")}</select></label>
          <label class="full">Subject<input class="input" name="subject" value="${esc(v.subject)}" required></label>
          <label class="full">Message<textarea class="input mailbody" name="body" lang="fr" required>${esc(v.body)}</textarea></label>
          <div class="full ins-row"><span class="small muted">Insert</span>${VARS.map((k) => `<button type="button" class="chip mono" data-ins="{${k}}">{${k}}</button>`).join("")}</div>
          <div class="full">${aiBar(ai)}</div></div>
          <div class="tpl-imgs"><div class="row"><h4>Images sent with every email</h4><span class="small muted" data-img-n></span><span class="grow"></span>
            <label class="btn sm">${small(I.plus)} Upload<input type="file" accept="image/jpeg,image/png,image/webp" multiple data-asset-upload hidden></label></div>
            <div class="img-picked" data-picked></div>
            <details><summary class="small">From your library <span data-lib-n></span></summary><div class="img-lib" data-lib></div></details>
            <p class="small dim" style="margin:0">Before/after pairs work best side by side in one image, about 2000 px wide. Keep the total under 5 MB.</p></div>
          <div class="row"><label class="check"><input type="checkbox" class="chk" name="attach_photo" ${v.attach_photo ? "checked" : ""}> Also attach the lead's own photo, marked</label>
            <label class="check"><input type="checkbox" class="chk" name="archived" ${v.archived ? "checked" : ""}> Archived (not offered)</label></div>
        </div>
        <div class="tpl-r"><div class="row"><h4 style="flex:1">What a real lead gets</h4><select class="select" data-prev-lead aria-label="Preview for"></select></div>
          <div class="pv" data-preview><p class="small muted">Loading a lead…</p></div>
          <p class="small dim" style="margin:0">{prenom} is left out when the first name isn't known: “Bonjour {prenom},” becomes “Bonjour,”.</p></div>
        <div class="tpl-foot">${x ? `<button class="btn" type="button" data-save-copy title="Keeps this one as it is">Save as a new template</button>` : ""}<span class="grow"></span><button class="btn" type="button" data-close>Cancel</button><button class="btn primary">Save</button></div></form>`, { cls: "wide" });
    const tf = sheet.querySelector("form");
    wireAiBar(sheet.querySelector(".ai-bar"), {
      subject: tf.subject, body: tf.body, extra: () => ({ segment: tf.segment.value, step: +tf.step.value }),
    });
    const thumb = (id) => library.find((y) => y.id === id);
    const drawImgs = () => {
      const tot = picked.reduce((s, id) => s + (thumb(id)?.size || 0), 0);
      $("[data-img-n]", sheet).innerHTML = picked.length ? `${picked.length} · ${(tot / 1e6).toFixed(1)} MB${tot > 5e6 ? ` <span class="flag">heavy</span>` : ""}` : "none yet";
      $("[data-picked]", sheet).innerHTML = picked.length ? picked.map((id, i) => { const a = thumb(id); return a ? `<figure><img src="${a.thumb}" alt="${esc(a.name)}"><figcaption><span>${i + 1} · ${esc(a.name)}</span>${i ? `<button type="button" data-left="${id}" aria-label="Move earlier">←</button>` : ""}<button type="button" data-unpick="${id}" aria-label="Remove ${esc(a.name)}">${small(I.x)}</button></figcaption></figure>` : ""; }).join("")
        : `<p class="small muted" style="margin:0">No image. Upload before/after examples, or pick them from your library.</p>`;
      const rest = library.filter((a) => !picked.includes(a.id));
      $("[data-lib-n]", sheet).textContent = `(${rest.length})`;
      $("[data-lib]", sheet).innerHTML = rest.map((a) => `<button type="button" data-pick="${a.id}" title="Add ${esc(a.name)}"><img src="${a.thumb}" alt="${esc(a.name)}"><span>${esc(a.name)}</span></button>`).join("") || `<p class="small muted" style="margin:0">Nothing else in your library.</p>`;
    };
    let pvTimer;
    const preview = () => {
      clearTimeout(pvTimer);
      pvTimer = setTimeout(async () => {
        try {
          const p = await api("/api/templates/preview", { method: "POST", body: { subject: tf.subject.value, body: tf.body.value, segment: tf.segment.value, asset_ids: picked, lead_id: previewLead } });
          const sel = $("[data-prev-lead]", sheet);
          if (!sel.options.length && p.choices.length) sel.innerHTML = p.choices.map((c) => `<option value="${c.id}">${esc(c.label)}</option>`).join("");
          if (!p.lead) { $("[data-preview]", sheet).innerHTML = `<p class="small muted">No lead with a first name in this segment yet.</p>`; return; }
          const atts = picked.map(thumb).filter(Boolean);
          $("[data-preview]", sheet).innerHTML = `<div class="pv-h"><span>To</span><b>${esc(p.to)}</b><span>Subject</span><b>${esc(p.subject)}</b></div>
            <div class="pv-b">${esc(p.body)}<div class="optout">--\n${esc(p.footer)}</div></div>
            ${atts.length ? `<div class="pv-a">${atts.map((a, i) => `<img src="${a.thumb}" alt="Attachment ${i + 1}">`).join("")}</div>` : ""}`;
        } catch (e) { $("[data-preview]", sheet).innerHTML = `<p class="small muted">${esc(e.message)}</p>`; }
      }, 250);
    };
    drawImgs();
    preview();
    sheet.oninput = (e) => { if (e.target.matches("[name=subject], [name=body]")) preview(); };
    sheet.onchange = async (e) => {
      const t = e.target;
      if (t.matches("[name=segment]")) { $("[data-prev-lead]", sheet).innerHTML = ""; previewLead = null; preview(); }
      if (t.matches("[data-prev-lead]")) { previewLead = +t.value; preview(); }
      if (t.matches("[data-asset-upload]")) {
        for (const file of [...t.files]) {
          try {
            toast(`Uploading ${file.name}…`);
            const r = await fetch(`/api/assets?name=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
            const j = await r.json();
            if (!r.ok) throw new Error(`${file.name}: ${j.detail}`);
            library.push(j);
            picked.push(j.id);
          } catch (err) { fail(err); }
        }
        t.value = "";
        drawImgs();
        preview();
      }
    };
    const save = async (f, asNew, btn) => {
      const body = { name: f.name.value, segment: f.segment.value, step: +f.step.value, subject: f.subject.value, body: f.body.value, attach_photo: f.attach_photo.checked, asset_ids: picked, archived: f.archived.checked };
      if (asNew && body.name === x.name) body.name = `${x.name} (new version)`;
      await busy(btn, async () => {
        const saved = await api(x && !asNew ? `/api/templates/${x.id}` : "/api/templates", { method: x && !asNew ? "PUT" : "POST", body });
        closeModal();
        toast("Template saved");
        if (onSaved) onSaved(saved); else this.render();
      });
    };
    sheet.onclick = (e) => {
      if (e.target.closest("[data-close]")) closeModal();
      const c = e.target.closest("[data-save-copy]");
      if (c) save(sheet.querySelector("form"), true, c);
      const b = e.target.closest("button");
      if (!b) return;
      const d = b.dataset;
      if (d.ins) {
        const ta = document.activeElement === tf.subject ? tf.subject : tf.body;
        const at_ = ta.selectionStart ?? ta.value.length;
        ta.value = ta.value.slice(0, at_) + d.ins + ta.value.slice(ta.selectionEnd ?? at_);
        ta.focus();
        ta.selectionStart = ta.selectionEnd = at_ + d.ins.length;
        preview();
      } else if (d.pick) { picked.push(+d.pick); drawImgs(); preview(); }
      else if (d.unpick) { picked = picked.filter((y) => y !== +d.unpick); drawImgs(); preview(); }
      else if (d.left) { const i = picked.indexOf(+d.left); [picked[i - 1], picked[i]] = [picked[i], picked[i - 1]]; drawImgs(); preview(); }
    };
    // Insert buttons go into the field you were last typing in.
    let lastField = tf.body;
    tf.subject.addEventListener("focus", () => (lastField = tf.subject));
    tf.body.addEventListener("focus", () => (lastField = tf.body));
    sheet.addEventListener("mousedown", (e) => { if (e.target.closest("[data-ins]")) { e.preventDefault(); lastField.focus(); } });
    sheet.onsubmit = (e) => { e.preventDefault(); save(e.target, false, e.target.querySelector(".primary")); };
  },
};

// --- A/B tests ---------------------------------------------------------------------------
const AB_COL = ["var(--emerald)", "var(--blue)", "var(--violet)", "var(--warn)"];
const pct = (v, d = 0) => (v == null ? "–" : `${(v * 100).toFixed(d)} %`);
const VERDICT = { winner: "ok", early: "warn", leaning: "info", tie: "info", waiting: "info" };

export const tests = {
  draft: null, // the "new test" form, kept while a version is being written
  async render() {
    const x = await api("/api/experiments");
    this.x = x;
    const running = x.items.filter((e) => e.status === "running");
    const done = x.items.filter((e) => e.status !== "running");
    C().innerHTML = `<div class="view">
      <div class="topline"><div><h1>A/B tests</h1><div class="sub">Send two to four versions of your first email to the same kind of lead, and see which one gets more replies.</div></div><span class="grow"></span><button class="btn primary" data-new-test>${small(I.plus)} New test</button></div>
      ${running.map((e) => this.card(e)).join("") || `<section class="card ab"><h3 class="ab-title">No test running</h3>
        <p class="small muted" style="margin:0;max-width:62ch">While a test runs, every new lead of its segment gets one of the versions, chosen to keep them even. That applies to the composer and to campaigns that let the app pick the first email. Replies are counted per version, follow-ups included, and the app tells you when one version has really won rather than got lucky.</p>
        <div><button class="btn primary" data-new-test data-preset="photographer">Test a first email for photographers</button></div></section>`}
      ${done.length ? `<h2 class="ab-sec">Finished</h2>${done.map((e) => this.card(e)).join("")}` : ""}
      ${this.howto()}</div>`;
    C().onclick = (e) => this.click(e);
  },
  card(e) {
    const r = e.results, live = e.status === "running";
    const metric = e.metric === "reply" ? "replies" : "interested";
    const top = Math.max(0.1, ...r.versions.map((v) => v.high || 0)) * 1.1;
    const pos = (v) => `${Math.min(100, (v / top) * 100).toFixed(1)}%`;
    const row = (v, i) => {
      const win = e.winner_id === v.template_id || (live && r.verdict.state === "winner" && r.leader === v.letter);
      return `<div class="ab-row ${win ? "win" : ""}">
        <span class="ab-l" style="background:${AB_COL[i]}">${v.letter}</span>
        <div style="min-width:0"><b>${esc(v.name)}</b>${win ? ` <span class="pill st-pilot">${live ? "ahead" : "winner"}</span>` : ""}${v.archived ? ` <span class="tag">archived</span>` : ""}<div class="small muted ab-subj" title="${esc(v.subject)}">${esc(v.subject)}</div></div>
        <div class="ab-rate"><b>${pct(v.rate)}</b><span>${v.hits} of ${v.sent}${v.waiting ? ` · ${v.waiting} waiting` : ""}</span></div>
        <div class="ab-range c-range" title="Likely range of the true rate (90 %): ${pct(v.low, 1)} to ${pct(v.high, 1)}">${v.sent ? `<span class="band" style="left:${pos(v.low)};width:calc(${pos(v.high)} - ${pos(v.low)});background:${AB_COL[i]}"></span><span class="dot" style="left:${pos(v.rate || 0)};background:${AB_COL[i]}"></span>` : ""}</div>
        <div class="ab-best"><span class="mono">${v.sent ? pct(v.chance_best) : "–"}</span><div class="b"><span style="width:${(v.chance_best * 100).toFixed(1)}%;background:${AB_COL[i]}"></span></div></div></div>`;
    };
    const edited = r.versions.reduce((n, v) => n + v.edited, 0);
    const stopped = r.versions.reduce((n, v) => n + v.stopped, 0);
    const need = r.needed_each * r.versions.length;
    const facts = [
      `${r.sent} first emails sent${live && r.verdict.state !== "winner" ? ` of about ${need} needed to spot a version that ${e.metric === "reply" ? "doubles the reply rate" : "doubles interest"}` : ""}.`,
      live && r.days_left && r.verdict.state !== "winner" ? `At ${r.pace} a working day, that's about ${r.days_left} more working days.` : "",
      live && e.audience != null ? `${e.audience} ${KINDS_PL[e.kind].toLowerCase()} left to write to can enter.` : "",
      r.audience_rule || "",
      edited ? `${edited} ${edited > 1 ? "were" : "was"} changed by hand before sending; they still count for their version.` : "",
      stopped ? `${stopped} answered “stop”: not counted as replies.` : "",
    ].filter(Boolean);
    return `<section class="card ab" data-exp="${e.id}">
      <div class="ab-h"><h3 class="ab-title">${esc(e.name)}</h3><span class="pill ${live ? "st-pilot" : "st-new"}">${live ? "Running" : "Finished"}</span>
        <span class="small muted">${esc(KINDS_PL[e.kind])} · measures ${metric} · since ${esc(at(e.created_at))}${e.ended_at ? ` to ${esc(at(e.ended_at))}` : ""}</span><span class="grow"></span>
        ${live ? `<button class="btn sm" data-stop="${e.id}">Stop test…</button>` : ""}${live && !r.sent ? `<button class="btn ghost sm" data-del="${e.id}" title="Delete this test">${small(I.x)}</button>` : ""}</div>
      ${live || e.winner_id ? `<div class="note ${VERDICT[r.verdict.state]}">${esc(r.verdict.text)}</div>` : ""}
      <div class="ab-rows"><div class="ab-row h"><span></span><span>Version</span><span>${e.metric === "reply" ? "Reply rate" : "Interested"}</span><span class="c-range">Likely range</span><span>Chance best</span></div>${r.versions.map(row).join("")}</div>
      <p class="small muted" style="margin:0">${facts.map(esc).join(" ")}</p>
      <details class="ab-read"><summary class="small">Read the versions</summary>${r.versions.map((v, i) => `<div class="ab-ver"><span class="ab-l" style="background:${AB_COL[i]}">${v.letter}</span><div><b>${esc(v.subject)}</b><pre>${esc(v.body)}</pre></div></div>`).join("")}</details>
    </section>`;
  },
  howto() {
    return `<section class="card ab ab-how"><h3 class="ab-title" style="font-size:18px">Reading a test</h3>
      <ul class="small muted">
        <li><b>Chance best</b> is the probability that a version truly does best, given what came back so far. The app calls a winner at 95 % and at least 20 emails each.</li>
        <li><b>Likely range</b> is where the true rate probably sits. While two ranges overlap a lot, the gap can still be luck.</li>
        <li>Reply rates for cold emails sit around 2 to 10 %, so small tests can't separate close versions. Test bold differences (with or without their photo, short or long, lean or no lean), not a single word.</li>
        <li>Replies you mark by hand (status Replied, Interview…) count too, so the test works even when you send from your own mail app.</li>
      </ul></section>`;
  },
  async click(e) {
    const b = e.target.closest("button");
    if (!b) return;
    const d = b.dataset;
    if (d.newTest !== undefined) { this.draft = null; return this.newTest(d.preset); }
    if (d.stop) return this.stopTest(this.x.items.find((x) => x.id === +d.stop));
    if (d.del) return busy(b, async () => { await api(`/api/experiments/${d.del}`, { method: "DELETE" }); toast("Test deleted"); this.render(); });
  },
  newTest(preset) {
    const tpls = this.x.templates.filter((t) => !t.archived);
    const v = this.draft || { name: "", kind: preset || "photographer", metric: "reply", ids: [] };
    this.draft = v;
    const fits = (t) => t.segment === "any" || t.segment === v.kind || (v.kind === "independent_agent" && t.segment === "agency");
    const list = tpls.filter(fits);
    const sheet = openModal(`<div class="sheet-h"><h2>New A/B test</h2><button class="btn ghost sm" data-close aria-label="Close">${small(I.x)}</button></div>
      <form class="sheet-b"><div class="form-grid">
        <label class="full">Name<input class="input" name="name" value="${esc(v.name)}" placeholder="e.g. Their photo vs no photo"></label>
        <label>Segment<select class="select" name="kind">${SEGS.map((k) => `<option value="${k}" ${k === v.kind ? "selected" : ""}>${KINDS_PL[k]}</option>`).join("")}</select></label>
        <label>Measure<select class="select" name="metric">${Object.entries(this.x.metrics).map(([k, t]) => `<option value="${k}" ${k === v.metric ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></label></div>
        <div><div class="small" style="margin-bottom:6px"><b>Versions to compare</b> <span class="muted">· pick 2 to 4 first emails</span></div>
          <div class="ab-pick">${list.map((t) => `<label class="ab-opt"><input type="checkbox" class="chk" name="ids" value="${t.id}" ${v.ids.includes(t.id) ? "checked" : ""}>
            <div style="min-width:0"><b>${esc(t.name)}</b><div class="small muted ab-subj">${esc(t.subject)} · ${esc(t.body.replace(/\s+/g, " ").slice(0, 90))}…</div>${/\{lean\}/.test(t.body + t.subject) ? `<div class="small" style="color:var(--warn)">Quotes their lean: only leads with a measured lean of 1° or more can enter.</div>` : t.key === "ph_straight" ? `<div class="small" style="color:var(--warn)">Says their photos are straight: only leads measured straight can enter.</div>` : ""}</div>
            <button class="btn ghost sm" type="button" data-copy-tpl="${t.id}" title="Start a new version from this one">${small(I.copy)} New version</button></label>`).join("") || `<p class="small muted">No first email for this segment yet.</p>`}</div>
          <button class="btn sm" type="button" data-blank-tpl style="margin-top:8px">${small(I.plus)} Write a version from scratch</button></div>
        <div class="note info">Each new lead in ${esc(KINDS_PL[v.kind].toLowerCase())} gets one version. Change one big thing between versions so the result tells you something.</div>
        <div class="row"><span class="grow"></span><button class="btn" type="button" data-close>Cancel</button><button class="btn primary" disabled>Start test</button></div></form>`, { narrow: true });
    const f = sheet.querySelector("form");
    const sync = () => {
      v.name = f.name.value; v.metric = f.metric.value;
      v.ids = [...f.querySelectorAll("[name=ids]:checked")].map((i) => +i.value);
      const go = f.querySelector(".primary");
      go.disabled = v.ids.length < 2 || v.ids.length > 4;
      go.textContent = v.ids.length > 4 ? "Four versions at most" : v.ids.length < 2 ? "Pick at least two versions" : `Start test with ${v.ids.length} versions`;
    };
    sync();
    f.oninput = sync;
    f.kind.onchange = () => { v.kind = f.kind.value; v.ids = []; sync(); this.newTest(); };
    const writeVersion = (copy) => campaigns.editTpl(null, {
      copy, segment: v.kind, step: 0,
      onSaved: async (t) => { v.ids = [...v.ids, t.id].slice(-4); this.x = await api("/api/experiments"); this.newTest(); },
    });
    sheet.onclick = (e) => {
      if (e.target.closest("[data-close]")) { closeModal(); return; }
      const c = e.target.closest("[data-copy-tpl]");
      if (c) { e.preventDefault(); sync(); writeVersion(this.x.templates.find((t) => t.id === +c.dataset.copyTpl)); return; }
      if (e.target.closest("[data-blank-tpl]")) { sync(); writeVersion(null); }
    };
    f.onsubmit = async (e) => {
      e.preventDefault();
      sync();
      const names = v.ids.map((id) => tpls.find((t) => t.id === id)?.name).filter(Boolean);
      await busy(f.querySelector(".primary"), async () => {
        await api("/api/experiments", { method: "POST", body: { name: v.name.trim() || names.join(" vs "), kind: v.kind, metric: v.metric, template_ids: v.ids } });
        this.draft = null;
        closeModal();
        toast("Test started. New leads now get one version each.");
        this.render();
      });
    };
  },
  stopTest(e) {
    const r = e.results;
    const lead = r.versions.find((v) => v.letter === r.leader);
    const sure = r.verdict.state === "winner";
    const sheet = openModal(`<div class="sheet-h"><h2>Stop “${esc(e.name)}”</h2><button class="btn ghost sm" data-close aria-label="Close">${small(I.x)}</button></div>
      <form class="sheet-b">
        <div class="note ${VERDICT[r.verdict.state]}">${esc(r.verdict.text)}</div>
        <div class="ab-pick">${r.versions.map((v, i) => `<label class="ab-opt"><input type="radio" name="w" value="${v.template_id}" ${sure && v === lead ? "checked" : ""}><span class="ab-l" style="background:${AB_COL[i]}">${v.letter}</span><div style="min-width:0"><b>Keep ${esc(v.name)}</b><div class="small muted">${pct(v.rate)} · ${v.hits} of ${v.sent} · ${pct(v.chance_best)} chance best</div></div></label>`).join("")}
          <label class="ab-opt"><input type="radio" name="w" value="" ${sure ? "" : "checked"}><span></span><div><b>No winner</b><div class="small muted">First emails go back to the usual best template for each lead.</div></div></label></div>
        <label class="check"><input type="checkbox" class="chk" name="archive"> Archive the other versions</label>
        <p class="small dim" style="margin:0">The version you keep becomes the first email for ${esc(KINDS_PL[e.kind].toLowerCase())} from now on (when it fits the lead). The results stay here.</p>
        <div class="row"><span class="grow"></span><button class="btn" type="button" data-close>Cancel</button><button class="btn primary">Stop test</button></div></form>`, { narrow: true });
    sheet.onclick = (ev) => { if (ev.target.closest("[data-close]")) closeModal(); };
    sheet.onsubmit = async (ev) => {
      ev.preventDefault();
      const f = ev.target, w = f.querySelector("[name=w]:checked")?.value;
      await busy(f.querySelector(".primary"), async () => {
        await api(`/api/experiments/${e.id}/stop`, { method: "POST", body: { winner_id: w ? +w : null, archive_losers: !!w && f.archive.checked } });
        closeModal();
        toast(w ? "Stopped. The version you kept is now the first email for this segment." : "Stopped. Results are kept under Finished.");
        this.render();
      });
    };
  },
};

// --- Insights ----------------------------------------------------------------------------
export const insights = {
  async render() {
    const s = await api("/api/insights?days=14");
    const days = s.days, max = Math.max(5, ...days.map((d) => Math.max(d.emails, d.calls)));
    const top = Math.ceil(max / 5) * 5;
    const W = 680, H = 190, L = 32, R = 10, T = 24, B = 26, bw = (W - L - R) / days.length;
    const y = (v) => T + (1 - v / top) * (H - T - B);
    const chart = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Emails and calls per day over two weeks, with replies">
      ${[0, top / 2, top].map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line2)"/><text x="${L - 6}" y="${y(v) + 3}" text-anchor="end" font-size="10" fill="var(--dim)" font-family="Geist Mono, monospace">${v}</text>`).join("")}
      ${days.map((d, i) => { const x = L + i * bw, w = bw - 8; return `<rect x="${x + 4}" y="${y(d.emails)}" width="${w * 0.6}" height="${y(0) - y(d.emails)}" rx="2" fill="var(--emerald)"><title>${d.emails} emails</title></rect><rect x="${x + 4 + w * 0.6}" y="${y(d.calls)}" width="${w * 0.4}" height="${y(0) - y(d.calls)}" rx="2" fill="var(--blue)"><title>${d.calls} calls</title></rect>${d.replies ? `<circle cx="${x + bw / 2}" cy="${Math.max(y(Math.max(d.emails, d.calls)) - 10, 10)}" r="${3 + Math.min(d.replies, 6)}" fill="var(--warn)"><title>${d.replies} replies</title></circle>` : ""}`; }).join("")}
      ${days.map((d, i) => (i % 2 === 0 || i === days.length - 1 ? `<text x="${L + i * bw + bw / 2}" y="${H - 8}" text-anchor="middle" font-size="10" fill="var(--dim)" font-family="Geist Mono, monospace">${new Date(d.day + "T12:00").getDate()}</text>` : "")).join("")}
    </svg>`;
    const rate = (rows) => rows.length ? rows.map((r) => { const p = r.sent ? Math.round((r.replied / r.sent) * 100) : 0; return `<div class="frow" style="grid-template-columns:minmax(0,150px) minmax(0,1fr) 90px"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(r.label)}">${esc(r.label)}</span><div class="b"><span style="width:${p}%;background:var(--warn)"></span></div><span class="n">${p}% of ${r.sent}</span></div>`; }).join("") : `<p class="small muted" style="margin:0">Appears after your first emails.</p>`;
    const callRows = Object.entries(s.calls);
    const any = days.some((d) => d.emails || d.calls);
    C().innerHTML = `<div class="view">
      <div class="topline"><div><h1>Insights</h1><div class="sub">The last 14 days. Reply rates count each first email and whether that lead replied since.</div></div></div>
      <div class="grid2">
        <section class="card" style="grid-column:1/-1"><div class="card-h"><h2>Emails and calls per day</h2><span class="grow"></span><div class="legend"><span><i style="background:var(--emerald)"></i>emails</span><span><i style="background:var(--blue)"></i>calls</span><span><i style="background:var(--warn);border-radius:50%"></i>replies</span></div></div>
          <div class="chart">${any ? chart : `<p class="empty">Nothing sent yet. Your first emails and calls show up here.</p>`}</div></section>
        <section class="card"><div class="card-h"><h2>Reply rate by segment</h2></div><div class="funnel">${rate(s.by_kind)}</div></section>
        <section class="card"><div class="card-h"><h2>Reply rate by measured lean</h2></div><div class="funnel">${rate(s.by_lean)}</div></section>
        <section class="card"><div class="card-h"><h2>Reply rate by template</h2></div><div class="funnel">${rate(s.by_template)}</div></section>
        <section class="card"><div class="card-h"><h2>Calls</h2></div><div class="funnel">${callRows.length ? callRows.map(([k, n]) => `<div class="frow"><span>${esc(k)}</span><div class="b"><span style="width:${(n / Math.max(...callRows.map((x) => x[1]))) * 100}%;background:var(--blue)"></span></div><span class="n">${n}</span></div>`).join("") : `<p class="small muted" style="margin:0">No calls logged yet.</p>`}</div></section>
      </div></div>`;
    C().onclick = null;
  },
};

// --- Settings ----------------------------------------------------------------------------
export const settingsView = {
  async render() {
    const [s, t, ai] = await Promise.all([api("/api/settings"), api("/api/templates"), api("/api/ai")]);
    const mb = s.mailbox, fu = s.followups;
    const theme = store.get("theme", "");
    const fuTpls = t.items.filter((x) => x.step > 0 && !x.archived);
    C().innerHTML = `<div class="view" style="max-width:1000px">
      <div class="topline"><h1>Settings</h1></div>
      <div class="grid2">
        <section class="card camp"><h3>Your day</h3><form class="stack" id="set-form">
          <div class="form-grid"><label>Emails a day<input class="input" type="number" min="0" name="email" value="${s.goals.email}"></label><label>Calls a day<input class="input" type="number" min="0" name="call" value="${s.goals.call}"></label><label>Replies a day<input class="input" type="number" min="0" name="reply" value="${s.goals.reply}"></label>
          <label>Start Today with<select class="select" name="segment">${SEGS.map((k) => `<option value="${k}" ${k === s.segment ? "selected" : ""}>${KINDS_PL[k]}</option>`).join("")}</select></label></div>
          <label>Signature (the {signature} placeholder)<textarea class="input" name="signature" rows="3">${esc(s.signature)}</textarea></label>
          <div><div class="small muted" style="margin-bottom:6px">Follow-ups after a first email without reply</div>
            ${fu.map((f, i) => `<div class="inline-form" style="margin-top:4px;align-items:center"><span class="small" style="width:84px">Follow-up ${i + 1}</span><input class="input" type="number" min="1" max="60" name="fu_days_${i}" value="${f.days}" style="flex:0 0 72px;min-width:0" aria-label="Days after"><span class="small muted">days after</span><select class="select" name="fu_tpl_${i}" aria-label="Template">${fuTpls.map((x) => `<option value="${esc(x.key)}" ${x.key === f.template ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></div>`).join("")}</div>
          <div class="row"><span class="grow"></span><button class="btn primary">Save</button></div></form></section>
        <section class="card camp"><h3>Mailbox</h3>
          ${mb.configured ? `<div class="note ok">${mb.dry_run ? "Test mode: emails are written to data/outbox, not sent." : `Connected: sending as ${esc(mb.sender)}.`}</div>` : `<div class="note warn">Not connected yet. Until then, the composer copies each email for your own mail app and logs it.</div>`}
          <div class="kv"><span class="k">Sent today</span><span class="v">${mb.sent_today} of ${mb.cap}</span><span></span>
            <span class="k">Window</span><span class="v">Mon–Fri, ${mb.hours[0]}:00–${mb.hours[1]}:00, a few minutes apart</span><span></span>
            <span class="k">Replies</span><span class="v">${mb.imap ? (mb.inbox.last_error ? `<span class="flag">${esc(mb.inbox.last_error)}</span>` : `checked ${esc(when(mb.inbox.last_poll) || "every 5 minutes")}`) : "not read: add PROSPECT_IMAP_HOST"}</span>${mb.imap ? `<button class="btn sm" data-check-inbox>Check now</button>` : "<span></span>"}</div>
          <p class="small muted" style="margin:0">To connect, add these lines to <span class="mono">.env</span> in the project folder, then restart <span class="mono">prospect serve</span>. With Gmail, use an app password (Google account, Security, App passwords).</p>
          <pre class="env">PROSPECT_SMTP_HOST=smtp.gmail.com
PROSPECT_SMTP_PORT=465
PROSPECT_SMTP_USER=you@your-domain.fr
PROSPECT_SMTP_PASSWORD=your-app-password
PROSPECT_IMAP_HOST=imap.gmail.com
PROSPECT_MAIL_FROM_NAME=Yacine · Veralume
PROSPECT_DAILY_SEND_CAP=30</pre>
          <p class="small muted" style="margin:0">The inbox is only read, never changed. Add <span class="mono">PROSPECT_MAIL_DRY_RUN=true</span> to try everything without sending.</p></section>
        <section class="card camp"><h3>Writing with AI</h3>
          <p class="small muted" style="margin:0">Type what you want in the composer or the template editor, and the AI writes or rewrites the email. It only gets what the app knows about the lead, and is told not to invent anything. You always review before sending.</p>
          <div class="ab-pick">${ai.providers.map((p) => `<label class="ab-opt" style="${p.configured ? "" : "opacity:.65;cursor:default"}"><input type="radio" name="ai-provider" value="${p.key}" ${p.key === ai.current ? "checked" : ""} ${p.configured ? "" : "disabled"}>
            <div><b>${esc(p.label)}</b> <span class="tag">${esc(p.cost)}</span><div class="small muted">${p.configured ? `Ready · ${esc(p.model)}` : `Add <span class="mono">${esc(p.env)}=…</span> to .env`}</div></div></label>`).join("")}</div>
          <details class="ab-read"><summary class="small">How to set one up</summary><ul class="small muted" style="padding-left:18px;display:grid;gap:6px">
            <li><b>Gemini</b> (free): get a key at aistudio.google.com, then <span class="mono">PROSPECT_GEMINI_API_KEY=…</span>. Lead details go to Google.</li>
            <li><b>Mistral</b> (free tier): key at console.mistral.ai, then <span class="mono">PROSPECT_MISTRAL_API_KEY=…</span>. Lead details go to Mistral (France).</li>
            <li><b>Ollama</b> (free, stays on this computer): install it, run <span class="mono">ollama pull qwen2.5:3b</span>, then <span class="mono">PROSPECT_OLLAMA_URL=http://localhost:11434</span>. Slower on this machine.</li>
            <li><b>Claude</b> (paid): key at console.anthropic.com, then <span class="mono">PROSPECT_ANTHROPIC_API_KEY=…</span>. Uses ${esc(ai.providers.find((p) => p.key === "claude").model)}; set <span class="mono">PROSPECT_AI_MODEL_CLAUDE=claude-haiku-5-5</span> to pay less.</li>
            <li>Restart <span class="mono">prospect serve</span> after editing .env.</li></ul></details>
          <form class="stack" id="ai-form"><label>What you offer (the AI builds every email on this)<textarea class="input" name="brief" rows="5" lang="fr">${esc(ai.brief)}</textarea></label>
            <div class="row"><span class="grow"></span><button class="btn primary">Save</button></div></form></section>
        <section class="card camp"><h3>Look</h3><div class="seg" role="group" aria-label="Theme">${[["", "System"], ["light", "Light"], ["dark", "Dark"]].map(([v, l]) => `<button data-theme-set="${v}" aria-pressed="${theme === v}">${l}</button>`).join("")}</div></section>
      </div></div>`;
    $("#set-form").onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target;
      const body = {
        goals: { email: +f.email.value, call: +f.call.value, reply: +f.reply.value },
        segment: f.segment.value, signature: f.signature.value,
        followups: fu.map((_, i) => ({ days: +f[`fu_days_${i}`].value, template: f[`fu_tpl_${i}`].value })),
      };
      await busy(f.querySelector(".primary"), async () => { await api("/api/settings", { method: "PUT", body }); store.set("segment", body.segment); toast("Saved"); });
    };
    $("#ai-form").onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target, provider = C().querySelector("[name=ai-provider]:checked")?.value;
      await busy(f.querySelector(".primary"), async () => { await api("/api/ai", { method: "PUT", body: { brief: f.brief.value, ...(provider ? { provider } : {}) } }); toast("Saved"); });
    };
    C().onchange = async (e) => {
      if (e.target.name !== "ai-provider") return;
      try { await api("/api/ai", { method: "PUT", body: { provider: e.target.value } }); toast("AI switched"); } catch (err) { fail(err); }
    };
    C().onclick = async (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.themeSet !== undefined) { store.set("theme", b.dataset.themeSet); applyTheme(); return this.render(); }
      if (b.dataset.checkInbox !== undefined) return busy(b, async () => { const r = await api("/api/inbox/check", { method: "POST" }); const n = Object.values(r.found || {}).reduce((a, x) => a + x, 0); toast(n ? `${n} new: ${Object.entries(r.found).map(([k, v]) => `${v} ${k}`).join(", ")}` : "Nothing new"); changed(); });
    };
  },
};

export function applyTheme() {
  const t = store.get("theme", "");
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
}
