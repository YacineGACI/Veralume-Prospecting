"""Routes for the user's own tags (labels): create, rename, recolour, delete, and put
them on leads or take them off, one lead or many."""

from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from prospecting.db import LABEL_COLORS, Label, Lead, session

router = APIRouter(prefix="/api")


def _out(lab: Label, n: int = 0) -> dict[str, Any]:
    return {"id": lab.id, "name": lab.name, "color": lab.color, "n": n}


@router.get("/labels")
def list_labels() -> dict[str, Any]:
    with session() as s:
        counts: Counter[int] = Counter()
        for ids in s.scalars(select(Lead.labels).where(Lead.labels.is_not(None))):
            counts.update(set(ids or []))
        rows = s.scalars(select(Label).order_by(Label.name))
        return {"items": [_out(lab, counts.get(lab.id, 0)) for lab in rows], "colors": list(LABEL_COLORS)}


class LabelIn(BaseModel):
    name: str | None = None
    color: str | None = None


def _name(s, raw: str, lab: Label | None = None) -> str:
    name = " ".join(raw.split())[:60]
    if not name:
        raise HTTPException(422, "Give the tag a name.")
    clash = s.scalar(select(Label).where(Label.name == name))
    if clash is not None and clash is not lab:
        raise HTTPException(409, f"There is already a tag called “{name}”.")
    return name


def _color(c: str | None) -> str | None:
    if c is not None and c not in LABEL_COLORS:
        raise HTTPException(422, "Unknown colour")
    return c


@router.post("/labels")
def create_label(b: LabelIn) -> dict[str, Any]:
    with session() as s:
        lab = Label(name=_name(s, b.name or ""), color=_color(b.color) or "blue")
        s.add(lab)
        s.commit()
        return _out(lab)


@router.patch("/labels/{label_id}")
def edit_label(label_id: int, b: LabelIn) -> dict[str, Any]:
    with session() as s:
        lab = s.get(Label, label_id)
        if lab is None:
            raise HTTPException(404, "No such tag")
        if b.name is not None:
            lab.name = _name(s, b.name, lab)
        if _color(b.color):
            lab.color = b.color
        s.commit()
        return _out(lab)


@router.delete("/labels/{label_id}")
def delete_label(label_id: int) -> dict[str, Any]:
    """Delete a tag and take it off every lead."""
    with session() as s:
        lab = s.get(Label, label_id)
        if lab is None:
            raise HTTPException(404, "No such tag")
        n = 0
        for lead in s.scalars(select(Lead).where(Lead.labels.is_not(None))):
            if label_id in (lead.labels or []):
                lead.labels = [x for x in lead.labels if x != label_id]
                n += 1
        s.delete(lab)
        s.commit()
        return {"deleted": True, "leads": n}


class ApplyIn(BaseModel):
    lead_ids: list[int]
    add: list[int] = []
    remove: list[int] = []


@router.post("/leads/labels")
def apply_labels(b: ApplyIn) -> dict[str, Any]:
    """Put tags on leads and take tags off, for one lead or many at once."""
    with session() as s:
        known = set(s.scalars(select(Label.id)))
        add = [x for x in b.add if x in known]
        out = {}
        for lead in s.scalars(select(Lead).where(Lead.id.in_(b.lead_ids))):
            keep = [x for x in (lead.labels or []) if x not in b.remove and x in known]
            lead.labels = keep + [x for x in add if x not in keep]  # a new list, so the JSON column saves
            out[lead.id] = lead.labels
        s.commit()
        return {"changed": len(out), "labels": out}
