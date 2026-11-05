"""Background loop while the app runs: sends approved emails one at a time inside the
sending window, reads replies every few minutes, writes running campaigns' daily batch."""

from __future__ import annotations

import logging
import threading
import time

from prospecting import campaigns, inbox, mailer
from prospecting.config import settings
from prospecting.db import now, session

log = logging.getLogger("prospecting.worker")
STATUS: dict[str, object] = {"running": False, "last_send": None, "last_error": None}
_stop = threading.Event()


def tick(poll_inbox: bool) -> None:
    with session() as s:
        campaigns.prepare_running(s)
        s.commit()
        msg = mailer.send_due(s)
        s.commit()
        if msg is not None:
            STATUS["last_send"] = now()
            STATUS["last_error"] = msg.error if msg.status == "failed" else None
    if poll_inbox:
        try:
            with session() as s:
                found = inbox.poll(s)
                s.commit()
            inbox.STATUS.update(last_poll=now(), last_error=None, last_found=found)
        except Exception as e:  # network or login trouble: show it, keep going
            inbox.STATUS.update(last_poll=now(), last_error=str(e)[:300])
            log.warning("inbox poll failed: %s", e)


def loop() -> None:
    STATUS["running"] = True
    last_poll = -1e9
    while not _stop.is_set():
        due = time.monotonic() - last_poll >= settings.inbox_poll_s
        try:
            tick(poll_inbox=due and bool(settings.imap_host))
        except Exception as e:
            STATUS["last_error"] = str(e)[:300]
            log.exception("worker tick failed")
        if due:
            last_poll = time.monotonic()
        _stop.wait(15)
    STATUS["running"] = False


def start() -> threading.Thread:
    _stop.clear()
    t = threading.Thread(target=loop, name="prospecting-worker", daemon=True)
    t.start()
    return t


def stop() -> None:
    _stop.set()
