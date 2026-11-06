"""PostgreSQL storage: leads, their contacts and measured photos, emails, campaigns,
pilots and the contact log.

Every contact fact keeps its source (`{"value", "source"}`), so "where did you get my
email?" always has an answer.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from prospecting.config import settings

KINDS = ("agency", "independent_agent", "photographer", "other")
STATUSES = (
    "new",
    "shortlisted",
    "contacted",
    "replied",
    "interview",
    "pilot",
    "customer",
    "lost",
)
ACTIVITY_KINDS = (
    "note", "email", "email_in", "call", "visit", "interview", "photos_sent", "status",
)  # fmt: skip
# Call outcomes, as logged by call mode.
CALL_OUTCOMES = {
    "no_answer": "No answer",
    "voicemail": "Voicemail",
    "callback": "Call back",
    "interested": "Interested",
    "not_interested": "Not interested",
    "wrong_number": "Wrong number",
}
# Outbound: queued (written, waiting for review) → approved (sends at the next free slot)
# → sent | failed; cancelled when the lead replies. Inbound: received.
MESSAGE_STATUSES = ("queued", "approved", "sent", "failed", "cancelled", "received")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Base(DeclarativeBase):
    pass


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(300))
    legal_name: Mapped[str | None] = mapped_column(String(300))
    siren: Mapped[str | None] = mapped_column(String(9), index=True)
    siret: Mapped[str | None] = mapped_column(String(14), unique=True)
    osm_id: Mapped[str | None] = mapped_column(String(40), unique=True)
    places_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    legal_form: Mapped[str | None] = mapped_column(String(8))
    company_category: Mapped[str | None] = mapped_column(String(8))
    headcount: Mapped[str | None] = mapped_column(String(4))
    branches: Mapped[int | None] = mapped_column(Integer)
    founded: Mapped[str | None] = mapped_column(String(10))
    network: Mapped[str | None] = mapped_column(String(80))

    # ISO code (FR, BE, LU, CH, DE; CA is Quebec). See countries.py.
    country: Mapped[str | None] = mapped_column(String(2), default="FR", index=True)
    # The language to write to them in (fr de nl it en), and who said so: you | site | region.
    language: Mapped[str | None] = mapped_column(String(2), index=True)
    language_source: Mapped[str | None] = mapped_column(String(8))
    address: Mapped[str | None] = mapped_column(String(300))
    postcode: Mapped[str | None] = mapped_column(String(10))
    city: Mapped[str | None] = mapped_column(String(120), index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    distance_km: Mapped[float | None] = mapped_column(Float)

    website: Mapped[str | None] = mapped_column(String(500))
    website_source: Mapped[str | None] = mapped_column(String(300))
    emails: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    phones: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    people: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    socials: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    # The user's own tags on this lead (ids of Label), unlike `tags`, which the collection sets.
    labels: Mapped[list[int] | None] = mapped_column(JSON, default=list)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    listings_seen: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[float | None] = mapped_column(Float)
    rating_count: Mapped[int | None] = mapped_column(Integer)

    # Pipeline progress: None (to do), done, no_website, no_photos, error.
    enrich_status: Mapped[str | None] = mapped_column(String(20), index=True)
    enrich_note: Mapped[str | None] = mapped_column(String(300))
    enriched_at: Mapped[str | None] = mapped_column(String(32))
    screen_status: Mapped[str | None] = mapped_column(String(20), index=True)
    screened_at: Mapped[str | None] = mapped_column(String(32))
    lean_median: Mapped[float | None] = mapped_column(Float)
    lean_max: Mapped[float | None] = mapped_column(Float)
    photos_measured: Mapped[int] = mapped_column(Integer, default=0)

    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    score_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)

    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(String(32), default=now)
    updated_at: Mapped[str] = mapped_column(String(32), default=now, onupdate=now)

    # Outreach. next_action is "call" (a call-back or a lead put in the call queue) or a
    # free text reminder; it shows in Today once next_action_at has passed.
    next_action: Mapped[str | None] = mapped_column(String(200))
    next_action_at: Mapped[str | None] = mapped_column(String(32), index=True)
    last_contacted_at: Mapped[str | None] = mapped_column(String(32))
    snoozed_until: Mapped[str | None] = mapped_column(String(32))
    campaign_id: Mapped[int | None] = mapped_column(Integer, index=True)
    # The free test: interested → photos → retouching → sent_back → feedback → customer | lost
    pilot_stage: Mapped[str | None] = mapped_column(String(12), index=True)
    pilot_due_at: Mapped[str | None] = mapped_column(String(32))  # when we promised the photos

    photos: Mapped[list[Photo]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="Photo.id"
    )
    activities: Mapped[list[Activity]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", order_by="Activity.at.desc()"
    )

    def add_source(self, kind: str, url: str | None = None) -> None:
        sources = self.sources or []
        if any(s["kind"] == kind and s.get("url") == url for s in sources):
            return
        self.sources = [*sources, {"kind": kind, "url": url, "at": now()}]

    def add_contact(self, field: str, value: str, source: str) -> bool:
        """Append an email or phone if new. JSON columns need a new list to be saved."""
        items: list[dict[str, Any]] = getattr(self, field) or []
        if any(i["value"] == value for i in items):
            return False
        setattr(self, field, [*items, {"value": value, "source": source}])
        return True

    def add_person(self, name: str, role: str | None, source: str) -> None:
        people = self.people or []
        if any(p["name"].lower() == name.lower() for p in people):
            return
        self.people = [*people, {"name": name, "role": role, "source": source}]

    def add_tags(self, tags: set[str]) -> None:
        self.tags = sorted(set(self.tags or []) | tags)


class Photo(Base):
    __tablename__ = "photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(Text)
    page_url: Mapped[str | None] = mapped_column(Text)
    path: Mapped[str | None] = mapped_column(String(300))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    # Length-weighted median lean of near-vertical lines, degrees (Veralume's measure).
    lean_deg: Mapped[float | None] = mapped_column(Float)
    n_lines: Mapped[int | None] = mapped_column(Integer)
    roll_deg: Mapped[float | None] = mapped_column(Float)
    pitch_deg: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(String(300))
    measured_at: Mapped[str | None] = mapped_column(String(32))

    lead: Mapped[Lead] = relationship(back_populates="photos")


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    at: Mapped[str] = mapped_column(String(32), default=now)
    kind: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text, default="")

    lead: Mapped[Lead] = relationship(back_populates="activities")


class SearchQuery(Base):
    """Searches already run, so slow free searches can resume where they stopped."""

    __tablename__ = "search_queries"

    id: Mapped[int] = mapped_column(primary_key=True)
    engine: Mapped[str] = mapped_column(String(20))
    query: Mapped[str] = mapped_column(String(300))
    results: Mapped[int] = mapped_column(Integer, default=0)
    at: Mapped[str] = mapped_column(String(32), default=now)


class Template(Base):
    """An email template. step 0 is a first email, 1 and 2 are follow-ups, -1 a reply.

    Placeholders: {prenom} {agence} {ville} {lean} {n} {site}.
    """

    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    segment: Mapped[str] = mapped_column(String(32), default="any")  # a kind, or "any"
    step: Mapped[int] = mapped_column(Integer, default=0)
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    attach_photo: Mapped[bool] = mapped_column(Boolean, default=True)
    # Images from the library sent with every email of this template (before/after examples).
    asset_ids: Mapped[list[int] | None] = mapped_column(JSON, default=list)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    campaign_id: Mapped[int | None] = mapped_column(Integer, index=True)  # a campaign's version
    # The language of subject/body ("fr" when empty), and the same email in other
    # languages: {"de": {"subject": "...", "body": "..."}}. Each lead gets its own.
    language: Mapped[str | None] = mapped_column(String(2))
    translations: Mapped[dict[str, dict[str, str]] | None] = mapped_column(JSON, default=dict)
    updated_at: Mapped[str] = mapped_column(String(32), default=now, onupdate=now)


class Asset(Base):
    """An image in the library, attached to templates (before/after examples)."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    path: Mapped[str] = mapped_column(String(300))  # relative to data_dir
    size: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(32), default=now)
    dossier_id: Mapped[int | None] = mapped_column(Integer, index=True)


