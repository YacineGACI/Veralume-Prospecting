"""A/B tests of the first email: start, read, stop."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from prospecting import experiments
from prospecting.db import Experiment, Message, Template, session

router = APIRouter(prefix="/api")
SEGMENTS = ("photographer", "agency", "independent_agent")


def _fits_segment(t: Template, kind: str) -> bool:
    return t.segment in ("any", kind) or (kind == "independent_agent" and t.segment == "agency")


def _exp(s, e: Experiment) -> dict[str, Any]:
    return {
        "id": e.id, "name": e.name, "kind": e.kind, "metric": e.metric, "status": e.status,
        "template_ids": e.template_ids, "winner_id": e.winner_id,
        "created_at": e.created_at, "ended_at": e.ended_at,
        "results": experiments.results(s, e),
        "audience": experiments.audience(s, e) if e.status == "running" else None,
    }  # fmt: skip


@router.get("/experiments")
def list_experiments() -> dict[str, Any]:
    with session() as s:
        rows = list(s.scalars(select(Experiment).order_by(Experiment.id.desc())))
        tpls = s.scalars(
            select(Template)
            .where(Template.step == 0, Template.key != "after_voicemail")
            .order_by(Template.archived, Template.id)
        )
        return {
            "items": [_exp(s, e) for e in sorted(rows, key=lambda e: e.status != "running")],
            "templates": [
                {"id": t.id, "key": t.key, "name": t.name, "segment": t.segment,
                 "subject": t.subject, "body": t.body, "archived": t.archived,
                 "attach_photo": t.attach_photo, "rule": experiments.audience_rule([t])}
                for t in tpls
            ],
            "metrics": experiments.METRICS,
        }  # fmt: skip


class ExperimentIn(BaseModel):
    name: str
    kind: str
    template_ids: list[int]
    metric: str = "reply"


@router.post("/experiments")
def start(b: ExperimentIn) -> dict[str, Any]:
    if b.kind not in SEGMENTS:
        raise HTTPException(422, "Pick a segment.")
    if b.metric not in experiments.METRICS:
        raise HTTPException(422, "Unknown measure.")
    ids = list(dict.fromkeys(b.template_ids))
    if not 2 <= len(ids) <= 4:
        raise HTTPException(422, "Pick two to four versions to compare.")
    with session() as s:
        running = experiments.running_for(s, b.kind)
        if running:
            raise HTTPException(
                409, f"“{running.name}” already tests this segment. Stop it first, so each "
                "lead is in one test only.",
            )  # fmt: skip
        for i in ids:
            t = s.get(Template, i)
            if (t is None or t.step != 0 or t.archived or t.key == "after_voicemail"
                    or not _fits_segment(t, b.kind)):
                raise HTTPException(422, "Each version must be an active first email for this segment.")
        e = Experiment(name=b.name.strip() or "A/B test", kind=b.kind, template_ids=ids,
                       metric=b.metric, status="running")  # fmt: skip
        s.add(e)
        s.commit()
        return _exp(s, e)


class StopIn(BaseModel):
    winner_id: int | None = None
    archive_losers: bool = False


@router.post("/experiments/{exp_id}/stop")
def stop(exp_id: int, b: StopIn) -> dict[str, Any]:
    with session() as s:
        e = s.get(Experiment, exp_id)
        if e is None:
            raise HTTPException(404, "No such test")
        if b.winner_id is not None and b.winner_id not in e.template_ids:
            raise HTTPException(422, "The winner must be one of the test's versions.")
        experiments.stop(s, e, b.winner_id, b.archive_losers)
        s.commit()
        return _exp(s, e)


class RenameIn(BaseModel):
    name: str


@router.patch("/experiments/{exp_id}")
def rename(exp_id: int, b: RenameIn) -> dict[str, Any]:
    with session() as s:
        e = s.get(Experiment, exp_id)
        if e is None:
            raise HTTPException(404, "No such test")
        e.name = b.name.strip() or e.name
        s.commit()
        return _exp(s, e)


@router.delete("/experiments/{exp_id}")
def delete(exp_id: int) -> dict[str, Any]:
    """Only a test that sent nothing yet; emails it wrote stay, out of any test."""
    with session() as s:
        e = s.get(Experiment, exp_id)
        if e is None:
            raise HTTPException(404, "No such test")
        msgs = list(s.scalars(select(Message).where(Message.experiment_id == e.id)))
        if any(m.status == "sent" for m in msgs):
            raise HTTPException(409, "This test already sent emails. Stop it instead, to keep its results.")
        for m in msgs:
            m.experiment_id = None
        s.delete(e)
        s.commit()
        return {"ok": True}
