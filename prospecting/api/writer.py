"""Write and rewrite emails from a prompt."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from prospecting import mailer, writer
from prospecting.db import session

router = APIRouter(prefix="/api")


@router.get("/ai")
def ai_status() -> dict[str, Any]:
    with session() as s:
        return writer.status(s)


class AiSettingsIn(BaseModel):
    provider: str | None = None
    brief: str | None = None


@router.put("/ai")
def ai_settings(b: AiSettingsIn) -> dict[str, Any]:
    with session() as s:
        if b.provider is not None:
            if b.provider not in writer.PROVIDERS or not writer.configured(b.provider):
                raise HTTPException(422, "That AI isn't set up in .env yet.")
            mailer.set_setting(s, "ai_provider", b.provider)
        if b.brief is not None:
            mailer.set_setting(s, "ai_brief", b.brief.strip() or writer.DEFAULT_BRIEF)
        s.commit()
        return writer.status(s)


class WriteIn(BaseModel):
    instruction: str = ""
    subject: str = ""
    body: str = ""
    mode: str = "edit"  # edit: change the current text | new: write a fresh draft
    step: int = 0
    lead_id: int | None = None  # a real email for this lead; without it, a template
    segment: str | None = None


@router.post("/ai/write")
def ai_write(b: WriteIn) -> dict[str, Any]:
    if b.mode not in ("edit", "new"):
        raise HTTPException(422, "mode is edit or new")
    with session() as s:
        try:
            return writer.write(s, instruction=b.instruction, subject=b.subject, body=b.body,
                                mode=b.mode, step=b.step, lead_id=b.lead_id,
                                segment=b.segment)  # fmt: skip
        except writer.WriterError as e:
            raise HTTPException(409, str(e)) from e
