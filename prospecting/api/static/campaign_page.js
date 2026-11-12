// One started campaign, in four tabs: Overview (the journey, what's coming, replies),
// Leads (filter, act on one or many), Emails & automations (the sequence, its texts and
// delays, a live preview) and Settings (everything else). Drafts keep the builder.
import { $, api, busy, changed, confetti, COUNTRY, esc, fail, flag, I, KINDS_PL, LANG, small, STATUS as LEAD_STATUS, toast, when } from "./core.js";
import { openLead } from "./lead.js";
import {
  activeFilters, addLeads, closeDays, coveredLangs, editClose, editFollowup, editTranslations, FU, fuDays, fuMissing, fuOf, fuOn, fuText,
  langList, LET, pct, pickWinner, plural, sendFrom, setFuText, STATE, statusPill, TO_SEND, VCOL, vLangs, written,
} from "./campaigns.js";

const C = () => $("#canvas");
const P = { id: null, tab: "overview", stage: "all", q: "", country: "", lang: "", version: "", sort: "next", sel: new Set(), open: null, step: 1, fuLang: {}, fuDraft: {}, pvLead: null, pvTimer: null, qTimer: null, confirmDel: false };
const TABS = [["overview", "Overview"], ["leads", "Leads"], ["emails", "Emails & automations"], ["settings", "Settings"]];
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const STAGES = [
  ["all", "All", () => true],
  ["to_send", "To send", (l) => TO_SEND.includes(l.state)],
  ["waiting", "Waiting", (l) => ["waiting", "followed_up", "followed_up_2"].includes(l.state)],
  ["bounced", "Bounced", (l) => ["bounced", "bounced_retry"].includes(l.state)],
  ["replied", "Replied", (l) => ["replied", "interested"].includes(l.state)],
  ["no_reply", "No reply", (l) => l.state === "no_reply"],
  ["stopped", "Asked to stop", (l) => l.state === "stopped"],
  ["skipped", "Follow-ups skipped", (l) => l.skip],
];
const GROUP = { replied: "ok", interested: "ok", bounced: "bad", bounced_retry: "bad", stopped: "bad", failed: "bad", no_reply: "dim", skipped: "dim", waiting: "info", followed_up: "info", followed_up_2: "info", to_review: "todo", approved: "todo", not_sent: "" };
const shortDay = (iso) => new Date(iso.length === 10 ? `${iso}T12:00` : iso).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
const stepName = (k) => ({ 0: "First email", 1: "Follow-up 1", 2: "Follow-up 2" })[k] ?? "Email";

export function renderCampaign(c) {
  if (P.id !== c.id) Object.assign(P, { id: c.id, tab: "overview", stage: "all", q: "", country: "", lang: "", version: "", sel: new Set(), open: null, step: 1, fuLang: {}, fuDraft: {}, pvLead: null, confirmDel: false });
  P.c = c;
  const r = c.results, t = r.totals;
  const toSend = r.leads.filter((l) => TO_SEND.includes(l.state)).map((l) => l.id);
  const live = c.status === "running" || c.status === "paused";
  C().innerHTML = `<div class="view wide cx">
    <header class="cx-head">
      <div class="cx-title"><p class="small muted" style="margin:0"><a href="#campaigns">Campaigns</a> / ${esc(KINDS_PL[c.kind] || c.kind)}</p>
        <div class="row" style="gap:12px;flex-wrap:wrap"><h1>${esc(c.name)}</h1>${statusPill(c.status)}</div>
        <p class="muted" style="margin:0">${meta(c)}</p></div>
      <div class="row cx-actions">
        ${toSend.length && c.status !== "archived" ? `<button class="btn" data-send-next>${small(I.send, 15)} Send the next first email <span class="small" style="opacity:.75">(${toSend.length} left)</span></button>` : ""}
        ${c.status === "running" ? `<button class="btn" data-status="paused">Pause</button>` : c.status === "paused" ? `<button class="btn primary" data-status="running">Resume</button>` : ""}
        ${live ? `<button class="btn primary" data-add>${small(I.plus, 15)} Add leads</button>` : ""}
      </div>
    </header>
    <nav class="cx-tabs" role="tablist" aria-label="Campaign">${TABS.map(([k, l]) => `<button role="tab" data-tab="${k}" aria-selected="${P.tab === k}">${l}${k === "leads" ? ` <span class="n">${t.leads}</span>` : k === "emails" ? ` <span class="n">${1 + [1, 2].filter((x) => fuOn(c, x)).length}</span>` : ""}</button>`).join("")}</nav>
    ${banners(c)}
    <div class="cx-body">${{ overview, leads: leadsTab, emails: emailsTab, settings: settingsTab }[P.tab](c)}</div>
  </div>`;
  wire(c, toSend);
  if (P.tab === "emails") loadPreview(c);
}

function meta(c) {
  const t = c.results.totals, days = c.send_days || [], hrs = c.send_hours || [9, 18];
  const dayTxt = days.join() === "0,1,2,3,4" ? "Mon–Fri" : days.map((d) => DAYS[d]).join(", ");
  const steps = 1 + [1, 2].filter((k) => fuOn(c, k)).length;
  return [c.started_at ? `Started ${esc(shortDay(c.started_at))}` : "", plural(t.leads, "lead"), steps > 1 ? `1 email + ${plural(steps - 1, "follow-up")}` : "1 email",
    `up to ${c.daily_cap} a day, ${dayTxt} ${hrs[0]}:00–${hrs[1]}:00`].filter(Boolean).join(" · ");
}

function banners(c) {
  const t = c.results.totals, out = [];
  if (c.status === "archived") out.push(`<div class="cx-banner"><span>Archived: nothing goes out. Its results stay here.</span><button class="btn sm" data-status="unarchive">Unarchive</button></div>`);
  if (c.paused_reason) out.push(`<div class="cx-banner bad"><span>${esc(c.paused_reason)}</span></div>`);
  else if (c.status === "paused") out.push(`<div class="cx-banner warn"><span>Paused: nothing goes out, follow-ups included, until you resume.</span></div>`);
  if (t.bounced) out.push(`<div class="cx-banner bad"><span><b>${plural(t.bounced, "address", "addresses")} bounced.</b> ${t.bounced === 1 ? "This lead gets" : "These leads get"} nothing more until you find their real address (on their site, Instagram…).</span><button class="btn sm" data-goto="bounced">See ${t.bounced === 1 ? "it" : `the ${t.bounced}`}</button></div>`);
  if (t.to_review) out.push(`<div class="cx-banner"><span>${plural(t.to_review, "first email")} written, waiting for you to read and send.</span><button class="btn sm" data-goto="to_send">See them</button></div>`);
  if (c.start_on && c.start_on > new Date().toISOString().slice(0, 10)) out.push(`<div class="cx-banner"><span>Starts on ${esc(shortDay(c.start_on))}.</span></div>`);
  return out.join("");
}

