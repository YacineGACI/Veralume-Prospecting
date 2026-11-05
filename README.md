# Veralume Prospecting

Finds, screens and tracks the first pilot customers for Veralume around Strasbourg:
estate agencies, independent agents and real-estate photographers. Free sources only,
and the only photo processing is Veralume's geometry measurement (local, no paid API).

```bash
uv sync
uv run prospect run --target 500     # collect → enrich → screen → score, until 500 reachable leads
uv run prospect serve                # the app on http://127.0.0.1:8011
```

## Pipeline

| Step | Command | What it does |
|---|---|---|
| collect | `prospect collect [registre\|osm\|photographers\|places]` | Leads from the company register (activity 68.31Z, active establishments within `radius_km`), OpenStreetMap (France only), a slow DuckDuckGo search for real-estate photographers, and Google Places if a key is set |
| enrich | `prospect enrich [--limit N] [--retry] [--id ID]` | Finds the website (known, SAFTI/iad advisor sitemaps, or a guessed domain confirmed by the SIREN or name + town on the site), then reads up to 8 pages: emails, phones, the publisher named in the legal notice, social links, activity tags, listing photo URLs |
| screen | `prospect screen [--geocalib]` | Downloads up to 4 listing photos per lead and runs `measure_worker.py` in Veralume's environment: Veralume's line detector gives the median lean of near-vertical lines. Photos with faces (YuNet) or fewer than 12 lines are skipped |
| score | `prospect score` | 0-100 from fit, reach, measured lean, listings and distance; the reasons are stored and shown |

Every step is resumable: it only processes leads not done yet (`--retry` redoes them).
`prospect stats` shows where things stand.

## The app

`uv run prospect serve`, then http://127.0.0.1:8011. Keyboard first: `Ctrl K` search,
`g t` / `g l` / `g c` / `g p` / `g m` / `g i` to switch views, `j` `k` `Enter` `e` in lists.

- **Today**: daily goals (emails, calls, replies), streak, and one queue: replies to answer,
  written emails waiting for your review (follow-ups on their day, campaign batches, the
  email after a voicemail), call-backs and reminders, then fresh leads of the segment you
  work on, new and small businesses first.
- **Leads**: Photographers / Agencies / Independent agents, with filters (their email, not
  contacted, contacted, measured, lean, contact to fix, département). Select rows to add
  them to the call queue, mark them contacted, move them, or mark them do-not-contact.
- **Lead file** (`Enter`): stage bar, Email `e`, Call `c`, Snooze `s`, Next stage `→`; their
  photo with the lean drawn on it, contacts with where each came from, the email thread,
  why the score, notes, the activity log, company details.
- **Composer**: the right French template for the lead, filled in; their marked photo
  attached; follow-ups on day 4 and 10 written for your review. *Send*, *Send later*, or
  *Copy and log as sent* when your mailbox isn't connected.
- **Call mode** (`g c`): a queue (call-backs, emailed without reply for 5 days, best new leads
  with a direct phone), a dial link that rings through your phone, the script with their
  numbers, and keys `1`–`6` for the outcome; the next lead comes up.
- **Pipeline**: drag cards between stages. **Campaigns**: a segment, a first email and a
  daily cap; each working morning the next batch is written for your review. Templates are
  edited there. **Insights**: emails and calls per day, reply rate by segment, lean and template.

### Email

Emails go out from your own mailbox (SMTP) and replies are read from it (IMAP, read-only):
set it up in `.env` (see `.env.example`). Nothing goes out without your approval. Approved
emails are sent one at a time, Monday to Friday 9:00–18:00, 2 to 6 minutes apart, at most
`PROSPECT_DAILY_SEND_CAP` a day. A reply moves the lead to *replied* and cancels its
follow-ups; a "stop" reply marks it Do not contact; a bounce flags the address.
`prospect inbox` reads the mailbox now.

Addresses that don't reach the lead are flagged and never used: a network's head office
(`contact@safti.fr`), a data-protection inbox, an email or phone shared by many leads.

### Writing with AI

In the composer and the template editor, type what you want ("plus court, parle de leurs
photos d'intérieur") and press Enter: the AI rewrites the email, or writes a new draft. Quick
buttons cover the usual changes, and every change can be undone. The AI sees only what the
app knows about the lead (name, town, website, measured lean, past emails) and the offer
written in Settings; it is told not to invent anything, and the app adds the opt-out footer.

Pick the AI in Settings once its key is in `.env` (see `.env.example`): Gemini or Mistral
(free tiers; lead details go to that provider), Ollama (free, runs on this machine) or
Claude (paid, `claude-opus-5-5` by default; set `PROSPECT_AI_MODEL_CLAUDE` to change it).

### Before/after images and pilots

- **Images in templates**: in Campaigns, open a template and upload before/after examples
  (or pick them from the library). Every email of that template carries them, as
  `avant-apres-1.jpg`, `avant-apres-2.jpg`… `{nb_images}` says how many. The editor shows
  the email exactly as a real lead of the segment gets it, with their name filled in.
- **Their photos**: photos attached to a lead's reply are saved in their file
  (`data/leadfiles/<lead>/`), and a WeTransfer/Drive link in it is shown so you can fetch
  them. The lead's file has a *Their photos* tab: download one or all (.zip), drop your
  retouched versions (matched to theirs by file name), compare side by side, and *Send
  them back* in one email.
- **Pilots** (`g o`): every free test, from "said yes" to the decision. The steps move on
  their own: photos received → retouching (your first upload) → sent back → feedback
  (their next reply). Set the date you promised; late ones are flagged.

Gmail refuses emails over 25 MB: the composer shows the size and stops above 20 MB.

### A/B tests

Compare two to four first emails on one segment: each new lead gets one version, and the
A/B tests view shows each version's reply rate and its chance of being the best.

## Free sources and their limits

- **Register** (recherche-entreprises.api.gouv.fr): every agency, no phone or website.
  About 40% are sole traders (network agents); few have a findable site.
- **OpenStreetMap**: website and phone for a minority of agencies.
- **DuckDuckGo**: shows a captcha after a few queries. We wait `search_interval_s`
  between queries and stop at the first captcha; `prospect collect photographers` later
  resumes where it stopped.
- **Google Places** (optional): set `PROSPECT_GOOGLE_PLACES_KEY` in `.env`. It needs a
  billing account; stay within the free monthly quota and cap usage in the console.

Crawling is polite: honest user agent, robots.txt, 1.5 s between requests to a site, pages
cached for 14 days in `data/cache/`. Portals (SeLoger, Leboncoin, Bien'ici…) are never
crawled.

## Contacting people (France)

B2B prospecting by email is allowed without prior consent when the message relates to
the person's job, the sender is identified, and every email offers a free, simple way to
object. Each email and phone keeps its source, so you can always say where it came from.
Honour objections at once with *Do not contact*. Independent agents are sole traders: their
data is personal data, so keep only what you use.

## Data

`data/prospecting.db` (SQLite: leads, photos, activities, search_queries),
`data/photos/<lead>/`, `data/cache/`. Settings: `prospecting/config.py`, overridable with
`PROSPECT_*` variables (area centre and radius, politeness, photos per lead).

```bash
uv run pytest
```
