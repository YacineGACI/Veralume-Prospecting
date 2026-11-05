"""Routes for pilots: the image library, the photos leads send us and our retouched
versions, and the pilots board. Uploads are the raw file in the body, its name in ?name=."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import select

from prospecting import mailer, pilot
from prospecting.db import Asset, Lead, LeadFile, Message, session
from prospecting.outreach import summary

router = APIRouter(prefix="/api")


def _lead(s, lead_id: int) -> Lead:
    lead = s.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "No lead with this id")
    return lead


async def _body(request: Request) -> bytes:
    data = await request.body()
    if not data:
        raise HTTPException(422, "The file is empty.")
    return data


def _thumb(rel: str) -> FileResponse:
    try:
        return FileResponse(pilot.thumb(rel), media_type="image/jpeg",
                            headers={"Cache-Control": "max-age=86400"})  # fmt: skip
    except (pilot.FileError, OSError) as e:
        raise HTTPException(404, "No preview for this file") from e


# --- the image library ---------------------------------------------------------------


@router.get("/assets")
def assets(archived: bool = False) -> dict[str, Any]:
    with session() as s:
        q = select(Asset).order_by(Asset.id)
        if not archived:
            q = q.where(Asset.archived.is_(False))
        return {"items": [pilot.asset_dict(a) for a in s.scalars(q)]}


@router.post("/assets")
async def add_asset(request: Request, name: str, dossier_id: int | None = None) -> dict[str, Any]:
    data = await _body(request)
    with session() as s:
        try:
            a = pilot.add_asset(s, name, data)
        except pilot.FileError as e:
            raise HTTPException(422, str(e)) from e
        a.dossier_id = dossier_id
        s.commit()
        return pilot.asset_dict(a)


class AssetPatch(BaseModel):
    name: str | None = None
    archived: bool | None = None
    dossier_id: int | None = None


@router.patch("/assets/{asset_id}")
def edit_asset(asset_id: int, p: AssetPatch) -> dict[str, Any]:
    with session() as s:
        a = s.get(Asset, asset_id)
        if a is None:
            raise HTTPException(404, "No such image")
        if p.name is not None and p.name.strip():
            a.name = p.name.strip()[:160]
        if p.archived is not None:
            a.archived = p.archived
        if p.dossier_id is not None:
            a.dossier_id = p.dossier_id
        s.commit()
        return pilot.asset_dict(a)


@router.get("/assets/{asset_id}/file")
def asset_file(asset_id: int) -> FileResponse:
    with session() as s:
        a = s.get(Asset, asset_id)
        if a is None:
            raise HTTPException(404, "No such image")
        return FileResponse(pilot.abs_path(a.path))


@router.get("/assets/{asset_id}/thumb")
def asset_thumb(asset_id: int) -> FileResponse:
    with session() as s:
        a = s.get(Asset, asset_id)
        if a is None:
            raise HTTPException(404, "No such image")
        return _thumb(a.path)


# --- a lead's photos -----------------------------------------------------------------


def lead_files(s, lead: Lead) -> dict[str, Any]:
    rows = list(s.scalars(select(LeadFile).where(LeadFile.lead_id == lead.id).order_by(LeadFile.id)))
    links = []
    for m in s.scalars(
        select(Message).where(Message.lead_id == lead.id, Message.direction == "in")
        .order_by(Message.id)
    ):  # fmt: skip
        links += [{"url": u, "at": m.sent_at, "message_id": m.id} for u in m.links or []]
    c = pilot.counts(s, [lead.id])[lead.id]
    return {
        "files": [pilot.file_dict(f) for f in rows], "links": links, "counts": c,
        "stage": lead.pilot_stage, "due_at": lead.pilot_due_at, "next": pilot.next_step(lead, c),
        "stages": [{"key": k, "label": pilot.STAGE_LABELS[k]} for k in pilot.STAGES],
    }  # fmt: skip


@router.get("/leads/{lead_id}/files")
def files(lead_id: int) -> dict[str, Any]:
    with session() as s:
        return lead_files(s, _lead(s, lead_id))


@router.post("/leads/{lead_id}/files")
async def upload(lead_id: int, request: Request, name: str, kind: str = "retouched") -> dict[str, Any]:
    if kind not in ("received", "retouched"):
        raise HTTPException(422, "kind is received or retouched")
    data = await _body(request)
    with session() as s:
        lead = _lead(s, lead_id)
        try:
            f = pilot.add_file(s, lead, name, data, kind)
        except pilot.FileError as e:
            raise HTTPException(422, str(e)) from e
        if kind == "retouched" and lead.pilot_stage in (None, "interested", "photos"):
            pilot.set_stage(s, lead, "retouching")
        elif kind == "received" and lead.pilot_stage in (None, "interested"):
            pilot.set_stage(s, lead, "photos")
        s.commit()
        return {"file": pilot.file_dict(f), **lead_files(s, lead)}


@router.get("/leads/{lead_id}/files.zip")
def zip_files(lead_id: int, kind: str = "received") -> Response:
    with session() as s:
        lead = _lead(s, lead_id)
        rows = list(s.scalars(
            select(LeadFile).where(LeadFile.lead_id == lead.id, LeadFile.kind == kind)
            .order_by(LeadFile.id)
        ))  # fmt: skip
        if not rows:
            raise HTTPException(404, "No photos to download")
        name = pilot.safe_name(lead.name).lower()
        data = pilot.zip_files(rows)
    label = "photos" if kind == "received" else "retouchees"
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="{name}-{label}.zip"'})  # fmt: skip


@router.get("/files/{file_id}")
def file(file_id: int, download: bool = False) -> FileResponse:
    with session() as s:
        f = s.get(LeadFile, file_id)
        if f is None:
            raise HTTPException(404, "No such file")
        return FileResponse(
            pilot.abs_path(f.path), filename=f.filename if download else None,
            content_disposition_type="attachment" if download else "inline",
        )  # fmt: skip


@router.get("/files/{file_id}/thumb")
def file_thumb(file_id: int) -> FileResponse:
    with session() as s:
        f = s.get(LeadFile, file_id)
        if f is None:
            raise HTTPException(404, "No such file")
        return _thumb(f.path)


@router.delete("/files/{file_id}")
def delete_file(file_id: int) -> dict[str, Any]:
    with session() as s:
        f = s.get(LeadFile, file_id)
        if f is None:
            raise HTTPException(404, "No such file")
        lead = _lead(s, f.lead_id)
        p = pilot.abs_path(f.path)
        s.delete(f)
        s.commit()
        p.unlink(missing_ok=True)
        return lead_files(s, lead)


# --- the pilot's steps ---------------------------------------------------------------


class PilotIn(BaseModel):
    stage: str | None = None
    due_at: str | None = None
    clear: bool = False  # take the lead out of the pilots


@router.put("/leads/{lead_id}/pilot")
def set_pilot(lead_id: int, p: PilotIn) -> dict[str, Any]:
    with session() as s:
        lead = _lead(s, lead_id)
        try:
            pilot.set_stage(s, lead, None if p.clear else (p.stage or lead.pilot_stage), p.due_at)
        except pilot.FileError as e:
            raise HTTPException(422, str(e)) from e
        if lead.pilot_stage in ("customer", "lost"):
            mailer.cancel_pending(s, lead.id, f"pilot ended: {lead.pilot_stage}")
        s.commit()
        return lead_files(s, lead)


@router.get("/pilots")
def pilots() -> dict[str, Any]:
    with session() as s:
        leads = list(s.scalars(select(Lead).where(Lead.pilot_stage.is_not(None))
                               .order_by(Lead.updated_at.desc())))  # fmt: skip
        c = pilot.counts(s, [l.id for l in leads])
        thumbs: dict[int, list[str]] = {}
        for f in s.scalars(
            select(LeadFile).where(LeadFile.lead_id.in_([l.id for l in leads]))
            .order_by(LeadFile.kind.desc(), LeadFile.id)  # retouched first
        ):  # fmt: skip
            t = thumbs.setdefault(f.lead_id, [])
            if len(t) < 4:
                t.append(f"/api/files/{f.id}/thumb")
        today = mailer.local()
        cols: dict[str, list[dict[str, Any]]] = {k: [] for k in pilot.STAGES}
        need = 0
        for l in leads:
            cc = c[l.id]
            since = l.last_contacted_at or l.updated_at
            waiting = (today - mailer.local(since)).days if since else 0
            due = l.pilot_due_at
            urgent = (
                (l.pilot_stage in ("photos", "retouching") and (
                    not due or mailer.local(due) <= today + timedelta(days=1)))
                or (l.pilot_stage in ("interested", "sent_back") and waiting >= 4)
                or l.pilot_stage == "feedback"
            )  # fmt: skip
            need += bool(urgent)
            cols[l.pilot_stage].append({
                **summary(l), "counts": cc, "thumbs": thumbs.get(l.id, []),
                "next": pilot.next_step(l, cc), "urgent": bool(urgent), "waiting_days": waiting,
            })  # fmt: skip
        total = {k: sum(x[k] for x in c.values()) for k in ("received", "retouched", "sent")}
        return {
            "columns": cols, "stages": [{"key": k, "label": pilot.STAGE_LABELS[k]} for k in pilot.STAGES],
            "need": need, "running": sum(len(cols[k]) for k in pilot.STAGES[:5]),
            "customers": len(cols["customer"]), "photos": total,
        }  # fmt: skip
