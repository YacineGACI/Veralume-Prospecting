"""`prospect` command line: collect, enrich, screen, score, serve."""

from __future__ import annotations

from typing import Annotated

import typer
from sqlalchemy import func, select

from prospecting import enrich, languages, scoring, screen
from prospecting.areas import RINGS
from prospecting.db import Lead, SearchQuery, session
from prospecting.sources import abroad as abroad_src
from prospecting.sources import business, directory, osm, photographers, registre
from prospecting.sources.search import already_ran

app = typer.Typer(no_args_is_help=True, add_completion=False)

LimitOpt = Annotated[int | None, typer.Option(help="Process at most N leads")]
RetryOpt = Annotated[bool, typer.Option(help="Redo leads already processed")]
IdsOpt = Annotated[list[int] | None, typer.Option("--id", help="Only these lead ids")]


@app.command()
def collect(
    source: Annotated[
        str, typer.Argument(help="registre | osm | photographers | places | all")
    ] = "all",
) -> None:
    """Find leads: company register, OpenStreetMap, photographer search, Google Places."""
    if source in ("registre", "all"):
        registre.collect()
    if source in ("osm", "all"):
        osm.collect()
    if source in ("places", "all"):
        photographers.collect_places()
    if source in ("photographers", "all"):
        photographers.collect_photographers()
    scoring.rescore()


@app.command("enrich")
def enrich_cmd(limit: LimitOpt = None, retry: RetryOpt = False, id: IdsOpt = None) -> None:
    """Find websites, then read them for contacts, activity and listing photos."""
    enrich.run(lead_ids=id, limit=limit, retry=retry)
    scoring.rescore()


@app.command("screen")
def screen_cmd(
    limit: LimitOpt = None,
    retry: RetryOpt = False,
    id: IdsOpt = None,
    geocalib: Annotated[
        bool, typer.Option(help="Also estimate camera roll/pitch (slower)")
    ] = False,
) -> None:
    """Download a few listing photos per lead and measure their lean with Veralume."""
    screen.run(lead_ids=id, limit=limit, retry=retry, geocalib=geocalib)
    scoring.rescore()


@app.command()
def score() -> None:
    """Recompute every lead's score."""
    typer.echo(f"scored {scoring.rescore()} leads")


@app.command()
def run(
    target: Annotated[
        int, typer.Option(help="Stop enriching once this many leads are reachable")
    ] = 500,
    batch: Annotated[int, typer.Option(help="Leads enriched per round")] = 150,
) -> None:
    """Everything, in order, until `target` leads have an email or a phone."""
    with session() as s:
        empty = not s.scalar(select(func.count(Lead.id)))
    if empty:
        registre.collect()
        osm.collect()
        photographers.collect_places()
    photographers.collect_photographers()
    while _reachable() < target:
        counts = enrich.run(limit=batch)
        if not counts:
            break
        screen.run()
        scoring.rescore()
        typer.echo(f"reachable leads: {_reachable()} / {target}")
    scoring.rescore()
    stats()


@app.command()
def expand(
    target: Annotated[int, typer.Option(help="Stop once this many leads are reachable")] = 2000,
) -> None:
    """Widen the search ring by ring (Strasbourg circle, then French départements by
    distance), enriching and screening each ring, until `target` leads are reachable."""
    for ring in RINGS:
        if _reachable() >= target:
            break
        if not already_ran("ring", ring.key):
            typer.echo(f"== {ring.label}")
            registre.collect(departement=ring.departement, radius_km=ring.radius_km)
            osm.collect(departement=ring.departement, radius_km=ring.radius_km)
            with session() as s:
                s.add(SearchQuery(engine="ring", query=ring.key))
                s.commit()
        if ring.towns:
            photographers.collect_photographers(
                towns=None if ring.key == "circle-30" else ring.towns
            )
        enrich.run()
        screen.run()
        scoring.rescore()
        typer.echo(f"{ring.label}: {_reachable()} reachable / {target}")
    stats()


@app.command("photographers")
def photographers_cmd(
    search: Annotated[
        bool, typer.Option(help="Also run the slow DuckDuckGo search in every département")
    ] = True,
    sweep: Annotated[
        bool, typer.Option(help="Visit every directory profile, to find new and small ones")
    ] = True,
) -> None:
    """Real-estate photographers all over France, small and new ones included: directory
    city lists, every directory profile, a nationwide search, then their websites (contacts,
    portfolio lean) and the company register (new business, sole trader)."""
    directory.collect()
    _process_new_photographers()
    if search:
        towns = [label.rsplit(" (", 1)[0] for _, label in directory._towns()]
        photographers.collect_photographers(towns=towns)
        _process_new_photographers()
    if sweep:
        directory.sweep(on_batch=_process_new_photographers)
    _photographer_stats()


@app.command()
def abroad(
    country: Annotated[
        list[str] | None,
        typer.Option("--country", "-c", help="BE, LU, CH, DE or CA (Quebec); all by default"),
    ] = None,
    search: Annotated[bool, typer.Option(help="Also run the slow DuckDuckGo search")] = True,
) -> None:
    """Real-estate photographers in Belgium, Luxembourg, Switzerland, Germany and Quebec:
    OpenStreetMap, then a search in each country's languages, then their websites. Each
    lead keeps its country. Rerun to continue after a search captcha."""
    from prospecting.countries import FOREIGN

    codes = [c.upper() for c in country] if country else FOREIGN
    if bad := [c for c in codes if c not in FOREIGN]:
        raise typer.BadParameter(f"unknown country {bad}; use {', '.join(FOREIGN)}")
    for code in codes:
        abroad_src.collect_osm(code)
    _read_then_prune()
    if search:
        for code in codes:
            _, finished = abroad_src.collect_search(code)
            _read_then_prune()
            if not finished:
                typer.echo("DuckDuckGo wants a captcha: rerun `prospect abroad` in an hour or two")
                break
    languages.fill()
    languages.read_sites()
    _country_stats()


