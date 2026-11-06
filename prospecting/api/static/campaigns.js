// Campaigns: the list, one campaign (a three-step builder while it's a draft, its results
// once started) and dossiers (reusable sets of images).
import {
  $, aiBar, api, busy, changed, closeModal, confetti, COUNTRY, esc, fail, flag, I, LANG, idChips, kindIcon, KINDS_PL, LABELS, labelChips, openModal,
  small, toast, when, wireAiBar,
} from "./core.js";
import { openLead, openReview } from "./lead.js";
import { campaigns as templatesUI } from "./views.js";

const C = () => $("#canvas");
const SEGS = ["photographer", "agency", "independent_agent"];
const LET = ["A", "B", "C", "D"];
const VCOL = ["var(--emerald)", "var(--blue)", "var(--violet)", "var(--warn)"];
const pct = (v) => (v == null ? "–" : `${Math.round(v * 100)} %`);
const plural = (n, one, many = one + "s") => `${n.toLocaleString("en")} ${n === 1 ? one : many}`;
const STATUS = { draft: ["Draft", "st-new"], running: ["Running", "st-pilot"], paused: ["Paused", "st-contacted"], done: ["Finished", "st-customer"] };
const statusPill = (s) => `<span class="pill ${STATUS[s]?.[1] || ""}">${STATUS[s]?.[0] || esc(s)}</span>`;
const workdays = (n, cap) => Math.max(1, Math.ceil(n / Math.max(cap, 1)));
// Languages: a version's subject/body are in its main language; translations hold the
// same email in others. A lead gets its own language, if every version is written in it.
const mainLang = (v) => v.language || "fr";
const vLangs = (v) => [mainLang(v), ...Object.keys(v.translations || {}).filter((x) => x !== mainLang(v))];
const textOf = (v, lang) => (lang === mainLang(v) ? v : v.translations?.[lang] || { subject: "", body: "" });
const written = (v, lang) => lang === mainLang(v) || !!(textOf(v, lang).subject?.trim() && textOf(v, lang).body?.trim());
const coveredLangs = (vs) => vs.reduce((acc, v) => { const mine = vLangs(v).filter((l) => written(v, l)); return acc ? acc.filter((x) => mine.includes(x)) : mine; }, null) || ["fr"];
const langList = (ls) => ls.map((l) => LANG[l] || l).join(", ");
const usesLean = (vs) => vs.some((v) => `${v.subject} ${v.body}`.includes("{lean}"));

// --- the list --------------------------------------------------------------------------

export const campaignsView = {
  async render() {
    const [c, t] = await Promise.all([api("/api/campaigns"), api("/api/templates")]);
    const mb = c.mailbox;
    const card = (x) => {
      const r = x.results, tot = r?.totals;
      const test = r?.test;
      const body = x.status === "draft"
        ? `<p class="small muted" style="margin:0">${plural(x.preview.chosen, "lead")} chosen of ${x.preview.matching.toLocaleString("en")} matching · ${x.versions.length > 1 ? `A/B test of ${x.versions.length} versions` : "one email"} · not started</p>`
        : `<div class="cp-bar" title="${tot.replied} replied · ${tot.sent - tot.replied} waiting · ${tot.to_review + tot.approved} written · ${tot.not_sent} not written yet">
             <span style="flex:${tot.replied};background:var(--warn)"></span><span style="flex:${tot.sent - tot.replied};background:var(--emerald)"></span><span style="flex:${tot.to_review + tot.approved};background:var(--mint)"></span><span style="flex:${tot.not_sent}"></span></div>
           <div class="cp-stats"><div><b>${tot.sent}</b><span>of ${tot.leads} emailed</span></div><div><b>${tot.replied}</b><span>replied${tot.sent ? ` · ${pct(tot.replied / tot.sent)}` : ""}</span></div><div><b>${tot.interested}</b><span>interested</span></div><div><b>${tot.pilots}</b><span>free tests</span></div></div>
           ${test?.suggest ? `<div class="note ok">${small(I.check)} <span>Version ${esc(test.suggest.letter)} is clearly better. Open it to send ${esc(test.suggest.letter)} to the ${test.suggest.unsent} left.</span></div>`
             : test && tot.sent ? `<p class="small muted" style="margin:0">A/B test: ${esc(test.verdict.text)}</p>` : ""}
           ${tot.to_review ? `<p class="small" style="margin:0"><a href="#today">${plural(tot.to_review, "email")} waiting for your review in Today</a></p>` : ""}`;
      return `<a class="card cp-card" href="#campaign/${x.id}"><div class="row"><h3>${esc(x.name)}</h3>${statusPill(x.status)}<span class="grow"></span><span class="small muted">${esc(KINDS_PL[x.kind] || x.kind)}</span></div>${body}</a>`;
    };
    const tpls = t.items;
    C().innerHTML = `<div class="view">
      <div class="topline"><div><h1>Campaigns</h1><div class="sub">Choose who to write to, write one email or several versions to compare, and watch what works.</div></div><span class="grow"></span>
        <a class="btn" href="#dossiers">${small(I.copy)} Dossiers</a><button class="btn primary" data-new>${small(I.plus)} New campaign</button></div>
      ${!mb.configured ? `<div class="note warn" style="margin-bottom:14px">Your mailbox isn't connected, so campaigns can't send yet. <a href="#settings">Connect it</a></div>` : ""}
      ${c.items.length ? `<div class="grid2">${c.items.map(card).join("")}</div>`
        : `<section class="card cp-empty"><h3>Your first campaign</h3><p class="muted">Three short steps: who you write to and how many, the email (or two versions to compare), and how many go out each day. The app writes each morning's emails; you approve them in Today.</p>
            <div class="row"><button class="btn primary" data-new data-kind="photographer">Start with real-estate photographers</button><button class="btn" data-new data-kind="agency">Agencies</button></div></section>`}
      <details class="card" style="margin-top:18px"><summary class="row" style="cursor:pointer"><h2 style="margin:0;font-size:16px">Reply, follow-up and other templates</h2><span class="small muted">used outside campaigns, and by every campaign's follow-ups</span></summary>
        <div style="margin-top:10px">${tpls.map((x) => `<div class="tpl-row" data-tpl="${x.id}" ${x.archived ? 'style="opacity:.5"' : ""}><div style="min-width:0"><b>${esc(x.name)}</b><div class="small muted" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(x.subject)} · ${esc(x.body.replace(/\s+/g, " ").slice(0, 100))}…</div></div><span class="tag">${esc({ 0: "First email", 1: "Follow-up 1", 2: "Follow-up 2", "-1": "Reply" }[x.step] ?? x.step)}</span><span class="small mono">${x.sent ? `${x.sent} sent` : ""}</span></div>`).join("")}
        <button class="btn sm" data-new-tpl style="margin-top:10px">${small(I.plus)} Template</button></div></details>
    </div>`;
    templatesUI.t = t;
    C().onclick = async (e) => {
      const b = e.target.closest("button, [data-tpl]");
      if (!b) return;
      if (b.dataset.new !== undefined) {
        await busy(b, async () => {
          const x = await api("/api/campaigns", { method: "POST", body: { kind: b.dataset.kind || "photographer", name: b.dataset.kind ? `${KINDS_PL[b.dataset.kind]} · ${new Date().toLocaleDateString("en-GB", { month: "long" })}` : "New campaign" } });
          location.hash = `campaign/${x.id}`;
        });
      } else if (b.dataset.newTpl !== undefined) templatesUI.editTpl(null, { onSaved: () => this.render() });
      else if (b.dataset.tpl) templatesUI.editTpl(t.items.find((x) => x.id === +b.dataset.tpl), { onSaved: () => this.render() });
    };
  },
};

// --- one campaign ------------------------------------------------------------------------

const B = { c: null, aud: null, step: "who", ver: 0, lang: null, sigs: null, sigTimer: null, depOptions: [], dossiers: null, timer: null, audTimer: null, shown: 25, look: [], lookQ: "", lookTimer: null };

export const campaignView = {
  async render(arg) {
    const id = +arg;
    if (!id) { location.hash = "campaigns"; return; }
    const c = await api(`/api/campaigns/${id}`);
    if (B.c?.id !== id) Object.assign(B, { step: "who", ver: 0, lang: null, depOptions: [], aud: null, shown: 25, look: [], lookQ: "" });
    c.included = c.included || [];
    B.c = c;
    const st = await api("/api/settings").catch(() => ({}));
    B.manual = st.send_mode !== "scheduled";
    B.sigs = { fr: st.signature || "", ...(st.signatures || {}) };
    if (c.status === "draft") {
      B.dossiers = (await api("/api/dossiers")).items;
      renderBuilder();
      refreshAudience(true);
    } else renderResults(c);
  },
  leave() { flushSave(); },
};

// --- the builder (a draft) ----------------------------------------------------------------

function payload() {
  const c = B.c;
  return {
    name: c.name, kind: c.kind, filters: c.filters, size: c.size, order: c.order, excluded: c.excluded, included: c.included,
    daily_cap: c.daily_cap, review: c.review, metric: c.metric, start_on: c.start_on || "", followups_on: c.followups_on,
    versions: c.versions.map((v) => ({ id: v.id || null, name: v.name, subject: v.subject, body: v.body, asset_ids: v.asset_ids, attach_photo: v.attach_photo, language: mainLang(v), translations: v.translations || {} })),
  };
}
function save() {
  clearTimeout(B.timer);
  B.timer = setTimeout(flushSave, 500);
  const s = $("[data-saved]");
  if (s) s.textContent = "Saving…";
}
async function flushSave() {
  if (!B.timer || !B.c || B.c.status !== "draft") return;
  clearTimeout(B.timer);
  B.timer = null;
  try {
    const r = await api(`/api/campaigns/${B.c.id}`, { method: "PUT", body: payload() });
    r.versions.forEach((v, i) => { if (B.c.versions[i]) B.c.versions[i].id = v.id; });
    const s = $("[data-saved]");
    if (s) s.textContent = "Saved";
  } catch (e) { fail(e); }
}
function refreshAudience(first = false) {
  clearTimeout(B.audTimer);
  B.audTimer = setTimeout(async () => {
    const c = B.c;
    try {
      B.aud = await api("/api/campaigns/audience", { method: "POST", body: { kind: c.kind, filters: c.filters, order: c.order, size: c.size, excluded: c.excluded, included: c.included, shown: B.shown, campaign_id: c.id, needs_lean: usesLean(c.versions), languages: coveredLangs(c.versions) } });
      if (!(c.filters.deps || []).length) B.depOptions = B.aud.deps;
      if (B.step === "who") drawStep(); else drawSummary();
      if (B.step === "emails") { drawLangTabs(); preview(); }
      drawSteps();
    } catch (e) { fail(e); }
  }, first ? 0 : 250);
}

