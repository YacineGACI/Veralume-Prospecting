"""Reading the mailbox: quick reads of only what's new, a full re-read of the window once an
hour, a window that reaches back after a long stop, no 500-email cut-off, and a fresh start
if Gmail renumbers the mailbox. A fake mailbox stands in for Gmail: nothing is read or sent."""

import re
from datetime import datetime, timedelta

import pytest

from prospecting import inbox, mailer
from prospecting.config import settings
from prospecting.db import Message
from prospecting.mailer import TZ, local
from tests.test_bounces import _campaign_sent
from tests.test_campaigns import outside_sending_hours  # noqa: F401  (autouse)


class FakeGmail:
    """Just enough IMAP: UID SEARCH (SINCE date / UID n:*) and UID FETCH, with a log of calls."""

    def __init__(self):
        self.mails: list[tuple[int, datetime, bytes]] = []  # uid, date, raw
        self.validity = b"1"
        self.searches: list[tuple] = []
        self.fetched_bodies: list[bytes] = []
        self.header_batches: list[int] = []

    def add(self, raw: str, days_ago: float = 0) -> None:
        uid = (self.mails[-1][0] if self.mails else 100) + 1
        self.mails.append((uid, local() - timedelta(days=days_ago), raw.encode()))

    # the imaplib.IMAP4_SSL interface used by inbox.poll
    def __call__(self, host, port):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        pass

    def select(self, box, readonly=False):
        assert readonly, "the mailbox must be opened read-only"
        return "OK", [str(len(self.mails)).encode()]

    def response(self, code):
        return code, [self.validity]

    def uid(self, command, *args):
        if command == "SEARCH":
            self.searches.append(args[1:])
            if args[1] == "SINCE":
                since = datetime.strptime(args[2], "%d-%b-%Y").replace(tzinfo=TZ)
                found = [u for u, d, _ in self.mails if d.date() >= since.date()]
            else:  # "UID n:*": IMAP always returns the last email, even if below n
                lo = int(args[2].split(":")[0])
                found = [u for u, _, _ in self.mails if u >= lo] or [self.mails[-1][0]]
            return "OK", [b" ".join(str(u).encode() for u in found)]
        uids, what = args
        wanted = {int(u) for u in (uids.split(b",") if isinstance(uids, bytes) else [uids])}
        rows = [(u, raw) for u, _, raw in self.mails if u in wanted]
        if "HEADER.FIELDS" in what:
            self.header_batches.append(len(rows))
            out = []
            for u, raw in rows:
                head = raw.split(b"\n\n", 1)[0]
                keep = b"\n".join(l for l in head.split(b"\n") if re.match(
                    rb"(?i)(message-id|from|in-reply-to|references):", l))  # fmt: skip
                out += [
                    (f"{u} (UID {u} BODY[HEADER] {{{len(keep)}}}".encode(), keep + b"\n\n"),
                    b")",
                ]
            return "OK", out
        self.fetched_bodies += [str(u).encode() for u, _ in rows]
        return "OK", [(f"{rows[0][0]} (BODY[] {{1}}".encode(), rows[0][1]), b")"]


def _reply(m, n):
    return (f"From: Claire <{m['to']}>\nTo: yacine.veralume@gmail.com\nSubject: Re: Retouche\n"
            f"Message-ID: <reply-{n}@studio.fr>\nIn-Reply-To: {m['message_id']}\n\nOui, intéressée !\n")  # fmt: skip


def _noise(n):
    return f"From: news@shop.com\nTo: yacine.veralume@gmail.com\nSubject: Promo {n}\nMessage-ID: <n{n}@shop.com>\n\nPromo\n"


@pytest.fixture
def gmail(monkeypatch):
    fake = FakeGmail()
    monkeypatch.setattr(inbox.imaplib, "IMAP4_SSL", fake)
    monkeypatch.setattr(settings, "imap_host", "imap.example")
    monkeypatch.setattr(settings, "smtp_user", "me@example")
    monkeypatch.setattr(settings, "smtp_password", "x")
    monkeypatch.setitem(inbox._FULL_DONE, "since_start", False)  # as if the app just started
    return fake


def _sent(db, n=2):
    _, ids, msgs = _campaign_sent(db, n=n)
    with db() as s:
        for lid in ids:
            msgs[lid]["message_id"] = s.get(Message, msgs[lid]["id"]).message_id
    return [msgs[l] for l in ids]


def _poll(db, **kw):
    with db() as s:
        found = inbox.poll(s, **kw)
        s.commit()
        return found, dict(mailer.get_setting(s, "inbox_state", {}))


def _shift(db, key, delta):
    with db() as s:
        st = dict(mailer.get_setting(s, "inbox_state", {}))
        st[key] = (datetime.fromisoformat(st[key]) - delta).isoformat()
        mailer.set_setting(s, "inbox_state", st)
        s.commit()


def test_quick_reads_ask_only_for_new_mail_and_a_full_read_comes_once_an_hour(db, gmail):
    a, b = _sent(db)
    gmail.add(_noise(1), 3)
    gmail.add(_reply(a, 1), 2)
    found, state = _poll(db)  # first ever: the whole 14 days
    assert found == {"reply": 1} and gmail.searches[-1][0] == "SINCE"
    assert state["last_uid"] == 102 and state["uidvalidity"] == "1"

    found, _ = _poll(db)  # nothing new: one quick question, no download
    assert (
        found == {} and gmail.searches[-1] == ("UID", "103:*") and gmail.fetched_bodies == [b"102"]
    )

    gmail.add(_reply(b, 2))
    found, state = _poll(db)  # only the new email is read
    assert found == {"reply": 1} and gmail.header_batches[-1] == 1 and state["last_uid"] == 103

    _shift(db, "last_full", timedelta(hours=2))  # an hour later: the whole window again,
    found, _ = _poll(db)  # and nothing is counted twice
    assert found == {} and gmail.searches[-1][0] == "SINCE" and gmail.header_batches[-1] == 3

    inbox._FULL_DONE["since_start"] = False  # the app restarts a minute later: full read
    _poll(db)
    assert gmail.searches[-1][0] == "SINCE"


def test_after_a_long_stop_the_window_reaches_back_to_the_last_good_check(db, gmail):
    a, _ = _sent(db)
    gmail.add(_noise(1))
    _poll(db)
    _shift(db, "last_ok", timedelta(days=21))  # the app was off for 3 weeks
    gmail.add(_reply(a, 1), days_ago=20)  # arrived meanwhile, older than 14 days
    found, _ = _poll(db, full=True)
    assert found == {"reply": 1}
    since = datetime.strptime(gmail.searches[-1][1], "%d-%b-%Y").replace(tzinfo=TZ)
    assert (local() - since).days >= 21


def test_more_than_500_emails_are_all_read_in_batches(db, gmail):
    a, _ = _sent(db)
    gmail.add(_reply(a, 1), 5)  # the oldest of 1201: the old code cut it off
    for n in range(1200):
        gmail.add(_noise(n), 1)
    found, state = _poll(db)
    assert found == {"reply": 1} and gmail.header_batches == [500, 500, 201]
    assert state["last_uid"] == 1301


def test_if_gmail_renumbers_the_mailbox_everything_is_read_again(db, gmail):
    a, b = _sent(db)
    gmail.add(_reply(a, 1))
    _poll(db)
    gmail.mails = [(5, local(), _reply(b, 2).encode())]  # new numbers, lower than before
    gmail.validity = b"2"
    found, state = _poll(db)
    assert found == {"reply": 1} and gmail.searches[-1][0] == "SINCE"
    assert state["last_uid"] == 5 and state["uidvalidity"] == "2"