class LeadFile(Base):
    """A photo a lead sent us (received) or our retouched version of one (retouched)."""

    __tablename__ = "lead_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[int | None] = mapped_column(Integer, index=True)  # the email it came in
    kind: Mapped[str] = mapped_column(String(12), default="received")  # received | retouched
    filename: Mapped[str] = mapped_column(String(200))
    path: Mapped[str] = mapped_column(String(300))  # relative to data_dir
    size: Mapped[int] = mapped_column(Integer, default=0)
    original_id: Mapped[int | None] = mapped_column(Integer)  # retouched: the photo it redoes
    sent_at: Mapped[str | None] = mapped_column(String(32))  # retouched: when we sent it back
    created_at: Mapped[str] = mapped_column(String(32), default=now)


class Campaign(Base):
    """Who to write to (a segment, filters, how many, in what order), the email versions
    they get (templates with this campaign_id; two or more make an A/B test), and how it
    goes out (a daily cap, a start day, reviewed each morning or approved once)."""

    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(32))
    # {"deps": ["67"], "max_km": 40, "first_name": true, "small": true, "lean_min": 1,
    #  "website": true, "score_min": 60}
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    template_id: Mapped[int | None] = mapped_column(Integer)  # older campaigns: one template
    followups: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    daily_cap: Mapped[int] = mapped_column(Integer, default=20)
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft running paused done
    created_at: Mapped[str] = mapped_column(String(32), default=now)
    size: Mapped[int | None] = mapped_column(Integer)  # how many leads to contact
    lead_order: Mapped[str | None] = mapped_column(String(12))  # small | score | random
    excluded: Mapped[list[int] | None] = mapped_column(JSON, default=list)  # unticked leads
    included: Mapped[list[int] | None] = mapped_column(JSON, default=list)  # added by hand
    review: Mapped[str | None] = mapped_column(String(8))  # daily (you approve) | auto
    metric: Mapped[str | None] = mapped_column(String(12))  # reply | interested
    start_on: Mapped[str | None] = mapped_column(String(10))  # YYYY-MM-DD
    followups_on: Mapped[bool | None] = mapped_column(Boolean, default=True)
    started_at: Mapped[str | None] = mapped_column(String(32))
    ended_at: Mapped[str | None] = mapped_column(String(32))
    experiment_id: Mapped[int | None] = mapped_column(Integer)


