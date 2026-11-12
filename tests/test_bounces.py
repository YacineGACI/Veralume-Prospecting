"""Addresses that don't exist: the bounce is recognised whatever server sent it, the address
and the lead are flagged, and the campaign shows it instead of offering to send again.
Dry-run mailbox: nothing is sent."""

import email
from email.policy import default

from sqlalchemy import select

from prospecting import contacts, inbox
from prospecting.db import Lead, Message
from tests.test_campaign_languages import FR
from tests.test_campaigns import client, draft, leads
from tests.test_campaigns import outside_sending_hours  # noqa: F401  (autouse)

GMAIL = """From: Mail Delivery Subsystem <mailer-daemon@googlemail.com>
To: yacine.veralume@gmail.com
Subject: Delivery Status Notification (Failure)
Message-ID: <ndr-gmail@mx.google.com>
X-Failed-Recipients: {to}
In-Reply-To: {mid}
References: {mid}
MIME-Version: 1.0
Content-Type: multipart/report; boundary="B"; report-type=delivery-status

--B
Content-Type: text/plain; charset="UTF-8"

Address not found
Your message wasn't delivered to {to} because the address couldn't be found.

--B
Content-Type: message/delivery-status

Reporting-MTA: dns; googlemail.com

Final-Recipient: rfc822; {to}
Action: failed
Status: 5.1.1

--B
Content-Type: message/rfc822

Message-ID: {mid}
Subject: Retouche
To: {to}

Bonjour
--B--
"""

# Outlook/Exchange: the address only in the delivery report; no ids anywhere.
EXCHANGE = """From: postmaster@outlook.com
To: yacine.veralume@gmail.com
Subject: Undeliverable: Retouche
Message-ID: <ndr-exchange@outlook.com>
MIME-Version: 1.0
Content-Type: multipart/report; report-type=delivery-status; boundary="B"

--B
Content-Type: text/plain

Delivery has failed to these recipients or groups.

--B
Content-Type: message/delivery-status

Reporting-MTA: dns;outlook.com

Final-Recipient: rfc822;{to}
Action: failed
Status: 5.1.10
--B--
"""

# An old-style server: plain text only, the address named in it.
PLAIN = """From: MAILER-DAEMON@smtp.example.fr
To: yacine.veralume@gmail.com
Subject: failure notice
Message-ID: <ndr-plain@smtp.example.fr>

Hi. This is the qmail-send program. I'm afraid I wasn't able to deliver your message
to the following addresses.

<{to}>:
Sorry, no mailbox here by that name.
"""


def _campaign_sent(db, n=3, **kw):
    ids = leads(db, n, **kw)
    c = draft(size=50)
    client.put(f"/api/campaigns/{c['id']}", json={"versions": [{"name": "A", **FR}]})
    client.post(f"/api/campaigns/{c['id']}/start")
    msgs = {}
    for lid in ids:
        m = client.post(f"/api/campaigns/{c['id']}/leads/{lid}/email").json()["message"]
        msgs[lid] = client.post(f"/api/messages/{m['id']}/send").json()
    return c["id"], ids, msgs


def _bounce(db, raw, m):
    with db() as s:
        em = email.message_from_string(raw.format(to=m["to"], mid=m.get("message_id") or "<none@x>"), policy=default)
        what = inbox.handle(s, em)
        s.commit()
        return what


def test_bounces_from_gmail_outlook_and_old_servers_are_recognised(db):
    cid, ids, msgs = _campaign_sent(db)
    with db() as s:  # the sent emails' Message-IDs
        for lid in ids:
            msgs[lid]["message_id"] = s.scalars(select(Message.message_id).where(Message.id == msgs[lid]["id"])).one()
    for raw, lid in zip((GMAIL, EXCHANGE, PLAIN), ids, strict=True):
        assert _bounce(db, raw, msgs[lid]) == "bounce", raw[:40]
    with db() as s:
        for lid in ids:
            lead = s.get(Lead, lid)
            m = s.get(Message, msgs[lid]["id"])
            assert m.status == "failed" and m.error == "bounced"
            assert lead.emails[0]["bounced"] and not contacts.usable_emails(lead)
            assert "Email: bounced" in contacts.flags(lead)  # the flag shown in Leads and on the lead
    assert _bounce(db, GMAIL, msgs[ids[0]]) is None  # read again: already done, nothing new