function renderBuilder() {
  const c = B.c;
  C().onclick = C().onchange = C().ondragover = C().ondrop = null; // the builder listens on its own
  B.lean = usesLean(c.versions);
  C().innerHTML = `<div class="view wide cb">
    <div class="cb-top"><div style="flex:1;min-width:260px"><p class="small muted" style="margin:0"><a href="#campaigns">Campaigns</a> / new · <span data-saved>Saved</span></p>
      <input class="cb-name" data-f="name" value="${esc(c.name)}" aria-label="Campaign name" placeholder="Name this campaign"></div>
      <button class="btn ghost danger sm" data-delete>Delete draft</button></div>
    <div class="cb-grid">
      <ol class="cb-steps" aria-label="Steps" data-steps></ol>
      <main class="card cb-main" data-step></main>
      <aside class="cb-side" data-summary></aside>
    </div></div>`;
  drawSteps();
  drawStep();
}

function drawSteps() {
  const c = B.c, a = B.aud;
  const n = c.versions.length;
  const steps = [
    ["who", "Who", a ? `${a.chosen} of ${a.matching.toLocaleString("en")} leads` : "…"],
    ["emails", n > 1 ? "Emails · A/B test" : "Email", n > 1 ? `${n} versions to compare` : "one version"],
    ["sending", "Sending", `${c.daily_cap} a day · ${B.manual ? "you send each one" : c.review === "auto" ? "approved once" : "you review daily"}`],
  ];
  const el = $("[data-steps]");
  if (el) el.innerHTML = steps.map(([k, t, sub], i) => `<li><button data-go="${k}" aria-current="${B.step === k ? "step" : "false"}"><span class="cb-n">${i + 1}</span><span><b>${t}</b><span class="small muted">${esc(sub)}</span></span></button></li>`).join("");
  drawSummary();
}

function drawStep() {
  const el = $("[data-step]");
  if (!el) return;
  // Redrawing replaces the inputs: keep the one being typed in focused, caret in place.
  const focus = document.activeElement?.id && el.contains(document.activeElement) ? document.activeElement : null;
  const keep = focus && { id: focus.id, s: focus.selectionStart, e: focus.selectionEnd };
  const i = ["who", "emails", "sending"].indexOf(B.step);
  el.innerHTML = (B.step === "who" ? stepWho() : B.step === "emails" ? stepEmails() : stepSending())
    + `<div class="cb-nav">${i ? `<button class="btn" data-go="${["who", "emails", "sending"][i - 1]}">Back</button>` : ""}<span class="grow"></span>
       ${i < 2 ? `<button class="btn primary" data-go="${["who", "emails", "sending"][i + 1]}">Next: ${["", "the email", "sending"][i + 1]} →</button>` : `<button class="btn primary" data-start>Review and start</button>`}</div>`;
  if (B.step === "emails") wireEmails();
  if (keep) {
    const again = document.getElementById(keep.id);
    if (again) { again.focus(); try { again.setSelectionRange(keep.s, keep.e); } catch {} }
  }
}

// --- step 1: who ---------------------------------------------------------------------------
// Filters live in c.filters (see campaigns.candidates). Chips show, from the facet counts,
// how many of the current matches each one would keep.

const NAMES = {
  first_name: "first name known", email_personal: "a person's email", phone: "a phone to call",
  small: "small or new", solo: "works alone", measured: "photos measured", shortlisted: "shortlisted by you",
};
const n0 = (n) => (n == null ? "" : ` <span class="fn">${Number(n).toLocaleString("en")}</span>`);
const PRESETS = {
  photographer: [
    ["Small and new near Strasbourg", { small: true, max_km: 60 }],
    ["Their photos lean", { measured: true, lean_min: 1 }],
    ["Holiday lets and Airbnb", { tags_any: ["short_stay"] }],
    ["Luxury properties", { tags_any: ["prestige"] }],
    ["Solo, with a phone to call", { solo: true, phone: true }],
    ["On Instagram, a person's email", { socials: ["instagram"], email_personal: true }],
  ],
  agency: [
    ["Independent agencies", { network: "independent" }],
    ["Their photos lean", { measured: true, lean_min: 1 }],
    ["Luxury agencies", { tags_any: ["prestige"] }],
    ["Holiday lets", { tags_any: ["short_stay"] }],
    ["Small, near Strasbourg", { solo: true, max_km: 60 }],
    ["A person's email", { email_personal: true, first_name: true }],
  ],
  independent_agent: [
    ["Near Strasbourg", { max_km: 60 }],
    ["A person's email", { email_personal: true }],
  ],
};
const TAGS = {};
const tagName = (t) => (TAGS[t] || t.replace("_", " ")).toLowerCase();

/** The active filters as removable chips: [label, key, value-for-lists]. */
function activeFilters(f) {
  const out = [];
  for (const k of Object.keys(NAMES)) if (f[k]) out.push([NAMES[k], k]);
  if (f.website === "yes" || f.website === true) out.push(["has a website", "website"]);
  if (f.website === "no") out.push(["no website", "website"]);
  (f.socials || []).forEach((s) => out.push([`on ${s[0].toUpperCase() + s.slice(1)}`, "socials", s]));
  (f.tags_any || []).forEach((t) => out.push([`does ${tagName(t)}`, "tags_any", t]));
  (f.tags_none || []).forEach((t) => out.push([`not ${tagName(t)}`, "tags_none", t]));
  (f.labels_any || []).forEach((id) => out.push([`tagged ${LABELS.byId[id]?.name || "?"}`, "labels_any", id]));
  (f.labels_none || []).forEach((id) => out.push([`not tagged ${LABELS.byId[id]?.name || "?"}`, "labels_none", id]));
  (f.countries || []).forEach((c) => out.push([`in ${COUNTRY[c] || c}`, "countries", c]));
  (f.languages || []).forEach((x) => out.push([`speaks ${LANG[x] || x}`, "languages", x]));
  (f.deps || []).forEach((d) => out.push([`in ${d}`, "deps", d]));
  (f.networks || []).forEach((n) => out.push([n, "networks", n]));
  if (f.network === "independent") out.push(["independent (no network)", "network"]);
  if (f.network === "network") out.push(["in a network", "network"]);
  if (f.max_km) out.push([`within ${f.max_km} km`, "max_km"]);
  if (f.founded_years) out.push([`founded ≤ ${f.founded_years} year${f.founded_years > 1 ? "s" : ""} ago`, "founded_years"]);
  if (f.lean_min) out.push([`photos lean ${String(f.lean_min).replace(".", ",")}°+`, "lean_min"]);
  if (f.score_min) out.push([`score ${f.score_min}+`, "score_min"]);
  if (f.town) out.push([`town: “${f.town}”`, "town"]);
  if (f.q) out.push([`search: “${f.q}”`, "q"]);
  return out;
}

function chip(label, attrs, on, n) {
  return `<button class="fchip" type="button" ${attrs} aria-pressed="${!!on}">${on ? "✓ " : ""}${label}${on ? "" : n0(n)}</button>`;
}
const choice = (key, val, label, f, n) => chip(label, `data-fset="${key}" data-fval="${val}"`, String(f[key] ?? "") === String(val), n);
const sub = (t) => `<span class="small muted" style="text-transform:none;letter-spacing:0;font-weight:400">${t}</span>`;

