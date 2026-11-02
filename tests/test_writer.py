"""Writing emails from a prompt. Providers are faked: no request leaves the machine."""

import json
import sys
import types

import pytest
from fastapi.testclient import TestClient

from prospecting import writer
from prospecting.api.app import app
from prospecting.config import settings
from prospecting.db import Lead

client = TestClient(app)


def _lead(db, **kw) -> int:
    with db() as s:
        lead = Lead(
            kind="photographer", name="Studio Lumière", city="Colmar", status="new",
            emails=[{"value": "hello@studio-lumiere.fr", "source": "https://studio-lumiere.fr"}],
            phones=[], people=[{"name": "Claire Martin", "role": None, "source": "x"}],
            tags=["sole_trader"], sources=[], score=60, do_not_contact=False,
            website="https://studio-lumiere.fr", **kw,
        )  # fmt: skip
        s.add(lead)
        s.commit()
        return lead.id


@pytest.fixture
def fake(monkeypatch):
    """A configured Gemini whose answer we choose; records what it was sent."""
    seen = {}
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")

    def call(system, user):
        seen.update(system=system, user=user)
        default = {"subject": "Vos photos", "body": "Bonjour Claire,\n\nTexte.\n\nYacine"}
        return seen.get("reply", json.dumps(default))

    monkeypatch.setitem(writer.CALL, "gemini", call)
    return seen


def test_no_ai_set_up_says_how(db):
    lid = _lead(db)
    r = client.post("/api/ai/write", json={"lead_id": lid, "instruction": "plus court", "body": "x"})
    assert r.status_code == 409 and ".env" in r.json()["detail"]
    assert client.get("/api/ai").json()["current"] is None


def test_rewrite_sends_facts_draft_and_instruction(db, fake):
    lid = _lead(db, lean_median=2.4, photos_measured=4)
    r = client.post("/api/ai/write", json={
        "lead_id": lid, "instruction": "plus court et plus chaleureux", "mode": "edit",
        "subject": "Retouche", "body": "Bonjour Claire,\n\nAncien texte.",
    })  # fmt: skip
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["subject"] == "Vos photos" and out["provider"] == "gemini"
    u = fake["user"]
    assert "Studio Lumière" in u and "Colmar" in u and "2,4°" in u and "Claire" in u
    assert "Ancien texte." in u and "plus court et plus chaleureux" in u
    assert "Travaille seul(e)" in u
    assert "N'invente rien" in fake["system"] and "désinscription" in fake["system"]


def test_unmeasured_lead_gets_no_lean(db, fake):
    lid = _lead(db)
    client.post("/api/ai/write", json={"lead_id": lid, "instruction": "écris-le", "mode": "new"})
    assert "n'ont pas été mesurées" in fake["user"] and "inclinaison médiane" not in fake["user"]


def test_template_mode_keeps_only_known_placeholders(db, fake):
    fake["reply"] = json.dumps(
        {"subject": "Pour {agence}", "body": "Bonjour {prenom}, {foo} à {ville}.\n\n{signature}"}
    )
    r = client.post("/api/ai/write", json={"instruction": "un premier email", "mode": "new",
                                           "segment": "photographer"})  # fmt: skip
    assert r.json()["body"] == "Bonjour {prenom}, foo à {ville}.\n\n{signature}"
    assert "MODÈLE" in fake["system"]


def test_bad_answers_are_explained(db, fake):
    fake["reply"] = "désolé, je ne peux pas"
    lid = _lead(db)
    r = client.post("/api/ai/write", json={"lead_id": lid, "instruction": "x", "body": "y"})
    assert r.status_code == 409 and "didn't return an email" in r.json()["detail"]


def test_choose_provider_and_brief(db, fake, monkeypatch):
    assert client.put("/api/ai", json={"provider": "claude"}).status_code == 422  # not set up
    monkeypatch.setattr(settings, "mistral_api_key", "k")
    st = client.put("/api/ai", json={"provider": "mistral", "brief": "Mon offre."}).json()
    assert st["current"] == "mistral" and st["brief"] == "Mon offre."
    assert {p["key"]: p["configured"] for p in st["providers"]} == {
        "gemini": True, "mistral": True, "ollama": False, "claude": False}


def test_claude_request_shape(monkeypatch):
    """The Claude call uses the SDK with JSON-schema output, low effort and fallbacks."""
    sent = {}

    class Resp:
        stop_reason = "end_turn"
        content = [types.SimpleNamespace(type="text", text='{"subject": "S", "body": "B"}')]

    class Messages:
        def create(self, **kw):
            sent.update(kw)
            return Resp()

    class Client:
        def __init__(self, **kw):
            self.beta = types.SimpleNamespace(messages=Messages())

    fake_mod = types.ModuleType("anthropic")
    fake_mod.Anthropic = Client
    for name in ("AuthenticationError", "RateLimitError", "APIStatusError", "APIConnectionError"):
        setattr(fake_mod, name, type(name, (Exception,), {}))
    monkeypatch.setitem(sys.modules, "anthropic", fake_mod)
    assert json.loads(writer._claude("sys", "user")) == {"subject": "S", "body": "B"}
    assert sent["model"] == "claude-opus-5-5" and sent["fallbacks"] == "default"
    assert sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert sent["output_config"]["effort"] == "low"
    assert sent["output_config"]["format"]["schema"]["required"] == ["subject", "body"]
    Resp.stop_reason = "refusal"
    with pytest.raises(writer.WriterError):
        writer._claude("sys", "user")