// --- Overview --------------------------------------------------------------------------------

function overview(c) {
  const r = c.results, t = r.totals, f1 = c.followup_status || {}, f2 = c.followup2_status || {}, cl = c.close_status || {};
  const out = t.sent + t.bounced, unsent = t.not_sent + t.to_review + t.approved;
  const planned = (f) => (f.later || 0) + (f.waiting || 0);
  const node = (cls, label, big, lines, bar = "") => `<div class="cx-node ${cls}"><span class="small cx-nl">${label}</span>${big === "" ? "" : `<b class="cx-big">${big}</b>`}${lines}${bar}</div>`;
  const arrow = (days, attr, on) => `<div class="cx-link"><button class="cx-delay" ${attr} title="Change it">${on ? `+${days} days` : "off"}</button><svg width="44" height="12" viewBox="0 0 44 12" aria-hidden="true"><path d="M0 6h40M35 1l5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.5"/></svg><span class="small muted">if silent</span></div>`;
  const fuNode = (k, f) => {
    if (!fuOn(c, k)) return node("off", stepName(k), "–", `<span class="small muted">switched off</span>`);
    const p = planned(f), isNext = p > 0 && (k === 1 || !planned(f1));
    const col = k === 1 ? "blue" : "violet";
    return node(isNext ? "next" : "", `${stepName(k)}${k === 2 ? " · last" : ""}${isNext ? " · next" : ""}`, f.sent || p || "–",
      `<span class="small">${f.sent ? `sent${p ? ` · ${p} planned` : ""}` : p ? `planned${f.next_at ? `, from <b>${esc(shortDay(f.next_at))}</b>` : ""}` : k === 2 && planned(f1) ? "to those still silent" : "nobody yet"}</span>`,
      `<div class="cx-seg"><span style="flex:${f.sent || 0};background:var(--${col})"></span><span style="flex:${p};background:var(--${col});opacity:.3"></span><span style="flex:${Math.max(0, t.sent - (f.sent || 0) - p)}"></span></div>`);
  };
  const journey = `<section class="card cx-card"><div class="row"><h2 class="cx-h2">The journey</h2><span class="small muted">where the ${plural(t.leads, "lead")} are now · click a delay to change it</span></div>
    <div class="cx-journey">
      ${node("", "First email", out, `<span class="small">${unsent ? `sent · ${unsent} still to send` : "sent to everyone"}</span>`,
        `<div class="cx-seg"><span style="flex:${t.sent};background:var(--emerald)"></span><span style="flex:${t.bounced};background:var(--bad)"></span><span style="flex:${unsent}"></span></div><span class="small muted">${t.sent} delivered${t.bounced ? ` · <span class="bad-t">${t.bounced} bounced</span>` : ""}</span>`)}
      ${arrow(fuDays(c, 1), 'data-edit-fu="1"', fuOn(c, 1))}${fuNode(1, f1)}
      ${arrow(fuDays(c, 2), 'data-edit-fu="2"', fuOn(c, 2))}${fuNode(2, f2)}
      ${arrow(closeDays(c), "data-edit-close", c.close_on !== false)}
      ${node(c.close_on === false ? "off" : "", "Marked “No reply”", cl.done || "–", `<span class="small">${c.close_on === false ? "switched off" : cl.later ? `next${cl.next_at ? ` ${esc(shortDay(cl.next_at))}` : ""}` : cl.done ? "done" : "nobody yet"}</span><span class="small muted">can be tried again later</span>`)}
      <div class="cx-gap"></div>
      ${node("won", "Answered, at any step", "", `<div class="row" style="gap:18px"><div><b class="cx-big">${t.replied}</b><div class="small">replied</div></div><div><b class="cx-big">${t.interested}</b><div class="small">interested</div></div><div><b class="cx-big">${t.pilots}</b><div class="small">free tests</div></div></div><span class="small muted">a reply stops their follow-ups${t.stopped ? ` · ${t.stopped} asked to stop` : ""}</span>`)}
    </div></section>`;
  const bounceRate = out ? t.bounced / out : 0;
  const kpis = `<div class="cx-kpis">
    <div class="cx-kpi"><span class="small muted">First emails sent</span><b>${out}</b><span class="small muted">of ${t.leads}${unsent ? ` · ${unsent} to go` : ""}</span></div>
    <div class="cx-kpi"><span class="small muted">Reply rate</span><b>${t.sent ? pct(t.replied / t.sent) : "–"}</b><span class="small muted">${t.replied} of ${t.sent} delivered</span></div>
    <div class="cx-kpi"><span class="small muted">Bounce rate</span><b class="${bounceRate > 0.02 ? "bad-t" : ""}">${out ? pct(bounceRate) : "–"}</b><span class="small muted">${t.bounced} of ${out} · keep it under 2 %</span></div>
    <div class="cx-kpi"><span class="small muted">Asked to stop</span><b>${t.stopped}</b><span class="small muted">kept for good</span></div>
    <div class="cx-kpi"><span class="small muted">Follow-ups planned</span><b>${planned(f1) + planned(f2)}</b><span class="small muted">${planned(f1) + planned(f2) ? "if nobody replies" : "none waiting"}</span></div>
  </div>`;
  const fc = c.forecast || [], top = Math.max(1, c.daily_cap, ...fc.map((d) => d.fu1 + d.fu2));
  const coming = `<section class="card cx-card cx-grow2"><div class="row"><h2 class="cx-h2">Coming up</h2><span class="small muted">what goes out on its own, sending day by sending day · up to ${c.daily_cap} a day</span></div>
    ${fc.some((d) => d.fu1 + d.fu2) ? `<div class="cx-bars">${fc.map((d) => `<div class="cx-day" title="${esc(shortDay(d.day))}: ${d.fu1} follow-up 1, ${d.fu2} follow-up 2"><span class="small mono">${d.fu1 + d.fu2 || ""}</span>
        <span class="cx-stack" style="height:${Math.max(2, ((d.fu1 + d.fu2) / top) * 110)}px"><i style="flex:${d.fu2};background:var(--violet)"></i><i style="flex:${d.fu1};background:var(--blue)"></i></span>
        <span class="small muted">${esc(new Date(`${d.day}T12:00`).toLocaleDateString("en-GB", { weekday: "short" }).slice(0, 2))} ${+d.day.slice(8)}</span></div>`).join("")}</div>
      <p class="small muted" style="margin:0"><span class="sw" style="background:var(--blue)"></span>Follow-up 1 &nbsp;<span class="sw" style="background:var(--violet)"></span>Follow-up 2 &nbsp;· if nobody replies. A reply takes that lead's next emails off.</p>`
      : `<p class="small muted">Nothing goes out on its own in the next ${fc.length || 12} sending days.${c.status === "paused" ? " The campaign is paused." : ""}</p>`}</section>`;
  const replies = r.leads.filter((l) => l.reply).sort((a, b) => (b.reply_at || "").localeCompare(a.reply_at || "")).slice(0, 6);
  const feed = `<section class="card cx-card cx-grow1"><div class="row"><h2 class="cx-h2">Replies</h2><span class="pill">${t.replied + t.stopped}</span></div>
    ${replies.length ? `<div class="cx-feed">${replies.map((l) => `<button class="cx-reply" data-open="${l.id}"><span class="row" style="gap:8px"><b>${esc(l.person || l.name)}</b><span class="cx-st ${GROUP[l.state] || ""}">${esc(STATE[l.state]?.[0] || l.state)}</span><span class="grow"></span><span class="small muted">${esc(when(l.reply_at))}</span></span><span class="small muted cx-q">“${esc(l.reply)}”</span></button>`).join("")}</div>`
      : `<div class="cx-empty">${small(I.mail, 28)}<b>No replies yet</b><span class="small muted">Replies land here as they arrive; the inbox is read every 5 minutes. Open one to answer it or start their free test.</span></div>`}</section>`;
  return `${journey}${kpis}<div class="cx-cols">${coming}${feed}</div>${abTest(c)}<div class="cx-cols">${emailCard(c)}${reach(c)}</div>`;
}

function abTest(c) {
  const r = c.results, t = r.totals, test = r.test;
  if (r.versions.length < 2) return "";
  const live = c.status === "running" || c.status === "paused";
  const v = (x, i) => {
    const tv = test?.versions.find((y) => y.template_id === x.template_id), won = test?.winner_id === x.template_id, lead = test && test.leader === x.letter && t.sent;
    return `<div class="cx-v ${won || lead ? "lead" : ""}" style="--vc:${VCOL[i]}"><div class="row"><span class="vl" style="background:${VCOL[i]}">${x.letter}</span><b style="flex:1;min-width:0" class="cx-ell">“${esc(x.subject)}”</b>${won ? `<span class="pill st-customer">Winner</span>` : lead ? `<span class="pill st-pilot">Ahead</span>` : ""}</div>
      <div class="cx-mini"><div><b>${x.sent}</b><span>sent</span></div><div><b>${x.replied}</b><span>replied</span></div><div><b style="color:var(--vc)">${x.sent ? pct((test?.metric === "interested" ? x.interested : x.replied) / x.sent) : "–"}</b><span>rate</span></div>${tv?.sent ? `<div><b>${pct(tv.chance_best)}</b><span>chance best</span></div>` : ""}</div></div>`;
  };
  return `<section class="card cx-card"><div class="row"><h2 class="cx-h2">A/B test</h2><span class="small muted">counted on ${test?.metric === "interested" ? "interested leads" : "replies"}</span></div>
    ${test?.suggest ? `<div class="cx-banner ok"><span><b>Version ${esc(test.suggest.letter)} is clearly better.</b> ${esc(test.verdict.text)}</span><button class="btn primary sm" data-winner="${test.suggest.template_id}" data-letter="${esc(test.suggest.letter)}" data-unsent="${test.suggest.unsent}">Send ${esc(test.suggest.letter)} to the ${test.suggest.unsent} left</button></div>`
      : `<p class="small muted" style="margin:0">${esc(test?.verdict.text || "")} ${live && t.sent >= 10 && test?.status !== "stopped" ? `<button class="btn sm" data-pick>Pick a winner myself</button>` : ""}</p>`}
    <div class="cx-vs">${r.versions.map(v).join("")}</div></section>`;
}

function emailCard(c) {
  const r = c.results;
  return `<section class="card cx-card cx-grow1"><div class="row"><h2 class="cx-h2">${r.versions.length > 1 ? "The emails" : "The email"}</h2><span class="grow"></span><button class="btn ghost sm" data-tab="emails">See the sequence</button></div>
    ${r.versions.map((v) => { const cv = c.versions.find((x) => x.id === v.template_id); const ls = cv ? vLangs(cv).filter((l) => written(cv, l)) : [];
      return `<div class="cx-email">${v.asset_ids.length ? `<div class="cx-thumbs">${v.asset_ids.slice(0, 3).map((id) => `<img src="/api/assets/${id}/thumb" alt="">`).join("")}</div>` : ""}
        <div style="min-width:0;flex:1"><b class="cx-ell">“${esc(v.subject)}”</b><div class="small muted">${v.asset_ids.length ? `${plural(v.asset_ids.length, "image")} · ` : ""}in ${esc(ls.length > 3 ? `${langList(ls.slice(0, 3))} +${ls.length - 3}` : langList(ls))}</div>
        <div class="row small" style="gap:14px;margin-top:4px"><span><b class="mono">${v.sent}</b> sent</span><span><b class="mono">${v.replied}</b> replied</span><span><b class="mono">${v.sent ? pct(v.replied / v.sent) : "–"}</b> reply rate</span></div></div></div>`; }).join("")}
    ${r.versions.length === 1 ? `<p class="small muted" style="margin:0">To compare another subject or other images, duplicate the campaign and add a version B.</p>` : ""}</section>`;
}

function reach(c) {
  const by = {};
  for (const l of c.results.leads) {
    const k = l.country || "FR";
    by[k] = by[k] || { n: 0, replied: 0 };
    by[k].n++;
    if (["replied", "interested"].includes(l.state)) by[k].replied++;
  }
  const rows = Object.entries(by).sort((a, b) => b[1].n - a[1].n), max = Math.max(1, ...rows.map(([, x]) => x.n));
  const langs = Object.entries(c.results.languages || {});
  return `<section class="card cx-card cx-grow1"><div class="row"><h2 class="cx-h2">Who it reaches</h2><span class="grow"></span><button class="btn ghost sm" data-tab="leads">See all ${c.results.totals.leads}</button></div>
    ${rows.map(([k, x]) => `<div class="cx-rrow"><span class="cx-rl">${flag(k)} ${esc(COUNTRY[k] || k)}</span><span class="cx-seg" style="flex:1"><span style="flex:${x.n};background:var(--emerald)"></span><span style="flex:${max - x.n}"></span></span><span class="small mono cx-rn">${x.n} · ${x.replied} repl.</span></div>`).join("")}
    ${langs.length > 1 ? `<p class="small muted" style="margin:6px 0 0">In ${langs.map(([l, x]) => `${esc(LANG[l] || l)} ${x.leads}`).join(" · ")}</p>` : ""}</section>`;
}

// --- Leads -------------------------------------------------------------------------------------

function shown(c) {
  const st = STAGES.find(([k]) => k === P.stage) || STAGES[0], q = P.q.trim().toLowerCase();
  const rows = c.results.leads.filter((l) => st[2](l) && (!P.country || (l.country || "FR") === P.country) && (!P.lang || l.language === P.lang)
    && (!P.version || l.letter === P.version) && (!q || [l.name, l.person, l.city, l.email].some((x) => (x || "").toLowerCase().includes(q))));
  const key = { next: (l) => l.next?.at || (TO_SEND.includes(l.state) ? "0" : "9"), last: (l) => `${l.at || ""}`, name: (l) => (l.person || l.name || "").toLowerCase() }[P.sort];
  rows.sort((a, b) => (P.sort === "last" ? key(b).localeCompare(key(a)) : key(a).localeCompare(key(b))));
  return rows;
}

function nextTxt(c, l) {
  if (TO_SEND.includes(l.state) && c.status !== "archived") return `<button class="btn sm primary" data-send-lead="${l.id}">${small(I.send, 13)} Send</button>`;
  if (l.state === "bounced") return `<span class="small bad-t">Find their real address</span>`;
  if (l.skip) return `<span class="small muted">Follow-ups skipped</span>`;
  if (l.next) return `<span class="small">${stepName(l.next.step)} · ${esc(shortDay(l.next.at))}</span>`;
  return `<span class="small muted">–</span>`;
}

function timeline(l) {
  const line = (m) => `<div class="small"><b>${esc(m.at ? shortDay(m.at) : "")}</b> · ${stepName(m.step)} ${m.status === "sent" ? `sent to ${esc(m.to || "")}`
    : m.error === "bounced" ? `<span class="bad-t">bounced: ${esc(m.to || "")} doesn't exist</span>` : m.status === "approved" ? "written, goes out at the next free slot"
    : m.status === "queued" ? "written, waiting for you to send it" : esc(m.status)}</div>`;
  return `<div class="cx-tl">${l.emails.map(line).join("")}
    ${l.reply ? `<div class="small"><b>${esc(shortDay(l.reply_at))}</b> · <span class="ok-t">Replied:</span> “${esc(l.reply)}”</div>` : ""}
    ${l.next && !l.skip ? `<div class="small info-t"><b>${esc(shortDay(l.next.at))}</b> · ${stepName(l.next.step)} planned, if still silent</div>` : ""}
    ${!l.emails.length ? `<div class="small muted">Not emailed yet.</div>` : ""}</div>`;
}

function leadsTab(c) {
  const all = c.results.leads, rows = shown(c);
  const opts = (vals, cur, label, name = (v) => v) => `<option value="">${label}</option>${[...new Set(vals.filter(Boolean))].sort().map((v) => `<option value="${esc(v)}" ${v === cur ? "selected" : ""}>${esc(name(v))}</option>`).join("")}`;
  const countries = [...new Set(all.map((l) => l.country || "FR"))], langs = [...new Set(all.map((l) => l.language).filter(Boolean))];
  const allSel = rows.length > 0 && rows.every((l) => P.sel.has(l.id));
  return `<div class="cx-chips" role="group" aria-label="Where they are">${STAGES.map(([k, label, f]) => { const n = all.filter(f).length; return k !== "all" && !n && P.stage !== k ? "" : `<button class="cx-chip" data-stage="${k}" aria-pressed="${P.stage === k}">${label} <span class="n">${n}</span></button>`; }).join("")}</div>
    <div class="cx-tools">
      <label class="cx-search"><span class="sr">Search leads</span><input class="input" data-q value="${esc(P.q)}" placeholder="Search a name, studio, town, email…"></label>
      ${countries.length > 1 ? `<select class="input sm" data-f="country" aria-label="Country">${opts(countries, P.country, "All countries", (v) => COUNTRY[v] || v)}</select>` : ""}
      ${langs.length > 1 ? `<select class="input sm" data-f="lang" aria-label="Language">${opts(langs, P.lang, "All languages", (v) => LANG[v] || v)}</select>` : ""}
      ${c.results.versions.length > 1 ? `<select class="input sm" data-f="version" aria-label="Version">${opts(LET.slice(0, c.results.versions.length), P.version, "All versions")}</select>` : ""}
      <select class="input sm" data-f="sort" aria-label="Sort"><option value="next" ${P.sort === "next" ? "selected" : ""}>Next step first</option><option value="last" ${P.sort === "last" ? "selected" : ""}>Latest email first</option><option value="name" ${P.sort === "name" ? "selected" : ""}>Name</option></select>
      <span class="grow"></span><a class="btn sm" href="/api/campaigns/${c.id}/export.csv" download>Export CSV</a>
    </div>
    <section class="card cx-table"><div class="cx-scroll"><div class="cx-tr cx-th"><span><input type="checkbox" class="chk" data-all ${allSel ? "checked" : ""} aria-label="Select all shown"></span><span>Lead</span><span>Town</span><span>Lang.</span><span>Version</span><span>Where they are</span><span>Last email</span><span>Next step</span></div>
      ${rows.slice(0, 400).map((l) => `<div class="cx-tr ${P.sel.has(l.id) ? "sel" : ""} ${P.open === l.id ? "open" : ""}" data-row="${l.id}">
          <span><input type="checkbox" class="chk" data-sel="${l.id}" ${P.sel.has(l.id) ? "checked" : ""} aria-label="Select ${esc(l.person || l.name)}"></span>
          <span class="cx-who"><b class="cx-ell">${esc(l.person || l.name)}</b><span class="small muted cx-ell">${esc([l.person ? l.name : "", l.email].filter(Boolean).join(" · "))}</span></span>
          <span class="small cx-ell">${flag(l.country)} ${esc(l.city || "")}</span><span class="small mono">${esc((l.language || "").toUpperCase())}</span>
          <span><span class="vl sm" style="background:${VCOL[LET.indexOf(l.letter)] || "var(--dim)"}">${esc(l.letter)}</span></span>
          <span><span class="cx-st ${GROUP[l.state] || ""}">${esc(STATE[l.state]?.[0] || l.state)}</span></span>
          <span class="small muted">${esc(l.at ? when(l.at) : "")}</span><span>${nextTxt(c, l)}</span></div>
        ${P.open === l.id ? `<div class="cx-more">${timeline(l)}<div class="row cx-more-a">
          <button class="btn sm" data-open="${l.id}">Open the lead</button>
          ${l.next && !l.skip ? `<button class="btn sm" data-one="followup_now" data-id="${l.id}">Send ${stepName(l.next.step).toLowerCase()} now</button>` : ""}
          ${l.skip ? `<button class="btn sm" data-one="resume" data-id="${l.id}">Resume follow-ups</button>` : l.next ? `<button class="btn sm" data-one="skip" data-id="${l.id}">Skip their follow-ups</button>` : ""}
          <button class="btn sm ghost danger" data-one="remove" data-id="${l.id}">Remove from campaign</button></div></div>` : ""}`).join("")}
      ${rows.length ? "" : `<p class="small muted" style="padding:16px 18px;margin:0">Nobody here.</p>`}</div>
      <div class="cx-foot small muted"><span>${rows.length > 400 ? `Showing 400 of ${rows.length}` : plural(rows.length, "lead")}</span><span>Click a row for its timeline and actions</span></div></section>
    ${P.sel.size ? `<div class="cx-bulk" role="region" aria-label="Act on the selected leads"><b>${P.sel.size} selected</b><button class="btn ghost sm" data-clear>Clear</button><span class="grow"></span>
      <button class="btn sm" data-bulk="followup_now">Send follow-up now</button><button class="btn sm" data-bulk="skip">Skip follow-ups</button><button class="btn sm" data-bulk="resume">Resume follow-ups</button>
      <select class="input sm" data-bulk-status aria-label="Set their status"><option value="">Set status…</option>${Object.entries(LEAD_STATUS).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select>
      ${c.others?.length ? `<select class="input sm" data-bulk-move aria-label="Move to another campaign"><option value="">Move to…</option>${c.others.map((x) => `<option value="${x.id}">${esc(x.name)}${x.status === "draft" ? " (draft)" : ""}</option>`).join("")}</select>` : ""}
      <button class="btn sm danger" data-bulk="remove">Remove</button></div>` : ""}`;
}

// --- Emails & automations ---------------------------------------------------------------------

function emailsTab(c) {
  const f1 = c.followup_status || {}, f2 = c.followup2_status || {}, cl = c.close_status || {}, t = c.results.totals;
  const tog = (attr, on, label, dis = false) => `<label class="cx-switch" title="${label}"><input type="checkbox" ${attr} ${on ? "checked" : ""} ${dis ? "disabled" : ""}><span aria-hidden="true"></span><span class="sr">${label}</span></label>`;
  const wait = (attr, val, label, max) => `<div class="cx-wait"><span class="small muted">${label}</span><input class="input sm mono" type="number" min="1" max="${max}" ${attr} value="${val}" aria-label="${label} (days)"><span class="small muted">days</span></div>`;
  const step = (k, num, title, sub, stats) => `<button class="cx-step ${P.step === k ? "on" : ""}" data-step="${k}"><span class="row" style="gap:10px"><span class="cx-num s${k}">${num}</span><b style="flex:1">${title}</b></span><span class="small muted cx-ell">${sub}</span><span class="row small" style="gap:12px">${stats}</span></button>`;
  const v0 = c.versions[0] || { subject: "" };
  const seq = `<aside class="cx-seq"><p class="small muted" style="margin:0 0 10px">The emails each lead gets, in order. Click one to read or change it. Delays and switches apply any time, even now.</p>
    ${step(0, 1, c.versions.length > 1 ? `First email · ${c.versions.length} versions` : "First email", `“${esc(v0.subject)}”`, `<span><b class="mono">${t.sent + t.bounced}</b> sent</span><span><b class="mono">${t.replied}</b> replied</span>${t.bounced ? `<span class="bad-t"><b class="mono">${t.bounced}</b> bounced</span>` : ""}`)}
    ${wait('data-days="1"', fuDays(c, 1), "if no reply after", 30)}
    <div class="cx-steprow">${step(1, 2, "Follow-up 1", "“Re: …” in the same conversation · no images", fuOn(c, 1) ? `<span><b class="mono">${f1.sent || 0}</b> sent</span><span><b class="mono">${(f1.later || 0) + (f1.waiting || 0)}</b> planned</span>` : `<span class="muted">off</span>`)}${tog('data-on="1"', c.followups_on !== false, "Follow-up 1 on")}</div>
    ${wait('data-days="2"', fuDays(c, 2), "if still no reply after", 30)}
    <div class="cx-steprow">${step(2, 3, "Follow-up 2 · last", "A short last email", fuOn(c, 2) ? `<span><b class="mono">${f2.sent || 0}</b> sent</span><span><b class="mono">${(f2.later || 0) + (f2.waiting || 0)}</b> planned</span>` : `<span class="muted">off</span>`)}${tog('data-on="2"', c.followup2_on !== false, "Follow-up 2 on", c.followups_on === false)}</div>
    ${wait("data-close-days", closeDays(c), "if still no reply after", 90)}
    <div class="cx-steprow">${step(3, "✓", "Mark as “No reply”", "They leave the sequence; a later campaign can try again", c.close_on !== false ? `<span><b class="mono">${cl.done || 0}</b> marked</span><span><b class="mono">${cl.later || 0}</b> to come</span>` : `<span class="muted">off</span>`)}${tog("data-close-on", c.close_on !== false, "No reply on")}</div>
    <p class="small muted" style="margin:14px 0 0">Always: a reply stops everything for that lead; “stop” in any of 13 languages marks them Do not contact; a bounce flags the address.</p></aside>`;
  return `<div class="cx-split">${seq}<section class="cx-pane">${pane(c)}</section></div>`;
}

function pane(c) {
  if (P.step === 3) return `<div class="card cx-card"><h2 class="cx-h2">Mark as “No reply”</h2>
    <p style="margin:0">Leads who answered none of the emails get the status <b>No reply</b> ${plural(closeDays(c), "day")} after the last one. It isn't “Lost” (they never said no) nor Do not contact: a later campaign can try them again, and a late reply moves them to Replied.</p>
    <p class="small muted" style="margin:0">${(c.close_status || {}).done || 0} marked so far${c.close_status?.next_at ? ` · next ones from ${esc(shortDay(c.close_status.next_at))}` : ""}.</p></div>`;
  const pv = `<div class="cx-pv"><div class="row"><b style="flex:1">Preview</b><label class="small muted">as <select class="input sm" data-pv-lead aria-label="Preview for">${c.results.leads.slice(0, 200).map((l) => `<option value="${l.id}" ${P.pvLead === l.id ? "selected" : ""}>${esc(l.person || l.name)}${l.language ? ` · ${esc(l.language.toUpperCase())}` : ""}</option>`).join("")}</select></label><button class="btn sm" data-test>Send me a test</button></div>
    <div class="cx-mail"><div class="cx-mail-h small muted" data-pv-h>…</div><div class="cx-mail-b" data-pv-b></div><div class="cx-thumbs" data-pv-img></div></div></div>`;
  if (P.step === 0) {
    return `${c.versions.map((v, i) => `<div class="card cx-card"><div class="row"><h2 class="cx-h2" style="flex:1">First email${c.versions.length > 1 ? ` · version ${esc(LET[i])}` : ""}</h2><button class="btn sm" data-tr-edit="${v.id}">Languages (${vLangs(v).filter((l) => written(v, l)).length})</button></div>
        <p class="small muted" style="margin:0">Fixed once the campaign has started, so everyone gets the same email. Languages can still be added. To try another text, duplicate the campaign.</p>
        <div class="cx-ro"><b>${esc(v.subject)}</b>\n\n${esc(v.body)}</div>
        ${v.asset_ids.length ? `<div class="cx-thumbs">${v.asset_ids.map((id) => `<img src="/api/assets/${id}/thumb" alt="">`).join("")}</div>` : ""}</div>`).join("")}${pv}`;
  }
  const k = P.step, x = { versions: c.versions, [FU[k].text]: P.fuDraft[k] || structuredClone(fuOf(c, k)), [FU[k].def]: c[FU[k].def] };
  const langs = coveredLangs(c.versions), l = langs.includes(P.fuLang[k]) ? P.fuLang[k] : langs[0], fu = x[FU[k].text], miss = fuMissing(x, k);
  const dirty = !!P.fuDraft[k];
  return `<div class="card cx-card"><div class="row"><h2 class="cx-h2" style="flex:1">${stepName(k)} · ${plural(fuDays(c, k), "day")} after ${FU[k].after}</h2><span class="small ${dirty ? "warn-t" : "muted"}">${dirty ? "Not saved yet" : "Saved"}</span></div>
      ${fuOn(c, k) ? "" : `<div class="cx-banner warn"><span>Switched off: nobody gets it. Switch it on in the sequence.</span></div>`}
      ${langs.length > 1 ? `<div class="cx-langs" role="tablist" aria-label="Language">${langs.map((y) => `<button role="tab" class="cx-lt" data-fulang="${y}" aria-selected="${y === l}">${esc(y.toUpperCase())}${fuText(fu, y).trim() ? "" : " ·"}</button>`).join("")}<span class="small ${miss.length ? "warn-t" : "ok-t"}">${miss.length ? `Missing in ${esc(langList(miss))}` : "Written in every language"}</span></div>` : ""}
      <p class="small muted" style="margin:0">Subject: “Re: ” and the first email's. In the same conversation, no images. {signature} and the opt-out line are added.</p>
      <label class="small muted cx-lab">Message${langs.length > 1 ? ` in ${esc(LANG[l] || l)}` : ""}<textarea class="input mailbody" rows="12" data-fubody="${l}" lang="${l}">${esc(fuText(fu, l))}</textarea></label>
      <div class="row"><span class="small muted">Insert:</span>${["{prenom}", "{ville}", "{signature}"].map((v) => `<button class="cx-var" data-ins="${v}">${v}</button>`).join("")}<span class="grow"></span>${dirty ? `<button class="btn" data-fu-revert>Undo changes</button>` : ""}<button class="btn primary" data-fu-save ${dirty ? "" : "disabled"}>Save ${stepName(k).toLowerCase()}</button></div>
      <p class="small muted" style="margin:0">Saving changes every ${stepName(k).toLowerCase()} not sent yet, including those already waiting for a slot.</p></div>${pv}`;
}

async function loadPreview(c, test = false) {
  const box = $("[data-pv-b]");
  if (!box || P.step === 3) return;
  const k = P.step, lead = c.results.leads.find((l) => l.id === P.pvLead) || c.results.leads[0];
  if (!lead) { box.textContent = "No lead yet."; return; }
  P.pvLead = lead.id;
  const langs = coveredLangs(c.versions);
  const lang = (k && langs.includes(P.fuLang[k]) ? P.fuLang[k] : null) || lead.language || langs[0];
  const body = k && P.fuDraft[k] ? fuText(P.fuDraft[k], lang) : undefined;
  try {
    const p = await api(`/api/campaigns/${c.id}/preview`, { method: "POST", body: { step: k, language: lang, lead_id: lead.id, body, test } });
    if (test) { toast(`Test sent to ${p.sent_to}.`); return; }
    if (!$("[data-pv-b]")) return;
    $("[data-pv-h]").innerHTML = `From <b>${esc(c.sender || "you")}</b> · to ${esc(p.to || "?")}<br><b class="cx-pv-s">${esc(p.subject)}</b>`;
    $("[data-pv-b]").textContent = p.body;
    $("[data-pv-img]").innerHTML = (p.asset_ids || []).map((id) => `<img src="/api/assets/${id}/thumb" alt="">`).join("");
  } catch (e) { if (test) fail(e); else if ($("[data-pv-b]")) $("[data-pv-b]").textContent = e.message || "No preview."; }
}

// --- Settings ----------------------------------------------------------------------------------

function settingsTab(c) {
  const t = c.results.totals, [h0, h1] = c.send_hours || [9, 18];
  const hours = (sel, attr) => `<select class="input sm mono" ${attr}>${Array.from({ length: 25 }, (_, h) => `<option value="${h}" ${h === sel ? "selected" : ""}>${String(h).padStart(2, "0")}:00</option>`).join("")}</select>`;
  const stepper = (attr, val, unit = "") => `<span class="cx-stepper"><button type="button" ${attr} data-d="-1" aria-label="Less">−</button><span class="mono">${val}${unit}</span><button type="button" ${attr} data-d="1" aria-label="More">+</button></span>`;
  const sw = (attr, on, label, dis = false) => `<label class="cx-switch"><input type="checkbox" ${attr} ${on ? "checked" : ""} ${dis ? "disabled" : ""}><span aria-hidden="true"></span><span class="sr">${label}</span></label>`;
  const f = (label, sub, ctl) => `<div class="cx-f"><div class="cx-fl">${label}${sub ? `<span class="small muted">${sub}</span>` : ""}</div><div class="row">${ctl}</div></div>`;
  const chips = activeFilters(c.filters || {});
  return `<div class="cx-split cx-set"><nav class="cx-nav" aria-label="Settings sections">${[["general", "General"], ["reach", "Who it reaches"], ["sending", "Sending"], ["auto", "Automations"], ["rules", "Stop rules"], ["end", "Pause, archive, delete"]].map(([k, l]) => `<a href="#" data-jump="${k}">${l}</a>`).join("")}</nav>
    <div class="cx-pane">
    <section class="card cx-card" id="cs-general"><h2 class="cx-h2">General</h2>
      ${f("<b>Name</b>", "only you see it", `<input class="input" style="flex:1" data-set="name" value="${esc(c.name)}">`)}
      ${f("<b>Goal</b>", "what the A/B test counts", `<span>${c.metric === "interested" ? "Interested leads" : "Replies"}</span><span class="small muted">· fixed once started</span>`)}
      ${f("<b>Notes</b>", "why this campaign, what you're testing", `<textarea class="input" style="flex:1" rows="3" data-set="notes">${esc(c.notes || "")}</textarea>`)}</section>
    <section class="card cx-card" id="cs-reach"><div class="row"><h2 class="cx-h2" style="flex:1">Who it reaches</h2><span class="small muted">filters fixed once started</span></div>
      <div class="row" style="gap:6px">${chips.length ? chips.map(([l]) => `<span class="cx-fchip">${esc(l)}</span>`).join("") : `<span class="small muted">${esc(KINDS_PL[c.kind] || c.kind)}, no other filter</span>`}</div>
      <div class="row"><span class="small muted" style="flex:1">${plural(t.leads, "lead")} in it</span><button class="btn" data-add>Add leads</button><button class="btn" data-tab="leads">Remove or move some</button></div></section>
    <section class="card cx-card" id="cs-sending"><h2 class="cx-h2">Sending</h2>
      ${f("<b>Emails a day</b>", "first emails and follow-ups together", stepper("data-cap", c.daily_cap))}
      ${f("<b>Days</b>", "", DAYS.map((d, i) => `<button type="button" class="cx-day-b" data-wday="${i}" aria-pressed="${(c.send_days || []).includes(i)}">${d}</button>`).join(""))}
      ${f("<b>Hours</b>", "Paris time", `${hours(h0, 'data-hour="0" aria-label="From"')}<span class="muted">to</span>${hours(h1, 'data-hour="1" aria-label="Until"')}`)}
      ${f("<b>First emails</b>", "follow-ups always go out on their own", `<span>${c.send_mode === "scheduled" ? "The app sends them, within the days and hours above" : "You read and send each one"}</span><a class="small" href="#settings">change for all campaigns</a>`)}
      ${f("<b>From</b>", "", `<span>${esc(c.sender || "not connected")}</span>`)}</section>
    <section class="card cx-card" id="cs-auto"><div class="row"><h2 class="cx-h2" style="flex:1">Automations</h2><button class="btn ghost sm" data-tab="emails">Edit the texts</button></div>
      ${f("<b>Follow-up 1</b>", "a reply in the same conversation", `${sw('data-on="1"', c.followups_on !== false, "Follow-up 1")}<span>after</span>${stepper('data-fud="1"', fuDays(c, 1))}<span>days without a reply</span>`)}
      ${f("<b>Follow-up 2</b>", "the last one", `${sw('data-on="2"', c.followup2_on !== false, "Follow-up 2", c.followups_on === false)}<span>after</span>${stepper('data-fud="2"', fuDays(c, 2))}<span>more days</span>`)}
      ${f("<b>Mark “No reply”</b>", "they leave the sequence", `${sw("data-close-on", c.close_on !== false, "No reply")}<span>after</span>${stepper("data-cld", closeDays(c))}<span>days after the last email</span>`)}</section>
    <section class="card cx-card" id="cs-rules"><h2 class="cx-h2">Stop rules</h2>
      ${f("<b>A reply</b>", "always on", `<span>stops every email still to come for that lead</span>`)}
      ${f("<b>“Stop”, “unsubscribe”…</b>", "always on", `<span>marks them Do not contact, in all 13 languages</span>`)}
      ${f("<b>A bounce</b>", "always on", `<span>flags the address; Send then uses their other one, if any</span>`)}
      ${f("<b>Pause if bounces pass</b>", "protects your Gmail reputation", `${sw("data-bp-on", !!c.bounce_pause, "Pause on bounces")}${c.bounce_pause ? `${stepper("data-bp", c.bounce_pause, " %")}<span class="small muted">of first emails, once 20 are out</span>` : `<span class="small muted">off</span>`}`)}</section>
    <section class="card cx-card cx-danger" id="cs-end"><h2 class="cx-h2">Pause, archive, delete</h2>
      ${c.status === "running" ? f(`<button class="btn" data-status="paused">Pause</button>`, "", `<span class="small muted">Nothing goes out until you resume, follow-ups included.</span>`) : c.status === "paused" ? f(`<button class="btn primary" data-status="running">Resume</button>`, "", `<span class="small muted">Follow-ups that came due meanwhile go out first.</span>`) : ""}
      ${f(`<button class="btn" data-dup>Duplicate</button>`, "", `<span class="small muted">A new draft with the same filters, emails and automations.</span>`)}
      ${c.status === "archived" ? f(`<button class="btn" data-status="unarchive">Unarchive</button>`, "", `<span class="small muted">Back in the list, paused.</span>`) : f(`<button class="btn" data-status="archived">Archive</button>`, "", `<span class="small muted">Stops it and hides it from the list. Results kept.</span>`)}
      ${f(`<button class="btn danger" data-del>${P.confirmDel ? "Click again to delete" : "Delete"}</button>`, "", `<span class="small muted">The campaign and its unsent emails. Sent emails stay in each lead's history.</span>`)}</section>
    </div></div>`;
}

// --- events ----------------------------------------------------------------------------------

async function put(c, body, msg) {
  try { await api(`/api/campaigns/${c.id}`, { method: "PUT", body }); if (msg) toast(msg); } catch (e) { fail(e); }
  changed();
}

async function bulk(c, action, ids, extra = {}) {
  const r = await api(`/api/campaigns/${c.id}/leads/bulk`, { method: "POST", body: { action, lead_ids: ids, ...extra } });
  const what = { followup_now: "follow-up written, it goes out at the next free slot", skip: "follow-ups skipped", resume: "follow-ups back on", remove: "removed from the campaign", move: "moved", status: "status changed" }[action];
  const note = r.notes?.length ? ` ${r.notes[0]}${r.notes.length > 1 ? ` (+${r.notes.length - 1} more)` : ""}` : "";
  toast(`${r.done ? `${plural(r.done, "lead")}: ${what}.` : "Nothing changed."}${note}`, { err: !r.done && !!note });
  P.sel.clear();
  changed();
}

function wire(c, toSend) {
  const root = C();
  root.onclick = async (e) => {
    const b = e.target.closest("button, a[data-jump], [data-row], input[data-sel], input[data-all]");
    if (!b) return;
    const d = b.dataset;
    try {
      if (d.tab) { P.tab = d.tab; renderCampaign(c); window.scrollTo?.(0, 0); }
      else if (d.goto) { P.tab = "leads"; P.stage = d.goto; renderCampaign(c); }
      else if (d.sendNext !== undefined) await sendFrom(c, toSend, 0);
      else if (d.sendLead) { e.stopPropagation(); await sendFrom(c, toSend, Math.max(0, toSend.indexOf(+d.sendLead))); }
      else if (d.add !== undefined) addLeads(c);
      else if (d.status) await busy(b, async () => { await api(`/api/campaigns/${c.id}/status`, { method: "POST", body: { status: d.status } }); toast({ paused: "Paused. Nothing goes out until you resume.", running: "Resumed.", archived: "Archived.", unarchive: "Unarchived: it's paused." }[d.status]); changed(); });
      else if (d.editFu) editFollowup(c, +d.editFu);
      else if (d.editClose !== undefined) editClose(c);
      else if (d.trEdit) editTranslations(c, +d.trEdit);
      else if (d.pick !== undefined) pickWinner(c);
      else if (d.winner) {
        if (d.confirm !== "1") { d.confirm = "1"; b.textContent = `Confirm: everyone left gets ${d.letter}`; return; }
        await busy(b, async () => { await api(`/api/campaigns/${c.id}/winner`, { method: "POST", body: { template_id: +d.winner } }); toast(`Done: the ${d.unsent} leads left get version ${d.letter}.`); confetti(); changed(); });
      } else if (d.open) { e.stopPropagation(); openLead(+d.open); }
      // leads
      else if (d.stage) { P.stage = d.stage; P.open = null; renderCampaign(c); }
      else if (d.sel) { e.stopPropagation(); P.sel[b.checked ? "add" : "delete"](+d.sel); renderCampaign(c); }
      else if (d.all !== undefined) { shown(c).forEach((l) => P.sel[b.checked ? "add" : "delete"](l.id)); renderCampaign(c); }
      else if (d.clear !== undefined) { P.sel.clear(); renderCampaign(c); }
      else if (d.bulk) {
        if (d.bulk === "remove" && d.confirm !== "1") { d.confirm = "1"; b.textContent = `Confirm: remove ${P.sel.size}`; return; }
        await busy(b, () => bulk(c, d.bulk, [...P.sel]));
      } else if (d.one) {
        if (d.one === "remove" && d.confirm !== "1") { d.confirm = "1"; b.textContent = "Confirm: remove"; return; }
        await busy(b, () => bulk(c, d.one, [+d.id]));
      } else if (d.row) { if (e.target.closest("a, select, input")) return; P.open = P.open === +d.row ? null : +d.row; renderCampaign(c); }
      // emails
      else if (d.step) { P.step = +d.step; renderCampaign(c); }
      else if (d.fulang) { P.fuLang[P.step] = d.fulang; renderCampaign(c); }
      else if (d.ins) { const ta = $("[data-fubody]"); if (ta) { const p = ta.selectionStart ?? ta.value.length; ta.setRangeText(d.ins, p, ta.selectionEnd ?? p, "end"); ta.dispatchEvent(new Event("input", { bubbles: true })); ta.focus(); } }
      else if (d.fuRevert !== undefined) { delete P.fuDraft[P.step]; renderCampaign(c); }
      else if (d.fuSave !== undefined) {
        const k = P.step, text = P.fuDraft[k];
        if (!text?.body.trim()) { toast(`Write ${stepName(k).toLowerCase()} first.`, { err: true }); return; }
        await busy(b, async () => { await api(`/api/campaigns/${c.id}`, { method: "PUT", body: { [FU[k].text]: text } }); delete P.fuDraft[k]; toast(`Saved. Every ${stepName(k).toLowerCase()} not sent yet now uses it.`); changed(); });
      } else if (d.test !== undefined) await busy(b, () => loadPreview(c, true));
      // settings
      else if (d.jump) { e.preventDefault(); $(`#cs-${d.jump}`)?.scrollIntoView({ behavior: "smooth", block: "start" }); }
      else if (d.wday) { const s = new Set(c.send_days); s.has(+d.wday) ? s.delete(+d.wday) : s.add(+d.wday); if (!s.size) { toast("Keep at least one day.", { err: true }); return; } await put(c, { send_days: [...s].sort() }, "Saved."); }
      else if (d.cap !== undefined) await put(c, { daily_cap: Math.max(1, c.daily_cap + +d.d) });
      else if (d.fud) await put(c, { [FU[+d.fud].days]: Math.max(1, Math.min(30, fuDays(c, +d.fud) + +d.d)) });
      else if (d.cld !== undefined) await put(c, { close_days: Math.max(1, Math.min(90, closeDays(c) + +d.d)) });
      else if (d.bp !== undefined) await put(c, { bounce_pause: Math.max(1, Math.min(50, c.bounce_pause + +d.d)) });
      else if (d.dup !== undefined) await busy(b, async () => { const x = await api(`/api/campaigns/${c.id}/duplicate`, { method: "POST" }); location.hash = `campaign/${x.id}`; });
      else if (d.del !== undefined) {
        if (!P.confirmDel) { P.confirmDel = true; renderCampaign(c); return; }
        await busy(b, async () => { await api(`/api/campaigns/${c.id}`, { method: "DELETE" }); toast("Campaign deleted."); location.hash = "campaigns"; });
      }
    } catch (err) { fail(err); }
  };
  root.onchange = async (e) => {
    const el = e.target, d = el.dataset;
    try {
      if (d.f) { P[d.f] = el.value; renderCampaign(c); }
      else if (d.bulkStatus !== undefined && el.value) await bulk(c, "status", [...P.sel], { status: el.value });
      else if (d.bulkMove !== undefined && el.value) await bulk(c, "move", [...P.sel], { target: +el.value });
      else if (d.days) await put(c, { [FU[+d.days].days]: Math.max(1, Math.min(30, +el.value || FU[+d.days].d)) }, "Saved: follow-ups not sent yet follow the new delay.");
      else if (d.closeDays !== undefined) await put(c, { close_days: Math.max(1, Math.min(90, +el.value || 15)) }, "Saved.");
      else if (d.on) await put(c, { [FU[+d.on].on]: el.checked }, el.checked ? `${stepName(+d.on)} on.` : `${stepName(+d.on)} off: nobody gets it.`);
      else if (d.closeOn !== undefined) await put(c, { close_on: el.checked }, el.checked ? "“No reply” on." : "“No reply” off.");
      else if (d.bpOn !== undefined) await put(c, { bounce_pause: el.checked ? 5 : 0 }, el.checked ? "It pauses on its own if 5 % of first emails bounce." : "Off.");
      else if (d.hour) { const h = [...(c.send_hours || [9, 18])]; h[+d.hour] = +el.value; if (h[0] >= h[1]) { toast("The end comes after the start.", { err: true }); renderCampaign(c); return; } await put(c, { send_hours: h }, "Saved."); }
      else if (d.set) await put(c, { [d.set]: el.value }, "Saved.");
      else if (d.pvLead !== undefined) { P.pvLead = +el.value; loadPreview(c); }
    } catch (err) { fail(err); }
  };
  root.oninput = (e) => {
    const el = e.target;
    if (el.dataset.q !== undefined) {
      P.q = el.value;
      clearTimeout(P.qTimer);
      P.qTimer = setTimeout(() => { const pos = el.selectionStart; renderCampaign(c); const q = $("[data-q]"); q?.focus(); q?.setSelectionRange(pos, pos); }, 250);
    } else if (el.dataset.fubody) {
      const k = P.step, x = { versions: c.versions, [FU[k].text]: P.fuDraft[k] || structuredClone(fuOf(c, k)), [FU[k].def]: c[FU[k].def] };
      setFuText(x, k, el.dataset.fubody, el.value);
      const first = !P.fuDraft[k];
      P.fuDraft[k] = x[FU[k].text];
      if (first) { const pos = el.selectionStart; renderCampaign(c); const ta = $("[data-fubody]"); ta?.focus(); ta?.setSelectionRange(pos, pos); }
      clearTimeout(P.pvTimer);
      P.pvTimer = setTimeout(() => loadPreview(c), 400);
    }
  };
}
