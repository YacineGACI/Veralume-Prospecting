# Veralume Prospecting

Finds, screens and contacts the first pilot customers for Veralume (AI retouching of
real-estate photos): estate agencies, independent agents and real-estate photographers.
It collects leads from free sources only, measures how much the verticals of their
listing photos lean (Veralume's own line detector, run locally), and gives you an app to
email them from your own mailbox, call them, run campaigns with A/B tests, and follow the
free tests ("pilots") through to a customer.

- [Every day](#every-day)
- [Set it up on a new computer](#set-it-up-on-a-new-computer)
- [Back up](#back-up)
- [Restore from a backup (a pg_dump and a tar of `data/`)](#restore-from-a-backup)
- [The app](#the-app)
- [Finding leads](#finding-leads)
- [Settings](#settings)
- [Rules for contacting people (France)](#rules-for-contacting-people-france)
- [Development](#development)

## Every day

```bash
cd Prospecting
docker start veralume-prospecting-db     # the database; usually already running
uv run prospect serve                    # the app on http://127.0.0.1:8011
```

Open http://127.0.0.1:8011. While the app runs it sends the emails you approved (Monday
to Friday, 9:00–18:00), reads replies every few minutes and writes each campaign's morning
batch. When it's closed, nothing is lost: replies wait in your mailbox and are read when
you start it again.

## Set it up on a new computer

You need **Python 3.12**, **[uv](https://docs.astral.sh/uv/)**, **Docker**, and git.
Screening photos also needs the Veralume repository next to this one (`../Veralume`, with
its own `.venv`); everything else works without it.

```bash
git clone git@github.com:YacineGACI/Veralume-Prospecting.git Prospecting
cd Prospecting
uv sync
cp .env.example .env                     # then fill it in (below)
```

**The database** is PostgreSQL in Docker, on port 5433, reachable from this computer
only. Create it once, with a password of your choice:

```bash
PW=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
docker run -d --name veralume-prospecting-db --restart unless-stopped \
  -e POSTGRES_USER=prospect -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB=prospecting \
  -v veralume-prospecting-pg:/var/lib/postgresql/data \
  -p 127.0.0.1:5433:5432 postgres:17-alpine
echo "PROSPECT_DATABASE_URL=postgresql+psycopg://prospect:$PW@127.0.0.1:5433/prospecting" >> .env
```

The tables are created the first time the app or any `prospect` command runs. Without
`PROSPECT_DATABASE_URL`, the app uses a SQLite file instead (`data/prospecting.db`).

**The mailbox** (to send and read replies): in `.env`, set `PROSPECT_SMTP_HOST`,
`PROSPECT_SMTP_USER`, `PROSPECT_SMTP_PASSWORD`, `PROSPECT_IMAP_HOST` and
`PROSPECT_MAIL_FROM_NAME`. With Gmail: turn on 2-Step Verification, create an *app
password* (Google account › Security › App passwords) and use it as the password;
the servers are `smtp.gmail.com` (port 465) and `imap.gmail.com`.

Then `uv run prospect serve`. To start with a copy of existing data instead of an empty
database, follow [Restore from a backup](#restore-from-a-backup).

## Back up

Everything worth keeping is in two places:

| What | Where | Back it up with |
|---|---|---|
| Leads, emails, campaigns, templates, notes, pilots… | the PostgreSQL database | `pg_dump` (one `.dump` file) |
| Image library, photos leads sent you and your retouched versions | `data/assets/`, `data/leadfiles/` | a `tar` of `data/` |
| Passwords and settings | `.env` | copy it by hand to a safe place (password manager) |

```bash
cd Prospecting
mkdir -p data/backups
docker exec veralume-prospecting-db pg_dump -U prospect -Fc prospecting > data/backups/prospecting-$(date +%F).dump
tar -czf ~/prospecting-data-$(date +%F).tar.gz --exclude=data/photos --exclude=data/cache --exclude=data/backups data
```

Copy both files somewhere off this computer (a cloud drive, a USB key). Website photos
(`data/photos/`) and the page cache (`data/cache/`) are left out on purpose: they are
downloaded again when needed, and their measurements are in the database. Neither
backup contains `.env`: keep the passwords apart.

## Restore from a backup

You have a database dump (`prospecting-YYYY-MM-DD.dump`, from `pg_dump -Fc`) and an
archive of the data folder (`prospecting-data-YYYY-MM-DD.tar.gz`). Use the real file
names in the commands below.

**1. The code.** On a new computer, clone and install as in
[Set it up on a new computer](#set-it-up-on-a-new-computer), including `cp .env.example .env`.
On this computer, stop the app first (Ctrl C where `prospect serve` runs).

**2. The data folder.** Look inside the archive first, then unpack it at the project's
root:

```bash
cd Prospecting
tar -tzf ~/prospecting-data-2026-11-05.tar.gz | head    # paths should start with data/
tar -xzf ~/prospecting-data-2026-11-05.tar.gz            # creates or fills ./data/
```

If the paths start with `assets/`, `leadfiles/`… instead (the archive was made from
inside `data/`), unpack it with `mkdir -p data && tar -xzf ~/prospecting-data-….tar.gz -C data`.
Unpacking over an existing `data/` replaces files of the same name and keeps the others.

**3. The database container.** If `docker ps -a` doesn't list `veralume-prospecting-db`,
create it and add its address to `.env` (the `docker run` and `echo` lines of
[Set it up](#set-it-up-on-a-new-computer)). Wait until it's ready:

```bash
docker start veralume-prospecting-db
until docker exec veralume-prospecting-db pg_isready -U prospect -q; do sleep 1; done
```

**4. The database.** Into an empty database (a new container):

```bash
docker exec -i veralume-prospecting-db pg_restore -U prospect -d prospecting --no-owner \
  < ~/prospecting-2026-11-05.dump
```

Over a database that already has data (this replaces it all; make a fresh backup of it
first, as in [Back up](#back-up), if you might want it again):

```bash
docker exec -i veralume-prospecting-db pg_restore -U prospect -d prospecting --no-owner \
  --clean --if-exists < ~/prospecting-2026-11-05.dump
```

If your dump is a plain SQL file (`.sql`, made with `pg_dump` without `-Fc`), use
`docker exec -i veralume-prospecting-db psql -U prospect -d prospecting < backup.sql`
instead.

**5. The passwords.** Put the mailbox settings (and any AI keys) back in `.env`: backups
don't contain them.

**6. Check and start.**

```bash
uv run prospect stats      # leads by kind, status, département: should match what you had
uv run prospect serve
```

Opening the app also brings an older backup's tables up to date: columns added since
then are created automatically.

## The app

Keyboard first: `Ctrl K` searches leads and views; `g t` Today, `g l` Leads, `g c` Call
mode, `g p` Pipeline, `g o` Pilots, `g m` Campaigns, `g i` Insights, `g s` Settings;
`j` `k` `Enter` `e` in lists.

- **Today**: goals (emails, calls, replies), streak, and one queue: replies to answer,
  written emails waiting for your review (campaign batches, follow-ups on their day, the
  email after a voicemail), call-backs and reminders, then fresh leads.
- **Leads**: Photographers / Agencies / Independent agents with filters. Select rows to add
  them to the call queue, mark them contacted, move them, or mark them do-not-contact.
- **A lead's file** (`Enter`): stage bar and actions (Email `e`, Call `c`, Snooze `s`), the
  free-test steps, and three tabs: *Conversation* (every email in and out, with their
  attachments), *Their photos*, *Notes & activity*. On the side: next step, contacts with
  where each came from, their measured lean, company details.
- **Composer**: the right French template, filled in for the lead; images to tick; follow-ups
  on day 4 and 10 written for your review. *Send*, *Send later*, or *Copy and log as sent*.
- **Call mode**: a queue of leads to ring, their script and numbers, keys `1`–`6` for the
  outcome; the next lead comes up.
- **Pipeline**: drag cards between stages. **Insights**: emails, calls and reply rates.

### Campaigns and A/B tests

A campaign is built in three steps, saved as a draft as you go:

1. **Who**: a segment, filters you click on and off (first name known, new or one-person
   business, website, leaning photos, score, distance, départements), how many to contact
   and who goes first (small and new first, best score, or at random). The count updates
   live; untick anyone in the list of the first leads to leave them out.
2. **Email**: one version, or up to four to compare (an A/B test). Each version has its own
   subject, text and images ticked from your **dossiers**, a live preview on a real lead,
   and *Send to me* for a test. Each lead gets one version at random, in equal shares.
3. **Sending**: emails a day, a start day, and whether you approve each morning's batch in
   Today or approve the whole campaign once. Follow-ups (day 4 and 10) always wait for you.

Starting it fixes the list of leads (a lead is in one running campaign at a time). The
campaign page shows emailed / replied / interested / free tests, day by day, every lead
and what happened, and for a test each version's reply rate, its likely range and its
chance of being the best. When one version is clearly better (95 %), it offers to send
that one to everyone not emailed yet; nothing switches without your click. Campaigns can
be paused, grown, or duplicated to try something else.

**Dossiers** (Campaigns › Dossiers) are named sets of images reused across campaigns. Each
image shows how often it was sent and how often that got a reply. *Make a before/after*
puts two photos side by side with “Avant” and “Après” labels. Images are attached as
`avant-apres-1.jpg`, `avant-apres-2.jpg`…; `{nb_images}` in a text says how many.

### Email

Emails go out from your own mailbox and replies are read from it (read-only: nothing is
moved or marked). Nothing goes out without your approval. Approved emails are sent one at
a time, Monday to Friday 9:00–18:00, 2 to 6 minutes apart, at most
`PROSPECT_DAILY_SEND_CAP` a day. A reply moves the lead to *replied* and cancels its
follow-ups; a "stop" reply marks it Do not contact; a bounce flags the address. Gmail
refuses emails over 25 MB: the composer shows the size and stops above 20 MB.
`uv run prospect inbox` reads the mailbox now.

Addresses that don't reach the lead are flagged and never used: a network's head office
(`contact@safti.fr`), a data-protection inbox, an email or phone shared by many leads.

### Pilots and their photos

- Photos attached to a lead's reply are saved in their file (`data/leadfiles/<lead>/`); a
  WeTransfer/Drive link in it is shown so you can fetch them.
- *Their photos* tab: download one or all (.zip), drop your retouched versions (matched
  to theirs by file name), compare side by side, and *Send them back* in one email.
- **Pilots** (`g o`): every free test, from "said yes" to the decision. The steps move on
  their own: photos received → retouching (your first upload) → sent back → feedback
  (their next reply). Set the date you promised; late ones are flagged.

### Writing with AI

In the composer, the template editor and campaign versions, type what you want ("plus
court, parle de leurs photos d'intérieur") and press Enter: the AI rewrites the email or
writes a new draft; every change can be undone. The AI sees only what the app knows about
the lead and the offer written in Settings, and is told not to invent anything. Pick it in
Settings once its key is in `.env`: Gemini or Mistral (free tiers; lead details go to that
provider), Ollama (free, on this computer) or Claude (paid).

## Finding leads

```bash
uv run prospect run --target 500     # collect → enrich → screen → score, until 500 reachable leads
uv run prospect stats                # where things stand
```

| Step | Command | What it does |
|---|---|---|
| collect | `prospect collect [registre\|osm\|photographers\|places]` | Leads from the company register (activity 68.31Z, active establishments within `radius_km`), OpenStreetMap, a slow DuckDuckGo search for real-estate photographers, and Google Places if a key is set |
| enrich | `prospect enrich [--limit N] [--retry] [--id ID]` | Finds the website, then reads up to 8 pages: emails, phones, the publisher in the legal notice, social links, activity tags, listing photo URLs |
| screen | `prospect screen [--geocalib]` | Downloads up to 4 listing photos per lead and runs Veralume's line detector (`measure_worker.py`, in `../Veralume`'s environment): the median lean of near-vertical lines. Photos with faces or fewer than 12 lines are skipped |
| score | `prospect score` | 0–100 from fit, reach, measured lean, listings and distance; the reasons are shown |

Every step resumes where it stopped (`--retry` redoes leads already done).

Free sources and their limits: the company register has every agency but no phone or
website; OpenStreetMap has a website and phone for a minority; DuckDuckGo shows a captcha
after a few queries, so the search waits between queries and stops at the first captcha
(`prospect collect photographers` resumes later); Google Places is optional and needs a
billing account (stay inside the free quota). Crawling is polite: honest user agent,
robots.txt, 1.5 s between requests to a site, pages cached for 14 days in `data/cache/`.
Portals (SeLoger, Leboncoin, Bien'ici…) are never crawled.

## Settings

In `.env` (see `.env.example`); every setting of `prospecting/config.py` can be set as
`PROSPECT_<NAME>`.

| Variable | What |
|---|---|
| `PROSPECT_DATABASE_URL` | The PostgreSQL database. Empty: SQLite in `data/` |
| `PROSPECT_SMTP_HOST`, `_PORT`, `_USER`, `_PASSWORD`, `PROSPECT_IMAP_HOST` | Your mailbox |
| `PROSPECT_MAIL_FROM_NAME` | The sender name leads see |
| `PROSPECT_DAILY_SEND_CAP` | Most emails sent a day (default 30) |
| `PROSPECT_MAIL_DRY_RUN=true` | Test mode: emails are written to `data/outbox/`, never sent |
| `PROSPECT_GEMINI_API_KEY`, `_MISTRAL_API_KEY`, `_ANTHROPIC_API_KEY`, `PROSPECT_OLLAMA_URL` | Writing with AI |
| `PROSPECT_GOOGLE_PLACES_KEY` | Optional lead source |
| `PROSPECT_RADIUS_KM` | Area around Strasbourg for the company register |

## Rules for contacting people (France)

B2B prospecting by email is allowed without prior consent when the message relates to the
person's job, the sender is identified, and every email offers a free, simple way to
object: the app adds that line to every email. Each email and phone keeps its source, so
you can always say where it came from. Honour objections at once with *Do not contact*.
Independent agents are sole traders: their data is personal data, so keep only what you
use. No automated calls.

## Development

```bash
uv run pytest                    # on a throwaway SQLite database; never touches your data
PROSPECT_TEST_DATABASE_URL=postgresql+psycopg://prospect:…@127.0.0.1:5433/prospecting_test uv run pytest
```

The second line runs the same tests on PostgreSQL, in a separate empty database
(`docker exec veralume-prospecting-db createdb -U prospect prospecting_test` once).

| Path | What |
|---|---|
| `prospecting/sources/`, `enrich.py`, `screen.py`, `scoring.py` | Finding, reading and scoring leads |
| `prospecting/db.py`, `config.py` | Tables and settings |
| `prospecting/mailer.py`, `inbox.py`, `worker.py` | Sending, reading replies, the background loop |
| `prospecting/campaigns.py`, `experiments.py` | Campaigns and A/B tests |
| `prospecting/pilot.py`, `writer.py`, `outreach.py` | Photos and pilots, writing with AI, Today and calls |
| `prospecting/api/` | The web API (FastAPI) and the app itself (`static/`, plain JavaScript, no build step) |
| `tests/` | Tests |