function stepWho() {
  const c = B.c, f = c.filters, a = B.aud, fa = a?.facets || {};
  (fa.tags || []).forEach((t) => (TAGS[t.tag] = t.label));
  const opts = B.depOptions;
  const top = opts.slice(0, 6).map((x) => x.dep);
  const shownDeps = [...new Set([...top, ...(f.deps || [])])];
  const rest = opts.filter((x) => !shownDeps.includes(x.dep));
  const depN = (d) => opts.find((x) => x.dep === d)?.n;
  const max = Math.max(a?.matching || 0, c.size, 10);
  const lean = usesLean(c.versions);
  const act = activeFilters(f);
  const agency = c.kind === "agency";
  const tri = (t) => ((f.tags_any || []).includes(t) ? "any" : (f.tags_none || []).includes(t) ? "none" : "");
  const tagChip = (t) => {
    const st = tri(t.tag);
    return `<button class="fchip${st === "none" ? " no" : ""}" type="button" data-ftag="${t.tag}" aria-pressed="${!!st}" title="Once: only those who do it. Twice: leave them out. Three times: either.">${st === "any" ? "✓ " : st === "none" ? "✕ " : ""}${esc(t.label)}${st ? "" : n0(t.n)}</button>`;
  };
  const allTags = [...(fa.tags || []), ...[...(f.tags_any || []), ...(f.tags_none || [])].filter((t) => !(fa.tags || []).some((x) => x.tag === t)).map((t) => ({ tag: t, label: TAGS[t] || t }))];
  const list = a?.preview || [];
  const hand = list.filter((l) => l.by_hand).length || (c.included || []).length;
  return `<h2>Who do you want to reach?</h2>
    <p class="muted">Every click updates the count. The grey number on a filter is how many of the current matches it would keep. Leads already contacted, do-not-contact, without an address of their own, or waiting in another campaign are always left out.</p>
    <div class="cb-sec"><h4>Segment</h4><div class="seg" role="group" aria-label="Segment">${SEGS.map((k) => `<button data-kind="${k}" aria-pressed="${c.kind === k}">${KINDS_PL[k]}</button>`).join("")}</div></div>

    <div class="cb-sec"><h4>Start from ${sub("a ready-made selection, then refine it")}</h4><div class="chips">${(PRESETS[c.kind] || []).map(([t], i) => `<button class="fchip preset" type="button" data-preset="${i}">${esc(t)}</button>`).join("")}</div></div>

    <div class="cb-active" aria-live="polite">
      <span class="cb-count"><b>${a ? a.matching.toLocaleString("en") : "…"}</b> match</span>
      ${act.length ? act.map(([t, k, v]) => `<button class="achip" type="button" data-fdel="${k}" ${v != null ? `data-fval="${esc(v)}"` : ""} aria-label="Remove the filter ${esc(t)}">${esc(t)} <span aria-hidden="true">×</span></button>`).join("") + `<button class="linkish small" type="button" data-fclear>Clear all</button>` : `<span class="small muted">No filter yet: every reachable lead in this segment is in.</span>`}
    </div>

    <div class="cb-filters">
      <details class="fgrp" open><summary>Where</summary>
        <div class="chips">
          <label class="fchip sel">within <select data-km aria-label="Distance from Strasbourg">${[["", "any distance"], [10, "10 km"], [20, "20 km"], [40, "40 km"], [60, "60 km"], [100, "100 km"], [200, "200 km"]].map(([v, t]) => `<option value="${v}" ${String(f.max_km || "") === String(v) ? "selected" : ""}>${t}</option>`).join("")}</select> of Strasbourg</label>
          <label class="fchip sel ftext">town <input id="f-town" data-ftext="town" value="${esc(f.town || "")}" placeholder="e.g. Colmar" autocomplete="off"></label>
        </div>
        ${Object.keys(fa.countries || {}).length > 1 || (f.countries || []).length ? `<div class="chips"><span class="small muted">Country</span>${Object.keys(COUNTRY).filter((c) => fa.countries?.[c] || (f.countries || []).includes(c)).map((c) => chip(`${flag(c)} ${esc(COUNTRY[c])}`, `data-flist="countries" data-fval="${c}"`, (f.countries || []).includes(c), (f.countries || []).length ? null : fa.countries?.[c])).join("")}</div>` : ""}
        ${Object.keys(fa.languages || {}).length > 1 || (f.languages || []).length ? `<div class="chips"><span class="small muted">Language</span>${Object.keys(LANG).filter((x) => fa.languages?.[x] || (f.languages || []).includes(x)).map((x) => chip(esc(LANG[x]), `data-flist="languages" data-fval="${x}"`, (f.languages || []).includes(x), (f.languages || []).length ? null : fa.languages?.[x])).join("")}</div>` : ""}
        ${opts.length > 1 || (f.deps || []).length ? `<div class="chips"><span class="small muted">Département</span>${shownDeps.map((d) => chip(esc(d), `data-dep="${esc(d)}"`, (f.deps || []).includes(d), (f.deps || []).length ? null : depN(d))).join("")}
          ${rest.length ? `<select class="dep-more" data-dep-add aria-label="Another département"><option value="">+ another (${rest.length})</option>${rest.map((x) => `<option value="${esc(x.dep)}">${esc(x.dep)} · ${x.n}</option>`).join("")}</select>` : ""}</div>` : ""}
      </details>

      <details class="fgrp" open><summary>How you can reach them</summary><div class="chips">
        ${chip("first name known", `data-fb="first_name"`, f.first_name, fa.first_name)}
        ${chip("a person's email, not contact@", `data-fb="email_personal"`, f.email_personal, fa.email_personal)}
        ${chip("a phone to call", `data-fb="phone"`, f.phone, fa.phone)}
        ${choice("website", "yes", "has a website", f, fa.website_yes)}
        ${choice("website", "no", "no website", f, fa.website_no)}
        ${["instagram", "facebook", "linkedin"].map((s) => chip(`on ${s[0].toUpperCase() + s.slice(1)}`, `data-flist="socials" data-fval="${s}"`, (f.socials || []).includes(s), fa.socials?.[s])).join("")}
      </div></details>

      <details class="fgrp" open><summary>The business</summary><div class="chips">
        ${chip("small or new", `data-fb="small"`, f.small, fa.small)}
        ${chip("works alone", `data-fb="solo"`, f.solo, fa.solo)}
        ${[1, 2, 5].map((y) => choice("founded_years", y, `founded ≤ ${y} year${y > 1 ? "s" : ""} ago`, f, fa.founded?.[y])).join("")}
        ${agency ? choice("network", "independent", "independent", f, fa.independent) + choice("network", "network", "in a network", f, fa.network) : ""}
        ${chip("shortlisted by you", `data-fb="shortlisted"`, f.shortlisted, fa.shortlisted)}
        ${agency && (fa.networks || []).length ? `<select class="dep-more" data-fnet aria-label="Only this network"><option value="">+ a network</option>${fa.networks.map((x) => `<option value="${esc(x.name)}">${esc(x.name)} · ${x.n}</option>`).join("")}</select>` : ""}
      </div></details>

      ${allTags.length ? `<details class="fgrp" open><summary>What they do ${sub("once: only them · twice: leave them out")}</summary><div class="chips">${allTags.map(tagChip).join("")}</div></details>` : ""}

      ${LABELS.items.length ? `<details class="fgrp" open><summary>Your tags ${sub("once: only them · twice: leave them out")}</summary><div class="chips">${LABELS.items.map((x) => {
        const st = (f.labels_any || []).includes(x.id) ? "any" : (f.labels_none || []).includes(x.id) ? "none" : "";
        return `<button class="lab c-${esc(x.color)}" type="button" data-flab="${x.id}" aria-pressed="${!!st}">${st === "any" ? "✓ " : st === "none" ? "✕ " : "<i></i>"}${st === "none" ? "not " : ""}${esc(x.name)}${st ? "" : n0(fa.labels?.[x.id] || 0)}</button>`;
      }).join("")}</div></details>` : ""}

      <details class="fgrp" open><summary>Their photos and score</summary><div class="chips">
        ${chip("photos measured", `data-fb="measured"`, f.measured, fa.measured)}
        ${[0.5, 1, 2, 3].map((v) => choice("lean_min", v, `lean ${String(v).replace(".", ",")}°+`, f, fa.lean?.[v])).join("")}
        <span class="small muted">score</span>${[40, 60, 80].map((v) => choice("score_min", v, `${v}+`, f, fa.score?.[v])).join("")}
      </div></details>

      <details class="fgrp" ${f.q ? "open" : ""}><summary>Search their name, people, town, website or your notes</summary>
        <input class="input" id="f-q" data-ftext="q" value="${esc(f.q || "")}" placeholder="e.g. drone, Marie, immo" autocomplete="off" style="width:100%">
      </details>
      ${lean ? `<div class="note info"><span>${c.versions.filter((v) => `${v.subject} ${v.body}`.includes("{lean}")).map((v) => `Version ${LET[c.versions.indexOf(v)]}`).join(" and ")} quotes their measured lean, so only leads with a lean of 1° or more are counted. <button class="linkish" data-go="emails">Change the email</button></span></div>` : ""}
    </div>

    <div class="cb-sec"><h4><label for="f-add">Add someone by hand</label> ${sub("any lead, even one the filters leave out")}</h4>
      <div class="search"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/></svg><input id="f-add" data-lookup value="${esc(B.lookQ || "")}" placeholder="Name, town, website or postcode" autocomplete="off"></div>
      ${B.look?.length ? `<div class="cb-list cb-look">${B.look.map((l) => {
        const inside = (c.included || []).includes(l.id);
        return `<div class="cb-row"><span></span><span class="nm"><b>${esc(l.person || l.name)}</b>${l.person ? ` <span class="muted">· ${esc(l.name)}</span>` : ""}</span><span class="muted">${esc(l.city || "")}${l.dep ? ` (${esc(l.dep)})` : ""}</span><span class="small muted">${esc(KINDS_PL[l.kind] || l.kind)}</span>
          <span class="small ${l.why ? "muted" : ""}">${l.why ? `Can't: ${esc(l.why)}` : l.kind !== c.kind ? "another segment: check your email fits" : ""}</span>
          ${inside ? `<span class="small" style="color:var(--ink)">added</span>` : l.why ? `<span></span>` : `<button class="btn sm" type="button" data-hand="${l.id}">Add</button>`}</div>`;
      }).join("")}</div>` : (B.lookQ || "").length >= 2 ? `<p class="small muted">No lead matches “${esc(B.lookQ)}”.</p>` : ""}
      ${a?.blocked?.length ? `<div class="note warn" style="margin-top:8px"><span>Added by hand but can't be written to: ${a.blocked.map((l) => `${esc(l.name)} (${esc(l.why)}) <button class="linkish small" data-unhand="${l.id}">remove</button>`).join(" · ")}</span></div>` : ""}
    </div>

    <div class="cb-two">
      <div class="cb-sec"><h4><label for="cb-size">How many from the filters</label></h4>
        <div class="cb-big"><input type="number" id="cb-size" data-size min="1" max="${max}" value="${c.size}"><span class="muted">of ${a ? a.matching.toLocaleString("en") : "…"} matching</span></div>
        <input type="range" data-size min="1" max="${max}" value="${Math.min(c.size, max)}" aria-label="How many from the filters" style="width:100%;accent-color:var(--emerald)">
        <p class="small muted" style="margin:4px 0 0">${a && hand ? `Plus ${plural(a.chosen - a.from_filters, "lead")} added by hand: ${a.chosen} in all. ` : ""}${a && a.from_filters < c.size ? `Only ${a.from_filters} match, so that's all the filters give. ` : ""}At ${c.daily_cap} a day: about ${plural(workdays(a?.chosen ?? c.size, c.daily_cap), "working day")}.${c.versions.length > 1 ? ` About ${Math.floor((a?.chosen ?? c.size) / c.versions.length)} per version.` : ""}</p></div>
      <div class="cb-sec"><h4>Who goes first</h4><div class="cb-opts">${[["small", "Small and new first", "then by score"], ["score", "Best score first", ""], ["random", "At random", "fairest when comparing versions"]].map(([k, t, h]) => `<button data-order="${k}" aria-pressed="${c.order === k}"><b>${t}</b> <span class="small muted">${h}</span></button>`).join("")}</div></div>
    </div>

    <div class="cb-sec"><h4>Who will get it ${sub("in sending order · untick anyone to leave them out")}</h4>
      ${!a ? `<p class="muted small">Counting…</p>` : !list.length ? `<div class="note warn">No lead matches. Remove a filter above, or add someone by hand.</div>`
        : `<div class="cb-list cb-who">${list.map((l, i) => `<label class="cb-row"><input type="checkbox" class="chk" data-keep="${l.id}" ${l.by_hand ? `data-byhand="1"` : ""} checked aria-label="Keep ${esc(l.person || l.name)}">
            <span class="nm"><span class="dim mono small">${i + 1}</span> ${kindIcon(l.kind)} <b>${esc(l.person || l.name)}</b>${l.person ? ` <span class="muted">· ${esc(l.name)}</span>` : ""}${l.by_hand ? ` <span class="pill st-pilot">by hand</span>` : ""}</span>
            <span class="muted">${esc(l.city || "")}${l.dep ? ` (${esc(l.dep)})` : ""}</span>
            <span class="mono small">${l.lean_median != null ? `${l.lean_median.toFixed(1).replace(".", ",")}°` : ""}</span>
            <span class="small tagline">${labelChips(l)}${idChips(l, { max: 2 })}${l.phone_ok ? `<span title="Has a phone">☎</span>` : ""}</span>
            <button class="linkish small" data-peek="${l.id}" type="button">open</button></label>`).join("")}</div>
          ${a.chosen > list.length ? `<p class="small muted" style="margin:8px 0 0">Showing ${list.length} of ${a.chosen}. <button class="linkish small" type="button" data-more-list>Show ${Math.min(50, a.chosen - list.length)} more</button> · <button class="linkish small" type="button" data-all-list>show all</button></p>` : ""}`}
      ${a?.excluded.length ? `<p class="small muted" style="margin:8px 0 0">Left out by you: ${a.excluded.map((l) => `${esc(l.person || l.name)} <button class="linkish small" data-unexclude="${l.id}">put back</button>`).join(" · ")}</p>` : ""}</div>`;
}

function curLang(v) {
  const ls = vLangs(v);
  return ls.includes(B.lang) ? B.lang : ls[0];
}

function langTabs(v) {
  const c = B.c, main = mainLang(v), cur = curLang(v), ls = vLangs(v);
  const n = B.aud?.by_language || {}, left = B.aud?.left_out || {};
  const others = Object.keys(LANG).filter((l) => !ls.includes(l)).sort((a, b) => (n[b] || 0) - (n[a] || 0));
  const many = c.versions.length > 1;
  const onlyHere = many ? ls.filter((l) => written(v, l) && c.versions.some((x) => !vLangs(x).includes(l) || !written(x, l))) : [];
  return `<div class="cb-ltabs" role="tablist" aria-label="Languages"><span class="small muted">Language</span>
      ${ls.map((l) => `<button role="tab" data-lang-tab="${l}" aria-selected="${l === cur}" title="${n[l] || 0} matching leads speak ${esc(LANG[l])}">${esc(LANG[l])}${l === main ? ` <span class="small muted">main</span>` : ""} <span class="mono small muted">${n[l] || 0}</span>${written(v, l) ? "" : ` <span class="lt-todo" title="Needs a subject and a text">to write</span>`}</button>`).join("")}
      <select class="input sm" data-add-lang aria-label="Add a language"><option value="">+ Language</option>${others.map((l) => `<option value="${l}">${esc(LANG[l])}${n[l] ? ` · ${n[l]} leads` : ""}</option>`).join("")}</select>
      ${cur === main ? `<label class="small muted lt-main">This text is in <select class="input sm" data-main-lang>${Object.keys(LANG).map((l) => `<option value="${l}" ${l === main ? "selected" : ""}>${esc(LANG[l])}</option>`).join("")}</select></label>`
        : `<button class="btn ghost danger sm" data-del-lang="${cur}">Remove ${esc(LANG[cur])}</button>`}</div>
    ${Object.keys(left).length ? `<div class="note warn"><span><b>${plural(Object.values(left).reduce((a, b) => a + b, 0), "lead")} left out</b>: no text in their language yet (${Object.entries(left).map(([l, k]) => `${esc(LANG[l] || l)} ${k}`).join(" · ")}). They aren't written to until it's added above. Nobody gets an email in a language they don't speak.</span></div>` : ""}
    ${onlyHere.length ? `<div class="note info"><span>${esc(langList(onlyHere))}: written in some versions only. Leads speaking it join once every version has it.</span></div>` : ""}`;
}

function drawLangTabs() {
  const v = B.c.versions[B.ver], el = $(".cb-ltabs");
  if (!v || !el) return;
  const tmp = document.createElement("div");
  tmp.innerHTML = langTabs(v);
  el.parentNode.querySelectorAll(":scope > .cb-ltabs ~ .note").forEach((n) => { if (!n.closest(".cb-ed")) n.remove(); });
  el.replaceWith(...tmp.childNodes);
}

function stepEmails() {
  const c = B.c, v = c.versions[B.ver] || c.versions[0];
  const many = c.versions.length > 1;
  const ds = B.dossiers || [];
  const imgs = ds.flatMap((d) => d.images.map((x) => ({ ...x, dossier: d.name })));
  const picked = new Set(v.asset_ids);
  const VARS = ["prenom", "agence", "ville", "lean", "nb_images", "signature"];
  const main = mainLang(v), lang = curLang(v), tx = textOf(v, lang);
  return `<h2>What do they get?</h2>
    <p class="muted">${many ? "Each lead gets one version, at random, in equal shares. Change one thing between versions (the subject, the length, the images) to learn what works." : "Write the email. Add a version B to compare two emails on the same leads."}</p>
    <div class="cb-vtabs" role="tablist" aria-label="Versions">${c.versions.map((x, i) => `<button role="tab" data-ver="${i}" aria-selected="${i === B.ver}"><span class="vl" style="background:${VCOL[i]}">${LET[i]}</span>${esc(x.name && x.name !== LET[i] ? x.name : `Version ${LET[i]}`)}${many ? ` <span class="small muted">${Math.round(100 / c.versions.length)} %</span>` : ""}</button>`).join("")}
      ${c.versions.length < 4 ? `<button class="cb-add" data-add-ver>+ Version ${LET[c.versions.length]} <span class="small muted">starts as a copy</span></button>` : ""}</div>
    ${langTabs(v)}
    <div class="cb-ed">
      <div class="row"><label class="small muted" style="flex:1">Short name, for the results<input class="input" data-v="name" value="${esc(v.name && v.name !== LET[B.ver] ? v.name : "")}" placeholder="e.g. Long, with 3 images"></label>${many ? `<button class="btn ghost danger sm" data-del-ver style="align-self:end">Remove version ${LET[B.ver]}</button>` : ""}</div>
      <label class="small muted">Subject${lang !== main ? ` in ${esc(LANG[lang])}` : ""}<input class="input cb-subj" data-v="subject" value="${esc(tx.subject)}" lang="${lang}" placeholder="${lang === main ? "{prenom}, votre avis sur ces retouches ?" : esc(v.subject)}"></label>
      <label class="small muted">Email${lang !== main ? ` in ${esc(LANG[lang])}` : ""}<textarea class="input mailbody" data-v="body" lang="${lang}" placeholder="${lang === main ? "" : `The same email in ${esc(LANG[lang])}`}">${esc(tx.body)}</textarea></label>
      ${lang !== "fr" ? `<label class="small muted">Your signature in ${esc(LANG[lang])} <span>(the {signature} placeholder, for every email in ${esc(LANG[lang])})</span><textarea class="input" rows="2" data-sig="${lang}" lang="${lang}" placeholder="${esc(B.sigs?.fr || "")}">${esc(B.sigs?.[lang] || "")}</textarea></label>` : ""}
      <div class="ins-row"><span class="small muted">Insert</span>${VARS.map((k) => `<button type="button" class="chip mono" data-ins="{${k}}">{${k}}</button>`).join("")}</div>
      ${aiBar(B.ai)}
      <div class="cb-imgs"><div class="row"><h4 style="margin:0">Images</h4><span class="small muted">${picked.size ? `${plural(picked.size, "image")} attached` : "none attached"}</span><span class="grow"></span><a class="small" href="#dossiers">Manage dossiers</a></div>
        ${imgs.length ? ds.filter((d) => d.images.length).map((d) => `<div class="cb-dos"><span class="small muted">${esc(d.name)}</span><div class="cb-thumbs">${d.images.map((x) => `<button type="button" class="cb-th" data-img="${x.id}" aria-pressed="${picked.has(x.id)}" title="${esc(x.name)}${x.sent ? ` · sent ${x.sent}×, ${pct(x.replied / x.sent)} replied` : ""}"><img src="${x.thumb}" alt="${esc(x.name)}" loading="lazy"><span class="ck">${picked.has(x.id) ? small(I.check, 12) : ""}</span></button>`).join("")}</div></div>`).join("")
          : `<p class="small muted" style="margin:0">No images yet. <a href="#dossiers">Add before/after photos to a dossier</a>, then tick them here.</p>`}
      </div>
      <div class="cb-prev"><div class="row"><h4 style="margin:0">What a real lead gets</h4><span class="grow"></span><button class="btn sm" data-test>${small(I.send)} Send ${many ? "every version" : "it"} to me</button></div><div data-preview class="pv"><p class="small muted" style="padding:12px 16px;margin:0">Loading…</p></div></div>
    </div>
    ${many ? `<div class="cb-sec"><h4>The winner is the version with more…</h4><div class="cb-opts row">${[["reply", "Replies", "any answer that isn't a stop"], ["interested", "Interested leads", "a call, a free test or more"]].map(([k, t, h]) => `<button data-metric="${k}" aria-pressed="${c.metric === k}"><b>${t}</b> <span class="small muted">${h}</span></button>`).join("")}</div></div>` : ""}
    <label class="check"><input type="checkbox" class="chk" data-followups ${c.followups_on ? "checked" : ""}> Follow-ups on day 4 and day 10 if they don't reply, the same for every version. Each waits for your review.</label>`;
}

let pvTimer;
function preview() {
  clearTimeout(pvTimer);
  pvTimer = setTimeout(async () => {
    const v = B.c.versions[B.ver], el = $("[data-preview]");
    if (!v || !el) return;
    const lang = curLang(v), tx = textOf(v, lang);
    try {
      const lead = (B.aud?.preview || []).find((x) => (x.language || "fr") === lang);
      const p = await api("/api/templates/preview", { method: "POST", body: { subject: tx.subject, body: tx.body, segment: B.c.kind, asset_ids: v.asset_ids, lead_id: lead?.id ?? null, language: lang } });
      if (!p.lead) { el.innerHTML = `<p class="small muted" style="padding:12px 16px;margin:0">No lead speaking ${esc(LANG[lang] || lang)} to fill it in for.</p>`; return; }
      const imgs = (B.dossiers || []).flatMap((d) => d.images);
      el.innerHTML = `<div class="pv-h"><span>To</span><b>${esc(p.to)}</b><span>Subject</span><b>${esc(p.subject)}</b></div><div class="pv-b">${esc(p.body)}<div class="optout">--\n${esc(p.footer)}</div></div>
        ${v.asset_ids.length ? `<div class="pv-a">${v.asset_ids.map((id) => imgs.find((x) => x.id === id)).filter(Boolean).map((x) => `<img src="${x.thumb}" alt="${esc(x.name)}">`).join("")}</div>` : ""}`;
    } catch (e) { el.innerHTML = `<p class="small muted" style="padding:12px 16px;margin:0">${esc(e.message)}</p>`; }
  }, 250);
}

function wireEmails() {
  const el = $("[data-step]");
  const subj = $("[data-v=subject]", el), body = $("[data-v=body]", el);
  wireAiBar($(".ai-bar", el), {
    subject: subj, body,
    extra: () => ({ segment: B.c.kind, step: 0 }),
  });
  let last = body;
  subj.addEventListener("focus", () => (last = subj));
  body.addEventListener("focus", () => (last = body));
  el.addEventListener("mousedown", (e) => { if (e.target.closest("[data-ins]")) { e.preventDefault(); last.focus(); } });
  preview();
}

function stepSending() {
  const c = B.c;
  const today = new Date().toISOString().slice(0, 10);
  return `<h2>When does it go out?</h2>
    <p class="muted">${B.manual ? "From your mailbox, when you press Send. The daily number keeps your new address healthy: start low and raise it week after week." : "From your mailbox, a few minutes apart, Monday to Friday 9:00–18:00, so each email stays personal and your address stays healthy."}</p>
    <div class="form-grid">
      <label>Emails a day<input class="input" type="number" min="1" max="60" data-f="daily_cap" value="${c.daily_cap}"></label>
      <label>Start<input class="input" type="date" min="${today}" data-f="start_on" value="${esc(c.start_on || "")}"><span class="small muted">empty: as soon as you start it</span></label>
    </div>
    ${B.manual ? `<div class="cb-sec"><h4>Before each email goes out</h4>
      <div class="note ok">${small(I.check)} <span><b>You send each email yourself.</b> Each working morning, the day's emails (up to ${c.daily_cap}) are written and wait in Today. Read one, change it if you like, press Send, and the next one opens. Nothing leaves without your click. <a href="#settings">Change in Settings</a></span></div></div>`
    : `<div class="cb-sec"><h4>Before each email goes out</h4><div class="cb-opts">
      <button data-review="daily" aria-pressed="${c.review !== "auto"}"><b>I review each morning's batch</b><span class="small muted" style="display:block">The day's emails wait in Today. Skim, edit if you like, approve them in one click.</span></button>
      <button data-review="auto" aria-pressed="${c.review === "auto"}"><b>Approved once, for the whole campaign</b><span class="small muted" style="display:block">First emails go out on their own once started. Follow-ups still wait for you.</span></button></div></div>`}</div></div>
    ${c.versions.length > 1 ? `<div class="note ok">${small(I.check)} <span>When one version is clearly better, the campaign page offers to send only that one to the leads left. Nothing switches without your click.</span></div>` : ""}`;
}

function problems() {
  const c = B.c, a = B.aud, out = [];
  if (!a || !a.chosen) out.push(["who", "No lead matches the filters."]);
  c.versions.forEach((v, i) => {
    if (!v.subject.trim() || !v.body.trim()) out.push(["emails", `Version ${LET[i]} needs a subject and a text.`]);
    for (const l of vLangs(v)) {
      const tx = textOf(v, l);
      const unknown = [...`${tx.subject} ${tx.body}`.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).filter((k) => !["prenom", "agence", "ville", "lean", "n", "site", "nb_images", "signature"].includes(k));
      if (unknown.length) out.push(["emails", `Version ${LET[i]}${l !== mainLang(v) ? ` in ${LANG[l]}` : ""}: {${unknown[0]}} isn't a known placeholder.`]);
      if (l !== mainLang(v) && !written(v, l) && (tx.subject.trim() || tx.body.trim())) out.push(["emails", `Version ${LET[i]} in ${LANG[l]} needs both a subject and a text.`, true]);
    }
  });
  const left = Object.values(a?.left_out || {}).reduce((x, y) => x + y, 0);
  if (left) out.push(["emails", `${left} matching leads left out: no text in their language yet.`, true]);
  if (c.versions.length > 1 && a && a.chosen / c.versions.length < 20) out.push(["who", `Only ${Math.floor(a.chosen / c.versions.length)} leads per version: too few to tell versions apart. Aim for 40 or more each.`, true]);
  return out;
}

