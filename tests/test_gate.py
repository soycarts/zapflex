"""Tests for the approval gate's Wassist/Telegram notify and inbound-resolve paths.

These guard the oversight contract: a gated action always queues + audits even with
no channel configured, the Wassist call builds the right WhatsApp payload when keyed,
and an inbound `/approve <id>` reply flips pending_approvals via gate.resolve.
"""
import io
import json

import pytest

from agents import gate


class FakeDB:
    """In-memory stand-in for agents.db covering the gate's reads, writes, and audit."""

    def __init__(self):
        self.approvals: dict[int, dict] = {}
        self._next_id = 1
        self.executed: list[tuple[str, tuple]] = []
        self.decisions: list[dict] = []

    def fetchone(self, conn, sql, params=()):
        s = " ".join(sql.lower().split())
        if "insert into pending_approvals" in s:
            row = {"id": self._next_id, "requested_by": params[0],
                   "action_type": params[1], "status": "pending"}
            self.approvals[row["id"]] = row
            self._next_id += 1
            return {"id": row["id"]}
        if "select status from pending_approvals" in s:
            row = self.approvals.get(params[0])
            return {"status": row["status"]} if row else None
        return None

    def execute(self, conn, sql, params=()):
        self.executed.append((sql, params))
        s = " ".join(sql.lower().split())
        if "update pending_approvals set status" in s:
            status, _by, approval_id = params
            if approval_id in self.approvals:
                self.approvals[approval_id]["status"] = status

    def log_decision(self, conn, **kwargs):
        self.decisions.append(kwargs)


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(gate, "db", db)
    return db


# --- outbound notify -----------------------------------------------------------

def test_wassist_send_noops_without_creds(monkeypatch):
    monkeypatch.setattr(gate, "_WA_KEY", None)
    monkeypatch.setattr(gate, "_WA_CONV", None)
    assert gate._wassist_send("hello") is None


def test_wassist_send_builds_whatsapp_payload(monkeypatch):
    monkeypatch.setattr(gate, "_WA_KEY", "secret-key")
    monkeypatch.setattr(gate, "_WA_CONV", "conv-123")
    monkeypatch.setattr(gate, "_WA_BASE", "https://backend.wassist.app")
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = req.headers
        captured["body"] = json.loads(req.data.decode())
        return io.BytesIO(json.dumps({"id": "wamid.42"}).encode())

    monkeypatch.setattr(gate.urllib.request, "urlopen", fake_urlopen)
    msg_id = gate._wassist_send("approval needed")

    assert msg_id == "wamid.42"
    assert captured["url"] == "https://backend.wassist.app/api/v1/conversations/conv-123/messages/"
    assert captured["body"] == {"message": "approval needed"}
    # urllib title-cases header keys.
    assert captured["headers"].get("X-api-key") == "secret-key"


def test_notify_prefers_wassist_over_telegram(monkeypatch):
    monkeypatch.setattr(gate, "_wassist_send", lambda text: "w1")
    monkeypatch.setattr(gate, "_telegram_send", lambda text: "t1")
    assert gate._notify("x") == ("w1", "wassist")


def test_notify_falls_back_to_telegram(monkeypatch):
    monkeypatch.setattr(gate, "_wassist_send", lambda text: None)
    monkeypatch.setattr(gate, "_telegram_send", lambda text: "t1")
    assert gate._notify("x") == ("t1", "telegram")


def test_notify_dashboard_only_when_unconfigured(monkeypatch):
    monkeypatch.setattr(gate, "_wassist_send", lambda text: None)
    monkeypatch.setattr(gate, "_telegram_send", lambda text: None)
    assert gate._notify("x") is None


# --- request_approval queues + audits -----------------------------------------

def test_request_approval_records_notifies_and_audits(fake_db, monkeypatch):
    monkeypatch.setattr(gate, "_notify", lambda text: ("w1", "wassist"))
    out = gate.request_approval(object(), "agent-finance", "spend",
                                {"amount": 25}, summary="Pay supplier")

    assert out == {"approval_id": 1, "notified": True, "channel": "wassist"}
    assert fake_db.approvals[1]["status"] == "pending"
    # external message ref persisted, and the action is on the audit log.
    assert any("telegram_msg_id" in sql for sql, _ in fake_db.executed)
    assert fake_db.decisions and fake_db.decisions[0]["action"] == "request_approval"
    assert fake_db.decisions[0]["approvals_requested"]["channel"] == "wassist"


def test_request_approval_dashboard_only_still_audits(fake_db, monkeypatch):
    monkeypatch.setattr(gate, "_notify", lambda text: None)
    out = gate.request_approval(object(), "agent-ceo", "licensing", {"step": "VLP"})

    assert out["notified"] is False and out["channel"] == "dashboard"
    assert not any("telegram_msg_id" in sql for sql, _ in fake_db.executed)
    assert fake_db.decisions[0]["approvals_requested"]["notified"] is False


def test_request_approval_coerces_unknown_action(fake_db, monkeypatch):
    monkeypatch.setattr(gate, "_notify", lambda text: None)
    gate.request_approval(object(), "agent-x", "buy_a_yacht", {})
    assert fake_db.decisions[0]["approvals_requested"]["action_type"] == "other"


# --- inbound parsing + resolution ---------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("/approve 5", ("approve", 5)),
    ("approve #7", ("approve", 7)),
    ("/reject 12", ("reject", 12)),
    ("reject 3", ("reject", 3)),
    ("yes 9", ("approve", 9)),
    ("no 4", ("reject", 4)),
    ("APPROVE 5", ("approve", 5)),
    ("hello there", None),
    ("/approve", None),
    ("", None),
])
def test_parse_command(text, expected):
    assert gate.parse_command(text) == expected


def test_handle_inbound_resolves_and_audits(fake_db):
    fake_db.approvals[5] = {"id": 5, "status": "pending"}
    reply = gate.handle_inbound(object(), "/approve 5", by="whatsapp")

    assert "approved" in reply
    assert fake_db.approvals[5]["status"] == "approved"
    update = [p for sql, p in fake_db.executed if "update pending_approvals set status" in sql.lower()]
    assert update and update[0][0] == "approved" and update[0][1] == "whatsapp"
    assert fake_db.decisions[-1]["action"] == "resolve_approval:approved"


def test_handle_inbound_ignores_non_command(fake_db):
    assert gate.handle_inbound(object(), "thanks!", by="whatsapp") is None
    assert not fake_db.executed


def test_handle_inbound_reports_already_resolved(fake_db):
    fake_db.approvals[8] = {"id": 8, "status": "approved"}
    reply = gate.handle_inbound(object(), "/reject 8", by="whatsapp")
    assert "already resolved" in reply
    assert fake_db.approvals[8]["status"] == "approved"