class CampaignLead(Base):
    """A lead in a started campaign, in sending order, with the version it gets."""

    __tablename__ = "campaign_leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(Integer, index=True)
    lead_id: Mapped[int] = mapped_column(Integer, index=True)
    template_id: Mapped[int | None] = mapped_column(Integer)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(32), default=now)


LABEL_COLORS = ("gold", "violet", "teal", "blue", "red", "green", "pink", "grey")


class Label(Base):
    """A tag of the user's own (VIP, Met at the fair…), put on leads by hand."""

    __tablename__ = "labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    color: Mapped[str] = mapped_column(String(12), default="blue")
    created_at: Mapped[str] = mapped_column(String(32), default=now)


class Dossier(Base):
    """A named set of images sent again and again (before/after examples)."""

    __tablename__ = "dossiers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    note: Mapped[str] = mapped_column(String(300), default="")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(32), default=now)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(3), default="out")  # out | in
    status: Mapped[str] = mapped_column(String(12), index=True)
    to_addr: Mapped[str | None] = mapped_column(String(300))
    from_addr: Mapped[str | None] = mapped_column(String(300))
    subject: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    template_id: Mapped[int | None] = mapped_column(Integer)
    campaign_id: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(2))  # the language it's written in
    step: Mapped[int] = mapped_column(Integer, default=0)
    attach_photo_id: Mapped[int | None] = mapped_column(Integer)
    asset_ids: Mapped[list[int] | None] = mapped_column(JSON, default=list)  # library images
    file_ids: Mapped[list[int] | None] = mapped_column(JSON, default=list)  # lead files
    links: Mapped[list[str] | None] = mapped_column(JSON, default=list)  # in: transfer links
    due_at: Mapped[str | None] = mapped_column(String(32), index=True)
    experiment_id: Mapped[int | None] = mapped_column(Integer, index=True)
    edited: Mapped[bool | None] = mapped_column(Boolean)  # first email changed by hand
    sent_at: Mapped[str | None] = mapped_column(String(32), index=True)
    message_id: Mapped[str | None] = mapped_column(String(300), index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(300))
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[str] = mapped_column(String(32), default=now)


class Experiment(Base):
    """An A/B test: the first emails to one segment are split at random between
    two to four templates, and the reply (or interest) rate of each is compared."""

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    template_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    metric: Mapped[str] = mapped_column(String(12), default="reply")  # reply | interested
    status: Mapped[str] = mapped_column(String(12), default="running")  # running | stopped
    winner_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(32), default=now)
    ended_at: Mapped[str | None] = mapped_column(String(32))
    campaign_id: Mapped[int | None] = mapped_column(Integer)  # a campaign's own test


class Setting(Base):
    """Small app settings edited from the UI (goals, default segment, sender name)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


settings.data_dir.mkdir(parents=True, exist_ok=True)


def make_engine(url: str):
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)


engine = make_engine(settings.db_url)
Base.metadata.create_all(engine)


def _migrate() -> None:
    """Add columns created after the first release (ALTER TABLE ADD COLUMN)."""
    with engine.begin() as c:
        insp = inspect(c)
        for table in Base.metadata.sorted_tables:
            have = {col["name"] for col in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    ddl = col.type.compile(engine.dialect)
                    c.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}")
                    if (table.name, col.name) == ("leads", "country"):
                        # Leads before 2026-11-06 all came from French sources.
                        c.exec_driver_sql("UPDATE leads SET country = 'FR'")
                    if (table.name, col.name) == ("leads", "language_source"):
                        c.exec_driver_sql(
                            "UPDATE leads SET language = 'fr', language_source = 'region' "
                            "WHERE COALESCE(country, 'FR') = 'FR'"
                        )


_migrate()


@contextmanager
def session() -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as s:
        yield s