function drawSummary() {
  const el = $("[data-summary]");
  if (!el) return;
  const c = B.c, a = B.aud, f = c.filters;
  const chosen = a ? a.chosen : c.size;
  const filt = activeFilters(f).map(([label]) => label);
  const nHand = a ? a.chosen - a.from_filters : (c.included || []).length;
  if (nHand) filt.push(`plus ${nHand} added by hand`);
  const nImg = new Set(c.versions.flatMap((v) => v.asset_ids)).size;
  const probs = problems();
  const blocking = probs.filter((p) => !p[2]);
  el.innerHTML = `<section class="cb-sum"><h4>This campaign</h4>
    <div class="cb-big2"><div><b>${chosen.toLocaleString("en")}</b><span>leads</span></div><div><b>${workdays(chosen, c.daily_cap)}</b><span>working days</span></div></div>
    <dl><dt>Who</dt><dd>${esc(KINDS_PL[c.kind])}${filt.length ? `: ${esc(filt.join(" · "))}` : ""}</dd>
      <dt>Order</dt><dd>${esc({ small: "Small and new first", score: "Best score first", random: "At random" }[c.order])}</dd>
      <dt>Emails</dt><dd>${c.versions.length > 1 ? `${c.versions.length} versions, ${Math.round(100 / c.versions.length)} % each` : "One version"}${nImg ? ` · ${plural(nImg, "image")}` : ""}${c.followups_on ? " · follow-ups" : ""} · in ${esc(langList(coveredLangs(c.versions)))}</dd>
      <dt>Sending</dt><dd>${c.daily_cap} a day${c.start_on ? `, from ${esc(new Date(c.start_on).toLocaleDateString("en-GB", { day: "numeric", month: "short" }))}` : ""} · ${B.manual !== false ? "you send each one" : c.review === "auto" ? "approved once" : "you approve each morning"}</dd></dl>
    ${probs.map(([step, t, soft]) => `<button class="cb-prob ${soft ? "soft" : ""}" data-go="${step}">${esc(t)}</button>`).join("")}
    <button class="btn cb-go" data-start ${blocking.length ? "disabled" : ""}>Start campaign</button></section>
    <section class="card small"><b>Not sure yet?</b> It's saved as a draft as you go. Come back to it from Campaigns.</section>`;
}