def test_the_campaign_shows_bounced_leads_and_never_resends_to_the_dead_address(db):
    cid, ids, msgs = _campaign_sent(db, n=2)
    with db() as s:  # the second lead has another address of their own
        lead = s.get(Lead, ids[1])
        lead.emails = [*lead.emails, {"value": "claire@studio1.fr", "source": "https://studio1.fr"}]
        s.commit()
    for lid in ids:
        _bounce(db, EXCHANGE, msgs[lid])
    r = client.get(f"/api/campaigns/{cid}").json()["results"]
    states = {l["id"]: l["state"] for l in r["leads"]}
    assert states == {ids[0]: "bounced", ids[1]: "bounced_retry"} and r["totals"]["bounced"] == 2
    url = f"/api/campaigns/{cid}/leads"
    dead = client.post(f"{url}/{ids[0]}/email")
    assert dead.status_code == 409 and "no other" in dead.json()["detail"]
    retry = client.post(f"{url}/{ids[1]}/email").json()["message"]
    assert retry["to"] == "claire@studio1.fr" and retry["status"] == "queued"


def test_bounce_ignores_html_and_binary_attachment_message_ids(db):
    _, ids, msgs = _campaign_sent(db, n=2)
    with db() as s:
        real = s.get(Message, msgs[ids[0]]["id"])
        other = s.get(Message, msgs[ids[1]]["id"])
        em = email.message_from_string(
            GMAIL.format(to=real.to_addr, mid=real.message_id), policy=default
        )
        html = inbox.EmailMessage()
        html.set_content("<html><body>Delivery failed</body></html>", subtype="html")
        em.attach(html)
        image = inbox.EmailMessage()
        image.set_content(b"PNG<\x00\x00IDAT>" + other.message_id.encode(),
                          maintype="image", subtype="png", cte="base64")
        em.attach(image)
        # Exercise MIME parsing, including decoded inline image bytes.
        parsed = email.message_from_bytes(em.as_bytes(), policy=default)
        assert inbox.handle(s, parsed) == "bounce"
        s.commit()
        assert real.status == "failed"
        assert other.status == "sent"
        assert not s.get(Lead, ids[1]).emails[0].get("bounced")


def test_message_id_extraction_rejects_html_and_control_bytes():
    assert inbox._ids(
        '<html><table cellpadding="0"><\x00IDAT><bad\x00@id> '
        '<valid@example.com>\r\n <another@example.org>'
    ) == ["<valid@example.com>", "<another@example.org>"]


def test_incoming_reply_removes_nul_before_queries_and_storage(db):
    _, ids, msgs = _campaign_sent(db, n=1)
    with db() as s:
        sent = s.get(Message, msgs[ids[0]]["id"])
        em = inbox.EmailMessage()
        em["Message-ID"] = "<incoming\x00@example.com>"
        em["From"] = sent.to_addr
        em["To"] = "me\x00@example.com"
        em["In-Reply-To"] = sent.message_id
        em["Subject"] = "Re: bon\x00jour"
        em.set_content("Bonjour\x00, merci !")
        assert inbox.handle(s, em) == "reply"
        s.commit()
        incoming = s.scalar(select(Message).where(Message.direction == "in"))
        assert incoming.message_id == "<incoming@example.com>"
        assert incoming.subject == "Re: bonjour"
        assert incoming.to_addr == "me@example.com"
        assert incoming.body == "Bonjour, merci !"
        assert inbox.handle(s, em) is None