@app.command("languages")
def languages_cmd(
    sites: Annotated[bool, typer.Option(help="Also read the language of sites abroad")] = True,
) -> None:
    """Give every lead the language to write to them in (from the region, then the site)."""
    languages.fill()
    if sites:
        languages.read_sites()


def _read_then_prune() -> None:
    """Read the new sites first and drop the non-real-estate OSM studios, so their
    photos are never downloaded and measured."""
    with session() as s:
        ids = list(
            s.scalars(
                select(Lead.id).where(Lead.kind == "photographer", Lead.enrich_status.is_(None))
            )
        )
    if ids:
        enrich.run(lead_ids=ids)
    abroad_src.prune()
    _process_new_photographers()


def _country_stats() -> None:
    with session() as s:
        rows = s.execute(
            select(Lead.country, Lead.emails, Lead.phones, Lead.tags, Lead.do_not_contact).where(
                Lead.kind == "photographer"
            )
        ).all()
    by: dict[str, list[int]] = {}
    for c, emails, phones, tags, dnc in rows:
        d = by.setdefault(c or "?", [0, 0, 0])
        d[0] += 1
        d[1] += "real_estate" in (tags or [])
        d[2] += bool((emails or phones) and not dnc)
    for c, (n, re_, reach) in sorted(by.items(), key=lambda kv: -kv[1][0]):
        typer.echo(f"photographers {c}: {n}, real-estate confirmed {re_}, reachable {reach}")


def _process_new_photographers() -> None:
    """Read the websites of photographers not done yet, measure their photos, check the
    register, score. Run after each batch so contacts appear early."""
    with session() as s:
        ids = list(
            s.scalars(
                select(Lead.id).where(Lead.kind == "photographer", Lead.enrich_status.is_(None))
            )
        )
    if ids:
        enrich.run(lead_ids=ids)
    with session() as s:  # includes any left unmeasured by an interrupted run
        unmeasured = list(
            s.scalars(
                select(Lead.id).where(
                    Lead.kind == "photographer",
                    Lead.enrich_status == "done",
                    Lead.screen_status.is_(None),
                )
            )
        )
    if unmeasured:
        screen.run(lead_ids=unmeasured)
    business.run()
    scoring.rescore()
    _photographer_stats()


def _photographer_stats() -> None:
    with session() as s:
        ph = list(s.scalars(select(Lead).where(Lead.kind == "photographer")))
    reach = [l for l in ph if (l.emails or l.phones) and not l.do_not_contact]
    confirmed = [l for l in ph if "real_estate" in (l.tags or [])]
    small_new = [
        l for l in ph if {"new_business", "sole_trader", "recent_profile", "few_reviews",
                          "social_only"} & set(l.tags or [])
    ]  # fmt: skip
    social = [l for l in ph if l.socials and not l.emails and not l.phones]
    typer.echo(
        f"small or new: {len(small_new)}, reachable only by Instagram/Facebook: {len(social)}"
    )
    typer.echo(
        f"photographers: {len(ph)}, real-estate confirmed: {len(confirmed)}, "
        f"with website: {sum(1 for l in ph if l.website)}, with email: "
        f"{sum(1 for l in ph if l.emails)}, with phone: {sum(1 for l in ph if l.phones)}, "
        f"reachable: {len(reach)}"
    )


def _reachable() -> int:
    with session() as s:
        return sum(
            1
            for l in s.scalars(select(Lead).where(Lead.do_not_contact.is_(False)))
            if l.emails or l.phones
        )


@app.command()
def stats() -> None:
    """Counts by kind, pipeline step and status."""
    with session() as s:
        leads = list(s.scalars(select(Lead)))
    by = lambda f: sorted(
        {k: sum(1 for l in leads if f(l) == k) for k in {f(l) for l in leads}}.items(),
        key=lambda kv: -kv[1],
    )
    typer.echo(f"leads: {len(leads)}")
    typer.echo(f"kind: {by(lambda l: l.kind)}")
    typer.echo(f"enrich: {by(lambda l: l.enrich_status)}")
    typer.echo(f"screen: {by(lambda l: l.screen_status)}")
    typer.echo(f"status: {by(lambda l: l.status)}")
    by_dep: dict[str, list[int]] = {}
    for l in leads:
        d = by_dep.setdefault((l.postcode or "??")[:2], [0, 0])
        d[0] += 1
        d[1] += bool((l.emails or l.phones) and not l.do_not_contact)
    typer.echo(
        "by département (leads/reachable): "
        + ", ".join(
            f"{k} {v[0]}/{v[1]}" for k, v in sorted(by_dep.items(), key=lambda kv: -kv[1][0])
        )
    )
    typer.echo(
        f"with email: {sum(1 for l in leads if l.emails)}, with phone: "
        f"{sum(1 for l in leads if l.phones)}, reachable: {_reachable()}"
    )


@app.command()
def inbox(days: Annotated[int, typer.Option(help="How many days back to read")] = 14) -> None:
    """Read replies and bounces from your mailbox now (the app also does it every 5 min)."""
    from prospecting import inbox as inbox_mod

    with session() as s:
        found = inbox_mod.poll(s, days=days)
        s.commit()
    typer.echo(f"found: {found or 'nothing new'}")


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8011,
) -> None:
    """Open the app on http://127.0.0.1:8011."""
    import uvicorn

    uvicorn.run("prospecting.api.app:app", host=host, port=port)


if __name__ == "__main__":
    app()