function confirmStart() {
  const c = B.c, a = B.aud, probs = problems().filter((p) => !p[2]);
  if (probs.length) { B.step = probs[0][0]; drawSteps(); drawStep(); toast(probs[0][1], { err: true }); return; }
  const startsLater = c.start_on && c.start_on > new Date().toISOString().slice(0, 10);
  const sheet = openModal(`<div class="sheet-h"><h2>Start “${esc(c.name)}”?</h2><button class="btn ghost sm" data-close aria-label="Close">${small(I.x)}</button></div>
    <div class="sheet-b"><ul class="cb-check">
      <li>${small(I.check)} <span><b>${plural(a.chosen, "lead")}</b> get ${c.versions.length > 1 ? `one of <b>${c.versions.length} versions</b>, at random` : "the email"}.</span></li>
      <li>${small(I.check)} <span><b>${c.daily_cap} a day</b>, ${startsLater ? `from ${esc(c.start_on)}` : "from today, inside sending hours"}: about ${plural(workdays(a.chosen, c.daily_cap), "working day")}.</span></li>
      <li>${small(I.check)} <span>${c.review === "auto" ? "First emails go out <b>without asking you</b>." : "Each morning's emails <b>wait for you in Today</b> before anything goes out."}</span></li>
      <li>${small(I.check)} <span>The list of leads is fixed when you start. You can add more later, pause, or stop.</span></li></ul>
      <div class="row"><span class="grow"></span><button class="btn" data-close>Not yet</button><button class="btn primary" data-go-start>Start</button></div></div>`, { narrow: true });
  sheet.onclick = async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.dataset.close !== undefined) return closeModal();
    if (t.dataset.goStart !== undefined) {
      await busy(t, async () => {
        await flushSave();
        const r = await api(`/api/campaigns/${c.id}/start`, { method: "POST" });
        closeModal();
        confetti();
        toast(r.written ? `Started. ${plural(r.written, "email")} written for today: ${B.manual ? "send them from Today" : "approve them in Today"}.` : `Started with ${plural(r.leads, "lead")}. The first emails are written ${startsLater ? `on ${c.start_on}` : "at the next sending hour"}.`, { ms: 6000 });
        B.c = null;
        changed();
      });
    }
  };
}

function onBuilderInput(e) {
  const t = e.target, c = B.c;
  if (!c || c.status !== "draft") return;
  const v = c.versions[B.ver];
  if (t.dataset.f === "name") { c.name = t.value; save(); return; }
  if (t.dataset.f === "daily_cap") { c.daily_cap = Math.max(1, Math.min(60, +t.value || 1)); save(); drawSteps(); return; }
  if (t.dataset.f === "start_on") { c.start_on = t.value || null; save(); drawSummary(); return; }
  if (t.dataset.sig) {
    B.sigs[t.dataset.sig] = t.value;
    clearTimeout(B.sigTimer);
    B.sigTimer = setTimeout(() => { const { fr, ...rest } = B.sigs; api("/api/settings", { method: "PUT", body: { signatures: rest } }).catch(fail); }, 500);
    preview();
    return;
  }
  if (t.matches("[data-add-lang]") && v && t.value) {
    v.translations = { ...(v.translations || {}), [t.value]: { subject: "", body: "" } };
    B.lang = t.value;
    save(); drawStep(); return;
  }
  if (t.matches("[data-main-lang]") && v) {
    const old = mainLang(v), now = t.value, tr = { ...(v.translations || {}) };
    if (tr[now]) { tr[old] = { subject: v.subject, body: v.body }; v.subject = tr[now].subject; v.body = tr[now].body; delete tr[now]; }
    v.language = now; v.translations = tr; B.lang = now;
    save(); drawStep(); refreshAudience(); return;
  }
  if (t.dataset.v && v) {
    const lang = curLang(v), before = coveredLangs(c.versions).join();
    if (t.dataset.v === "name" || lang === mainLang(v)) v[t.dataset.v] = t.value;
    else v.translations[lang] = { ...textOf(v, lang), [t.dataset.v]: t.value };
    save();
    if (coveredLangs(c.versions).join() !== before) { refreshAudience(); drawLangTabs(); }
    if (t.dataset.v !== "name") { preview(); drawSummary(); }
    if (t.dataset.v === "body" && usesLean(c.versions) !== B.lean) { B.lean = usesLean(c.versions); refreshAudience(); }
    return;
  }
  if (t.dataset.ftext) {
    const v = t.value.trim();
    c.filters[t.dataset.ftext] = v || undefined;
    save();
    clearTimeout(B.textTimer);
    B.textTimer = setTimeout(refreshAudience, 300);
    return;
  }
  if (t.matches("[data-lookup]")) {
    B.lookQ = t.value;
    clearTimeout(B.lookTimer);
    B.lookTimer = setTimeout(async () => {
      const q = B.lookQ.trim();
      B.look = q.length >= 2 ? (await api(`/api/campaigns/lookup?q=${encodeURIComponent(q)}&campaign_id=${c.id}`)).items : [];
      if (B.step === "who") drawStep();
    }, 220);
    return;
  }
  if (t.matches("[data-size]")) {
    c.size = Math.max(1, +t.value || 1);
    for (const o of document.querySelectorAll("[data-size]")) if (o !== t) o.value = c.size;
    save();
    refreshAudience();
  }
}

