"""Screening: download a few listing photos per lead and measure how much they lean.

A lead whose walls visibly lean is a lead Veralume can help, and the photo itself is
the pitch. Measuring runs Veralume's line detection in Veralume's own environment
(`measure_worker.py`); nothing paid is called.
"""

from __future__ import annotations

import io
import json
import statistics
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from PIL import Image
from sqlalchemy import select

from prospecting.config import settings
from prospecting.db import Lead, Photo, now, session
from prospecting.http import Fetcher

WORKER = Path(__file__).with_name("measure_worker.py")
MIN_LONG_SIDE = 600
MIN_SHORT_SIDE = 400


def _ahash(im: Image.Image) -> int:
    """64-bit average hash: the same photo at another size or quality hashes alike."""
    px = list(im.convert("L").resize((8, 8), Image.Resampling.BILINEAR).getdata())
    mean = sum(px) / 64
    return sum(1 << i for i, v in enumerate(px) if v > mean)


def _download(
    lead_id: int, candidates: list[tuple[int, str]], kept: int, fetcher: Fetcher
) -> dict[int, dict]:
    """Photos until the lead has `photos_per_lead`. Returns {photo_id: fields to set}."""
    out: dict[int, dict] = {}
    seen: set[int] = set()
    folder = settings.photos_dir / str(lead_id)
    for photo_id, url in candidates:
        if kept >= settings.photos_per_lead:
            break
        data = fetcher.get_bytes(url)
        if not data:
            out[photo_id] = {"error": "download failed or disallowed"}
            continue
        try:
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
                fmt = (im.format or "jpeg").lower()
                digest = _ahash(im)
        except Exception:
            out[photo_id] = {"error": "not an image"}
            continue
        if any(bin(digest ^ d).count("1") <= 6 for d in seen):
            out[photo_id] = {"error": "duplicate"}
            continue
        if max(w, h) < MIN_LONG_SIDE or min(w, h) < MIN_SHORT_SIDE or not 0.5 < w / h < 2.4:
            out[photo_id] = {"error": f"not a listing photo ({w}x{h})"}
            continue
        seen.add(digest)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{photo_id}.{'jpg' if fmt == 'jpeg' else fmt}"
        path.write_bytes(data)
        out[photo_id] = {"path": str(path.relative_to(settings.data_dir)), "width": w, "height": h}
        kept += 1
    return out


FACE_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
    "face_detection_yunet_2023mar.onnx"
)


def _face_model() -> Path | None:
    """YuNet face detector (MIT), to skip staff portraits. Downloaded once."""
    path = settings.data_dir / "models" / "face_detection_yunet_2023mar.onnx"
    if not path.exists():
        try:
            r = httpx.get(FACE_MODEL_URL, follow_redirects=True, timeout=60)
            r.raise_for_status()
        except httpx.HTTPError:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
    return path


def _measure(photos: list[tuple[int, str]], geocalib: bool) -> dict[int, dict]:
    py = settings.veralume_python
    if not py.exists():
        raise RuntimeError(f"Veralume's environment not found at {py}; run `uv sync` there")
    cmd = [str(py), str(WORKER)] + (["--geocalib"] if geocalib else [])
    if (model := _face_model()) is not None:
        cmd.append(f"--faces={model}")
    stdin = "".join(
        json.dumps({"id": pid, "path": str(settings.data_dir / rel)}) + "\n" for pid, rel in photos
    )
    proc = subprocess.run(
        cmd, input=stdin, capture_output=True, text=True, cwd=settings.veralume_dir, check=False
    )
    if proc.returncode != 0 and not proc.stdout:
        raise RuntimeError(f"measure worker failed: {proc.stderr[-800:]}")
    return {(r := json.loads(line))["id"]: r for line in proc.stdout.splitlines() if line.strip()}


def _aggregate(lead: Lead) -> None:
    leans = [p.lean_deg for p in lead.photos if p.lean_deg is not None]
    lead.photos_measured = len(leans)
    lead.lean_median = round(statistics.median(leans), 2) if leans else None
    lead.lean_max = round(max(leans), 2) if leans else None
    lead.screen_status = "done" if leans else "no_photos"
    lead.screened_at = now()


def run(
    lead_ids: list[int] | None = None,
    limit: int | None = None,
    retry: bool = False,
    geocalib: bool = False,
    workers: int = 6,
    progress: Callable[[str], None] = print,
) -> dict[str, int]:
    with session() as s:
        q = select(Lead).where(Lead.enrich_status == "done")
        if lead_ids:
            q = q.where(Lead.id.in_(lead_ids))
        elif not retry:
            q = q.where(Lead.screen_status.is_(None))
        leads = list(s.scalars(q.order_by(Lead.score.desc())))[:limit]
        jobs = {
            l.id: (
                [(p.id, p.url) for p in l.photos if p.path is None and p.error is None],
                sum(p.path is not None for p in l.photos),
            )
            for l in leads
        }
    progress(f"screen: downloading photos for {len(jobs)} leads")

    fetcher = Fetcher()
    with ThreadPoolExecutor(workers) as ex:
        results = dict(
            zip(jobs, ex.map(lambda kv: _download(kv[0], *kv[1], fetcher), jobs.items()))
        )
    fetcher.close()
    with session() as s:
        for updates in results.values():
            for pid, fields in updates.items():
                photo = s.get(Photo, pid)
                if photo is None:
                    continue
                for k, v in fields.items():
                    setattr(photo, k, v)
        s.commit()
        to_measure = [
            (p.id, p.path)
            for p in s.scalars(
                select(Photo).where(
                    Photo.lead_id.in_(list(jobs)),
                    Photo.path.is_not(None),
                    Photo.measured_at.is_(None),
                )
            )
        ]
    progress(f"screen: measuring {len(to_measure)} photos with Veralume")
    measured = _measure(to_measure, geocalib) if to_measure else {}
    counts: dict[str, int] = {}
    with session() as s:
        for pid, r in measured.items():
            photo = s.get(Photo, pid)
            if photo is None:  # its lead was deleted meanwhile
                continue
            photo.lean_deg = None if r["lean_deg"] is None else round(r["lean_deg"], 2)
            photo.n_lines = r["n_lines"]
            photo.roll_deg, photo.pitch_deg = r.get("roll_deg"), r.get("pitch_deg")
            photo.error = r["error"]
            photo.measured_at = now()
        s.flush()
        for lead in s.scalars(select(Lead).where(Lead.id.in_(list(jobs)))):
            _aggregate(lead)
            counts[lead.screen_status] = counts.get(lead.screen_status, 0) + 1  # type: ignore[index]
        s.commit()
    progress(f"screen: {counts}")
    return counts