async function onBuilderClick(e) {
  const t = e.target.closest("button, label.cb-row");
  if (!t || !B.c) return;
  const c = B.c, d = t.dataset, f = c.filters;
  if (t.matches("label.cb-row")) return;
  if (d.go) { await flushSave(); B.step = d.go; drawSteps(); drawStep(); $("[data-step]")?.scrollIntoView({ block: "start", behavior: "smooth" }); return; }
  if (d.start !== undefined) return confirmStart();
  if (d.delete !== undefined) {
    if (d.confirm !== "1") { d.confirm = "1"; t.textContent = "Click again to delete"; return; }
    await api(`/api/campaigns/${c.id}`, { method: "DELETE" });
    B.c = null;
    location.hash = "campaigns";
    return;
  }
  const toggleIn = (key, val) => { const s = new Set(f[key] || []); s.has(val) ? s.delete(val) : s.add(val); f[key] = s.size ? [...s] : undefined; };
  const WHO = ["kind", "dep", "order", "unexclude", "fb", "fset", "flist", "ftag", "flab", "fdel", "fclear", "preset", "hand", "unhand", "moreList", "allList"];
  if (d.kind) { c.kind = d.kind; c.filters = {}; B.depOptions = []; c.excluded = []; }
  else if (d.preset) { Object.assign(f, structuredClone((PRESETS[c.kind] || [])[+d.preset][1])); }
  else if (d.fb) f[d.fb] = f[d.fb] ? undefined : true;
  else if (d.fset) { const v = isNaN(+d.fval) ? d.fval : +d.fval; f[d.fset] = String(f[d.fset] ?? "") === String(d.fval) ? undefined : v; }
  else if (d.flist) toggleIn(d.flist, d.fval);
  else if (d.ftag) {
    const t0 = d.ftag, inAny = (f.tags_any || []).includes(t0), inNone = (f.tags_none || []).includes(t0);
    if (inAny) { toggleIn("tags_any", t0); toggleIn("tags_none", t0); } else if (inNone) toggleIn("tags_none", t0); else toggleIn("tags_any", t0);
  } else if (d.flab) {
    const id = +d.flab, inAny = (f.labels_any || []).includes(id), inNone = (f.labels_none || []).includes(id);
    const tog = (key) => { const s = new Set(f[key] || []); s.has(id) ? s.delete(id) : s.add(id); f[key] = s.size ? [...s] : undefined; };
    if (inAny) { tog("labels_any"); tog("labels_none"); } else if (inNone) tog("labels_none"); else tog("labels_any");
  } else if (d.fdel) {
    if (d.fval !== undefined && Array.isArray(f[d.fdel])) toggleIn(d.fdel, d.fval); else f[d.fdel] = undefined;
  } else if (d.fclear !== undefined) c.filters = {};
  else if (d.dep) toggleIn("deps", d.dep);
  else if (d.order) c.order = d.order;
  else if (d.unexclude) c.excluded = c.excluded.filter((x) => x !== +d.unexclude);
  else if (d.hand) { c.included = [...new Set([...c.included, +d.hand])]; c.excluded = c.excluded.filter((x) => x !== +d.hand); toast("Added. They'll be written to first."); }
  else if (d.unhand) c.included = c.included.filter((x) => x !== +d.unhand);
  else if (d.moreList !== undefined) { B.shown += 50; refreshAudience(); return; }
  else if (d.allList !== undefined) { B.shown = 100000; refreshAudience(); return; }
  else if (d.peek) { openLead(+d.peek); return; }
  else if (d.ver) { B.ver = +d.ver; drawStep(); return; }
  else if (d.langTab) { B.lang = d.langTab; drawStep(); return; }
  else if (d.delLang) {
    if (d.confirm !== "1") { d.confirm = "1"; t.textContent = "Click again to remove"; return; }
    const v = c.versions[B.ver], tr = { ...(v.translations || {}) };
    delete tr[d.delLang];
    v.translations = tr; B.lang = mainLang(v);
    save(); drawStep(); refreshAudience(); return;
  }
  else if (d.addVer !== undefined) {
    const cur = c.versions[B.ver];
    c.versions.push({ id: null, name: "", subject: cur.subject, body: cur.body, asset_ids: [...cur.asset_ids], attach_photo: cur.attach_photo, language: mainLang(cur), translations: structuredClone(cur.translations || {}) });
    B.ver = c.versions.length - 1;
    if (c.order !== "random" && c.versions.length === 2) toast("Tip: “At random” in step 1 makes the comparison fairest.");
    save(); drawSteps(); drawStep(); refreshAudience(); return;
  } else if (d.delVer !== undefined) {
    if (d.confirm !== "1") { d.confirm = "1"; t.textContent = "Click again to remove"; return; }
    c.versions.splice(B.ver, 1);
    B.ver = 0;
    save(); drawSteps(); drawStep(); refreshAudience(); return;
  } else if (d.img) {
    const v = c.versions[B.ver], id = +d.img;
    v.asset_ids = v.asset_ids.includes(id) ? v.asset_ids.filter((x) => x !== id) : [...v.asset_ids, id];
    save(); drawStep(); drawSummary(); return;
  } else if (d.ins) {
    const ta = document.activeElement?.matches?.("[data-v=subject], [data-v=body]") ? document.activeElement : $("[data-v=body]");
    const p = ta.selectionStart ?? ta.value.length;
    ta.value = ta.value.slice(0, p) + d.ins + ta.value.slice(ta.selectionEnd ?? p);
    ta.selectionStart = ta.selectionEnd = p + d.ins.length;
    ta.dispatchEvent(new Event("input", { bubbles: true }));
    return;
  } else if (d.metric) c.metric = d.metric;
  else if (d.review) c.review = d.review;
  else if (d.test !== undefined) {
    await busy(t, async () => {
      await flushSave();
      const r = await api(`/api/campaigns/${c.id}/test`, { method: "POST", body: { language: curLang(c.versions[B.ver]) } });
      toast(`${plural(r.sent, "test email")} sent to ${r.to}, filled in for ${r.lead}.`, { ms: 6000 });
    });
    return;
  } else return;
  save();
  if (WHO.some((k) => d[k] !== undefined)) { drawStep(); refreshAudience(); }
  else { drawSteps(); drawStep(); }
}

function onBuilderChange(e) {
  const t = e.target, c = B.c;
  if (!c || c.status !== "draft") return;
  if (t.matches("[data-km]")) { c.filters.max_km = t.value ? +t.value : undefined; save(); refreshAudience(); }
  else if (t.matches("[data-dep-add]") && t.value) { c.filters.deps = [...new Set([...(c.filters.deps || []), t.value])]; save(); drawStep(); refreshAudience(); }
  else if (t.matches("[data-fnet]") && t.value) { c.filters.networks = [...new Set([...(c.filters.networks || []), t.value])]; save(); drawStep(); refreshAudience(); }
  else if (t.matches("[data-keep]")) {
    const id = +t.dataset.keep;
    // Unticking someone added by hand takes them off the hand-picked list; anyone else is left out.
    if (t.dataset.byhand) c.included = c.included.filter((x) => x !== id);
    else c.excluded = [...new Set([...c.excluded, id])];
    save(); refreshAudience();
  }
  else if (t.matches("[data-followups]")) { c.followups_on = t.checked; save(); drawSummary(); }
}

// One set of listeners on the canvas; they act only while a draft is shown.
let wired = false;
function wire() {
  if (wired) return;
  wired = true;
  C().addEventListener("input", (e) => { if ($(".cb", C())) onBuilderInput(e); });
  C().addEventListener("change", (e) => { if ($(".cb", C())) onBuilderChange(e); });
  C().addEventListener("click", (e) => { if ($(".cb", C())) onBuilderClick(e).catch(fail); });
}

// --- results (started) ----------------------------------------------------------------------

const STATE = {
  not_sent: ["Not sent yet", ""], to_review: ["Written, not sent", "todo"], approved: ["Approved, going out", ""],
  waiting: ["Sent, no reply yet", ""], replied: ["Replied", "ok"], interested: ["Interested", "ok"],
  stopped: ["Asked to stop", "bad"], failed: ["Not sent (error)", "bad"], skipped: ["Skipped (contacted another way)", ""],
};
let leadFilter = "to_send";
const TO_SEND = ["not_sent", "to_review", "approved", "failed"];

/** Open this lead's email to read and send; "Send and next" goes on with the leads after it. */
async function sendFrom(c, ids, i) {
  if (i >= ids.length) { toast("That's everyone in this campaign for now."); return; }
  try {
    const item = await api(`/api/campaigns/${c.id}/leads/${ids[i]}/email`, { method: "POST" });
    openReview(null, item, { manual: true, next: () => sendFrom(c, ids, i + 1), left: ids.length - i - 1 });
  } catch (e) { fail(e); }
}

function renderResults(c) {
  const r = c.results, t = r.totals, test = r.test;
  const live = c.status === "running" || c.status === "paused";
  const toSend = r.leads.filter((l) => TO_SEND.includes(l.state)).map((l) => l.id);
  const counts = { to_send: toSend.length, waiting: t.sent - t.replied - t.stopped, replied: t.replied + t.stopped, all: t.leads };
  if (!counts[leadFilter] && leadFilter !== "all") leadFilter = counts.to_send ? "to_send" : counts.replied ? "replied" : counts.waiting ? "waiting" : "all";
  const show = r.leads.filter((l) => leadFilter === "all" || (leadFilter === "replied" ? ["replied", "interested", "stopped"].includes(l.state) : leadFilter === "to_send" ? TO_SEND.includes(l.state) : l.state === leadFilter));
  const vcard = (v, i) => {
    const tv = test?.versions.find((x) => x.template_id === v.template_id);
    const lead = test && test.leader === v.letter && t.sent;
    const won = test?.winner_id === v.template_id;
    return `<article class="cr-v ${lead || won ? "lead" : ""}" style="--vc:${VCOL[i]}">
      <div class="row"><span class="vl" style="background:${VCOL[i]}">${v.letter}</span><div style="flex:1;min-width:0"><b>${esc(v.name && v.name !== v.letter ? v.name : `Version ${v.letter}`)}</b><div class="small muted" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">“${esc(v.subject)}”${v.asset_ids.length ? ` · ${plural(v.asset_ids.length, "image")}` : ""}</div></div>${won ? `<span class="pill st-customer">Winner</span>` : lead ? `<span class="pill st-pilot">Ahead</span>` : ""}</div>
      ${v.asset_ids.length ? `<div class="cr-imgs">${v.asset_ids.map((id) => `<img src="/api/assets/${id}/thumb" alt="">`).join("")}</div>` : ""}
      <div class="cr-nums"><div><b>${v.sent}</b><span>sent</span></div><div><b>${v.replied}</b><span>replied</span></div><div><b>${v.interested}</b><span>interested</span></div><div><b style="color:var(--vc)">${v.sent ? pct((test?.metric === "interested" ? v.interested : v.replied) / v.sent) : "–"}</b><span>${test?.metric === "interested" ? "interest" : "reply"} rate</span></div></div>
      ${tv && tv.sent ? `<div class="cr-bar"><span class="small muted">Likely ${test.metric === "interested" ? "interest" : "reply"} rate: ${pct(tv.low)} to ${pct(tv.high)}</span><div class="rng"><span style="left:${tv.low * 100}%;width:${Math.max(1, (tv.high - tv.low) * 100)}%"></span><i style="left:${(tv.rate || 0) * 100}%"></i></div></div>
        <div class="cr-bar"><span class="small muted">Chance of being the best: ${pct(tv.chance_best)}</span><div class="pb"><span style="width:${tv.chance_best * 100}%"></span></div></div>` : `<p class="small muted" style="margin:0">${v.planned} planned · ${v.sent ? `${v.sent} sent so far` : "nothing sent yet"}</p>`}
    </article>`;
  };
  const maxDay = Math.max(1, ...r.days.map((d) => d.sent));
  C().innerHTML = `<div class="view wide cr">
    <div class="topline"><div style="flex:1;min-width:280px"><p class="small muted" style="margin:0"><a href="#campaigns">Campaigns</a> / ${esc(KINDS_PL[c.kind])}</p>
      <div class="row"><h1>${esc(c.name)}</h1>${statusPill(c.status)}</div>
      <div class="sub">${c.started_at ? `Started ${esc(when(c.started_at))}` : ""} · ${c.daily_cap} a day · ${B.manual !== false ? "you send each one" : c.review === "auto" ? "approved once" : "you approve each morning"} · ${t.sent} of ${t.leads} emailed${r.days_left && live ? ` · about ${plural(r.days_left, "working day")} to go` : ""}</div></div>
      <div class="row">${toSend.length && c.status !== "draft" ? `<button class="btn primary" data-send-next>${small(I.send, 15)} Send the next email <span class="small" style="opacity:.8">(${toSend.length} left)</span></button>` : ""}
        ${c.status === "running" ? `<button class="btn" data-pause>Pause</button>` : c.status === "paused" ? `<button class="btn primary" data-resume>Resume</button>` : ""}
        ${c.status !== "draft" ? `<button class="btn" data-more>Add leads</button>` : ""}<button class="btn" data-dup title="A new draft with the same settings and emails">Duplicate</button>
        ${live ? `<button class="btn" data-settings>Settings</button>` : ""}</div></div>
    ${c.status === "paused" ? `<div class="note warn" style="margin-bottom:12px">Paused: no new emails are written. Approved first emails went back to review.</div>` : ""}
    ${c.start_on && c.start_on > new Date().toISOString().slice(0, 10) ? `<div class="note info" style="margin-bottom:12px">Starts on ${esc(c.start_on)}: the first emails are written that morning.</div>` : ""}
    <div class="cp-bar big" role="img" aria-label="${t.replied} replied, ${t.sent - t.replied} waiting, ${t.to_review + t.approved} written, ${t.not_sent} not written yet"><span style="flex:${t.replied};background:var(--warn)"></span><span style="flex:${t.sent - t.replied};background:var(--emerald)"></span><span style="flex:${t.to_review + t.approved};background:var(--mint)"></span><span style="flex:${t.not_sent}"></span></div>
    <div class="cr-kpi"><div><b>${t.sent}</b><span>emailed, of ${t.leads}</span></div><div><b>${t.replied}${t.sent ? ` <small>${pct(t.replied / t.sent)}</small>` : ""}</b><span>replied</span></div><div><b>${t.interested}</b><span>interested</span></div><div><b>${t.pilots}</b><span>free tests</span></div><div><b>${t.stopped}</b><span>asked to stop</span></div>
      ${t.to_review ? `<a href="#today" class="todo"><b>${t.to_review}</b><span>to review in Today →</span></a>` : ""}</div>
    ${r.versions.length > 1 ? `<section class="card cr-test"><div class="row"><h2>A/B test: which email works better?</h2><span class="small muted">counted on ${test?.metric === "interested" ? "interested leads" : "replies"} · ${Math.round(100 / r.versions.length)} % of leads each</span></div>
        ${test?.suggest ? `<div class="cr-verdict win"><span><b>Version ${esc(test.suggest.letter)} is clearly better.</b> ${esc(test.verdict.text)} ${plural(test.suggest.unsent, "lead")} haven't had their email yet.</span><button class="btn primary" data-winner="${test.suggest.template_id}" data-letter="${esc(test.suggest.letter)}" data-unsent="${test.suggest.unsent}">Send ${esc(test.suggest.letter)} to the ${test.suggest.unsent} left</button><button class="btn" data-keep-testing>Keep testing</button></div>`
          : test?.status === "stopped" ? `<div class="cr-verdict">${small(I.check)} <span>Test ended: ${esc(r.versions.find((v) => v.template_id === test.winner_id)?.letter || "?")} won, and the leads left get it.</span></div>`
          : `<div class="cr-verdict ${test?.verdict.state || ""}"><span>${esc(test?.verdict.text || "")}</span>${live && t.sent >= 10 ? `<button class="btn sm" data-pick>Pick a winner myself</button>` : ""}</div>`}
        <div class="cr-vs">${r.versions.map(vcard).join("")}</div>
        <p class="small muted" style="margin:0">“Likely rate” is where each version's true rate probably sits, given how few emails have gone out; the ranges narrow as more go. The verdict waits until each version has about 20 emails.</p></section>`
      : `<section class="card cr-test"><div class="cr-vs one">${r.versions.map(vcard).join("")}</div></section>`}
    ${Object.keys(r.languages || {}).length > 1 ? `<section class="card cr-langs"><h2>By language</h2><div class="cr-lg">${Object.entries(r.languages).map(([l, x]) => `<div><b>${esc(LANG[l] || l)}</b><span class="small muted">${x.leads} leads · ${x.sent} sent · ${x.replied} replied${x.sent ? ` (${pct(x.replied / x.sent)})` : ""}</span></div>`).join("")}</div></section>` : ""}
    <div class="cr-low">
      <section class="card"><h2>Day by day</h2>${r.days.length ? `<div class="cr-days">${r.days.map((d) => `<div title="${d.day}: ${d.sent} sent, ${d.replies} replies"><span class="b" style="height:${(d.sent / maxDay) * 100}%"><span class="r" style="height:${d.sent ? Math.min(100, (d.replies / d.sent) * 100) : 0}%"></span></span><span class="small muted">${new Date(d.day).toLocaleDateString("en-GB", { weekday: "short" }).slice(0, 2)}</span></div>`).join("")}</div>
        <p class="small muted" style="margin:6px 0 0"><span class="sw" style="background:var(--emerald)"></span> sent · <span class="sw" style="background:var(--warn)"></span> replies</p>` : `<p class="small muted">Nothing sent yet.</p>`}</section>
      <section class="card"><div class="row"><h2 style="flex:1">The ${t.leads} leads</h2><div class="seg" role="group" aria-label="Show">${[["to_send", "To send"], ["waiting", "Sent"], ["replied", "Replied"], ["all", "All"]].map(([k, l]) => `<button data-lf="${k}" aria-pressed="${leadFilter === k}">${l} <span class="mono small">${counts[k]}</span></button>`).join("")}</div></div>
        ${show.length ? `<div class="cr-leads">${show.slice(0, 200).map((l) => `<div class="cr-lead ${TO_SEND.includes(l.state) ? "can-send" : ""}" data-open="${l.id}" role="button" tabindex="0"><span class="vl sm" style="background:${VCOL[LET.indexOf(l.letter)] || "var(--dim)"}">${esc(l.letter)}</span><span class="nm"><b>${esc(l.person || l.name)}</b> <span class="muted small">${esc(l.city || "")}</span>${l.reply ? `<span class="small muted q">“${esc(l.reply)}”</span>` : ""}</span><span class="chip-s ${STATE[l.state]?.[1] || ""}">${STATE[l.state]?.[0] || l.state}</span><span class="small muted">${esc(l.at ? when(l.at) : "")}</span>${TO_SEND.includes(l.state) && c.status !== "draft" ? `<button class="btn sm primary" data-send-lead="${l.id}">${small(I.send, 13)} Send</button>` : `<span></span>`}</div>`).join("")}</div>` : `<p class="small muted">Nobody here.</p>`}</section>
    </div></div>`;
  C().onclick = async (e) => {
    const b = e.target.closest("button, [data-open]");
    if (!b) return;
    const d = b.dataset;
    try {
      if (d.sendLead) { const i = toSend.indexOf(+d.sendLead); await sendFrom(c, toSend, Math.max(0, i)); }
      else if (d.sendNext !== undefined) await sendFrom(c, toSend, 0);
      else if (d.lf) { leadFilter = d.lf; renderResults(c); }
      else if (d.open) openLead(+d.open);
      else if (d.pause !== undefined || d.resume !== undefined) await busy(b, async () => { await api(`/api/campaigns/${c.id}/status`, { method: "POST", body: { status: d.pause !== undefined ? "paused" : "running" } }); toast(d.pause !== undefined ? "Paused. Nothing new is written until you resume." : "Resumed."); changed(); });
      else if (d.dup !== undefined) await busy(b, async () => { const x = await api(`/api/campaigns/${c.id}/duplicate`, { method: "POST" }); location.hash = `campaign/${x.id}`; });
      else if (d.more !== undefined) addLeads(c);
      else if (d.settings !== undefined) liveSettings(c);
      else if (d.winner) {
        if (d.confirm !== "1") { d.confirm = "1"; b.textContent = `Confirm: everyone left gets ${d.letter}`; return; }
        await busy(b, async () => { const x = await api(`/api/campaigns/${c.id}/winner`, { method: "POST", body: { template_id: +d.winner } }); toast(`Done: the ${d.unsent} leads left get version ${d.letter}${x.moved ? "" : ""}.`); confetti(); changed(); });
      } else if (d.keepTesting !== undefined) { b.closest(".cr-verdict").hidden = true; toast("Keep going: the banner comes back on the next visit while it's still clear."); }
      else if (d.pick !== undefined) pickWinner(c);
    } catch (err) { fail(err); }
  };
}

function addLeads(c) {
  const sheet = openModal(`<div class="sheet-h"><h2>Add leads to “${esc(c.name)}”</h2><button class="btn ghost sm" data-close aria-label="Close">${small(I.x)}</button></div>
    <form class="sheet-b"><label>The next ones matching its filters: how many<input class="input" type="number" name="n" min="1" max="1000" value="${Math.max(10, c.daily_cap * 5)}"></label>
    <p class="small muted" style="margin:0">In the same order.${c.results.test?.status === "stopped" ? " They get the winning version." : " They get the versions in equal shares."}</p>
    <div class="row"><span class="grow"></span><button class="btn" type="button" data-close>Cancel</button><button class="btn primary">Add them</button></div></form>
    <div class="sheet-b" style="border-top:1px solid var(--line)"><label for="add-q"><b>Or someone in particular</b></label>
      <input class="input" id="add-q" placeholder="Name, town, website or postcode" autocomplete="off" autofocus>
      <div data-found class="cb-list cb-look" hidden></div></div>`, { narrow: true });
  let timer;
  const find = async () => {
    const q = $("#add-q", sheet).value.trim(), box = $("[data-found]", sheet);
    const items = q.length >= 2 ? (await api(`/api/campaigns/lookup?q=${encodeURIComponent(q)}&campaign_id=${c.id}`)).items : [];
    box.hidden = !items.length;
    box.innerHTML = items.map((l) => `<div class="cb-row" style="grid-template-columns:minmax(0,1.4fr) minmax(0,1fr) auto"><span class="nm"><b>${esc(l.person || l.name)}</b>${l.person ? ` <span class="muted">· ${esc(l.name)}</span>` : ""}<br><span class="small muted">${esc(l.city || "")} · ${esc(KINDS_PL[l.kind] || l.kind)}</span></span>
      <span class="small muted">${l.why ? `Can't: ${esc(l.why)}` : ""}</span>${l.why ? "<span></span>" : `<button class="btn sm" type="button" data-pick="${l.id}">Add</button>`}</div>`).join("");
  };
  $("#add-q", sheet).addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => find().catch(fail), 220); });
  sheet.onclick = async (e) => {
    if (e.target.closest("[data-close]")) return closeModal();
    const b = e.target.closest("[data-pick]");
    if (!b) return;
    await busy(b, async () => {
      const r = await api(`/api/campaigns/${c.id}/leads`, { method: "POST", body: { lead_ids: [+b.dataset.pick] } });
      if (r.added) { b.replaceWith(Object.assign(document.createElement("span"), { className: "small", textContent: "added", style: "color:var(--ink)" })); changed(); }
      else toast(r.notes[0] || "Not added.");
    });
  };
  sheet.onsubmit = async (e) => {
    e.preventDefault();
    await busy(e.target.querySelector(".primary"), async () => {
      const r = await api(`/api/campaigns/${c.id}/more`, { method: "POST", body: { n: +e.target.n.value } });
      closeModal();
      toast(r.added ? `${plural(r.added, "lead")} added.` : "Nobody else matches the filters.");
      changed();
    });
  };
}

function liveSettings(c) {
  const sheet = openModal(`<div class="sheet-h"><h2>Settings</h2><button class="btn ghost sm" data-close aria-label="Close">${small(I.x)}</button></div>
    <form class="sheet-b"><label>Name<input class="input" name="name" value="${esc(c.name)}"></label>
    <label>Emails a day<input class="input" type="number" name="daily_cap" min="1" max="60" value="${c.daily_cap}"></label>
    <label class="check"><input type="checkbox" class="chk" name="auto" ${c.review === "auto" ? "checked" : ""}> Approve first emails automatically (no morning review)</label>
    <label class="check"><input type="checkbox" class="chk" name="fu" ${c.followups_on ? "checked" : ""}> Write follow-ups after each first email</label>
    <p class="small muted" style="margin:0">Who it reaches and its emails are fixed once started. To change them, duplicate it.</p>
    <div class="row"><span class="grow"></span><button class="btn" type="button" data-close>Cancel</button><button class="btn primary">Save</button></div></form>`, { narrow: true });
  sheet.onclick = (e) => { if (e.target.closest("[data-close]")) closeModal(); };
  sheet.onsubmit = async (e) => {
    e.preventDefault();
    const f = e.target;
    await busy(f.querySelector(".primary"), async () => {
      await api(`/api/campaigns/${c.id}`, { method: "PUT", body: { name: f.name.value, daily_cap: +f.daily_cap.value, review: f.auto.checked ? "auto" : "daily", followups_on: f.fu.checked } });
      closeModal();
      changed();
    });
  };
}

function pickWinner(c) {
  const vs = c.results.versions;
  const sheet = openModal(`<div class="sheet-h"><h2>Pick the winner</h2><button class="btn ghost sm" data-close aria-label="Close">${small(I.x)}</button></div>
    <div class="sheet-b"><p class="muted" style="margin:0">Everyone not emailed yet gets the version you pick. The test stops; its results stay here.</p>
    ${vs.map((v) => `<button class="btn" data-w="${v.template_id}" style="justify-content:flex-start">Version ${esc(v.letter)} · ${esc(v.name)} · ${v.replied}/${v.sent} replied</button>`).join("")}</div>`, { narrow: true });
  sheet.onclick = async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    if (b.dataset.close !== undefined) return closeModal();
    if (b.dataset.w) await busy(b, async () => { await api(`/api/campaigns/${c.id}/winner`, { method: "POST", body: { template_id: +b.dataset.w } }); closeModal(); toast("Test ended. The leads left get that version."); changed(); });
  };
}

// --- dossiers ---------------------------------------------------------------------------------

let curDossier = null, pairing = null;
export const dossiersView = {
  async render() {
    const ds = (await api("/api/dossiers")).items;
    if (!ds.find((d) => d.id === curDossier)) curDossier = ds[0]?.id ?? null;
    const d = ds.find((x) => x.id === curDossier);
    C().innerHTML = `<div class="view wide">
      <div class="topline"><div><p class="small muted" style="margin:0"><a href="#campaigns">Campaigns</a> / Dossiers</p><h1>Dossiers</h1><div class="sub">Sets of photos you send again and again. In a campaign, each email version ticks the images it carries.</div></div><span class="grow"></span><button class="btn primary" data-new-dossier>${small(I.plus)} New dossier</button></div>
      <div class="ds">
        <ul class="ds-list">${ds.map((x) => `<li><button data-dossier="${x.id}" aria-current="${x.id === curDossier}">${x.images[0] ? `<img src="${x.images[0].thumb}" alt="">` : `<span class="ds-empty">empty</span>`}<span><b>${esc(x.name)}</b><span class="small muted">${plural(x.images.length, "image")}${x.used_by.length ? ` · in ${plural(x.used_by.length, "campaign")}` : ""}</span></span></button></li>`).join("") || `<li class="small muted">No dossier yet.</li>`}</ul>
        ${d ? `<section class="card ds-main">
          <div class="row"><input class="cb-name sm" data-dname value="${esc(d.name)}" aria-label="Dossier name"><span class="grow"></span>
            ${d.images.length >= 2 ? `<button class="btn" data-pair>${pairing ? "Cancel" : "Make a before/after"}</button>` : ""}<button class="btn ghost danger sm" data-archive-dossier>Remove dossier</button></div>
          ${d.used_by.length ? `<p class="small muted" style="margin:0">Used by ${esc(d.used_by.join(", "))}.</p>` : ""}
          ${pairing ? `<div class="note info">${small(I.note)} <span>${pairing.before ? "Now click the <b>retouched</b> photo." : "Click the <b>original</b> photo (before)."} The app puts them side by side with “Avant” and “Après” labels.</span></div>` : ""}
          <div class="ds-grid">${d.images.map((x) => `<figure class="ds-img ${pairing?.before === x.id ? "sel" : ""}"><button class="ds-pic" data-pic="${x.id}" title="${pairing ? "Pick" : "Open"}"><img src="${x.thumb}" alt="${esc(x.name)}" loading="lazy"></button>
              <figcaption><input class="ds-in" data-iname="${x.id}" value="${esc(x.name)}" aria-label="Image name"><span class="small muted">${x.sent ? `Sent ${x.sent}× · ${pct(x.replied / x.sent)} got a reply` : "Not sent yet"} · ${(x.size / 1e6).toFixed(1)} MB</span>
              <span class="row" style="gap:6px">${ds.length > 1 ? `<select class="select sm" data-move="${x.id}" aria-label="Move to"><option value="">Move to…</option>${ds.filter((o) => o.id !== d.id).map((o) => `<option value="${o.id}">${esc(o.name)}</option>`).join("")}</select>` : ""}<button class="btn ghost danger sm" data-del-img="${x.id}">Remove</button></span></figcaption></figure>`).join("")}</div>
          <label class="drop"><input type="file" multiple accept="image/jpeg,image/png,image/webp" data-up hidden>${small(I.arrow, 18)}<span><b>Drop photos here</b>, or click to choose. Ready-made before/after images, or the two photos to combine.</span></label>
        </section>` : `<section class="card"><p class="muted">Create a dossier, then drop your before/after photos in it.</p></section>`}
      </div></div>`;
    const main = C();
    main.onclick = async (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      const x = b.dataset;
      try {
        if (x.dossier) { curDossier = +x.dossier; pairing = null; this.render(); }
        else if (x.newDossier !== undefined) { const n = await api("/api/dossiers", { method: "POST", body: { name: "New dossier" } }); curDossier = n.id; await this.render(); $("[data-dname]")?.select(); }
        else if (x.pair !== undefined) { pairing = pairing ? null : { before: null }; this.render(); }
        else if (x.pic) {
          if (!pairing) { const img = d.images.find((i) => i.id === +x.pic); window.open(img.src, "_blank"); return; }
          if (!pairing.before) { pairing.before = +x.pic; this.render(); return; }
          if (pairing.before === +x.pic) return;
          await busy(b, async () => { await api(`/api/dossiers/${d.id}/pair`, { method: "POST", body: { before_id: pairing.before, after_id: +x.pic } }); pairing = null; toast("Before/after made and added to this dossier."); this.render(); });
        } else if (x.delImg) {
          if (x.confirm !== "1") { x.confirm = "1"; b.textContent = "Sure?"; return; }
          await api(`/api/assets/${x.delImg}`, { method: "PATCH", body: { archived: true } }); this.render();
        } else if (x.archiveDossier !== undefined) {
          if (x.confirm !== "1") { x.confirm = "1"; b.textContent = "Click again: remove it"; return; }
          await api(`/api/dossiers/${d.id}`, { method: "PATCH", body: { archived: true } }); curDossier = null; this.render();
        }
      } catch (err) { fail(err); }
    };
    main.onchange = async (e) => {
      const t = e.target;
      try {
        if (t.matches("[data-up]")) {
          for (const file of [...t.files]) {
            toast(`Adding ${file.name}…`);
            const r = await fetch(`/api/assets?name=${encodeURIComponent(file.name)}&dossier_id=${d.id}`, { method: "POST", body: file });
            if (!r.ok) fail(new Error(`${file.name}: ${(await r.json().catch(() => ({}))).detail || r.statusText}`));
          }
          this.render();
        } else if (t.matches("[data-dname]")) await api(`/api/dossiers/${d.id}`, { method: "PATCH", body: { name: t.value } });
        else if (t.matches("[data-iname]")) await api(`/api/assets/${t.dataset.iname}`, { method: "PATCH", body: { name: t.value } });
        else if (t.matches("[data-move]") && t.value) { await api(`/api/assets/${t.dataset.move}`, { method: "PATCH", body: { dossier_id: +t.value } }); this.render(); }
      } catch (err) { fail(err); }
    };
    main.ondragover = (e) => { if (e.dataTransfer?.types?.includes("Files")) { e.preventDefault(); $(".drop", main)?.classList.add("over"); } };
    main.ondrop = async (e) => {
      if (!d || !e.dataTransfer?.files?.length) return;
      e.preventDefault();
      for (const file of [...e.dataTransfer.files]) {
        const r = await fetch(`/api/assets?name=${encodeURIComponent(file.name)}&dossier_id=${d.id}`, { method: "POST", body: file });
        if (!r.ok) fail(new Error(`${file.name}: ${(await r.json().catch(() => ({}))).detail || r.statusText}`));
      }
      this.render();
    };
  },
  leave() { const m = C(); m.onchange = m.ondragover = m.ondrop = null; },
};

// The builder's AI bar needs the AI settings; fetched once.
api("/api/ai").then((ai) => (B.ai = ai)).catch(() => (B.ai = null));
wire();
