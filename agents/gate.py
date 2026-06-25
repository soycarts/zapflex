"""The human approval gate: high-stakes actions wait here for a human tap.

Any spend, pricing change, external send, or licensing move is written to
pending_approvals and surfaced to the human. The dashboard always shows the
pending queue; if an external channel is configured it also pushes a message so
the founder's phone buzzes on stage. Resolution (approved|rejected) flows back
into pending_approvals and the requesting agent proceeds only on 'approved'.

The notifier is pluggable and degrades gracefully. Channels, in priority order:
  1. Wassist (sponsor) — a WhatsApp message into the founder's conversation.
  2. Telegram — a bot message.
  3. Dashboard-only — the approval still renders in the pending queue.
So the oversight story holds with or without any external channel configured.
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.parse

from dotenv import load_dotenv

from agents import db

load_dotenv()

_TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
_TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID")

# Wassist: WhatsApp approval channel. Message the deployed assistant once to create
# a conversation, then put its id in WASSIST_CONVERSATION_ID.
_WA_KEY = os.environ.get("WASSIST_API_KEY")
_WA_CONV = os.environ.get("WASSIST_CONVERSATION_ID")
_WA_BASE = os.environ.get("WASSIST_BASE_URL", "https://backend.wassist.app").rstrip("/")

GATED_ACTIONS = {"spend", "pricing_change", "external_send", "licensing"}


def _wassist_send(text: str) -> str | None:
    """Send a plain-text WhatsApp message into the founder's Wassist conversation."""
    if not (_WA_KEY and _WA_CONV):
        return None
    try:
        url = f"{_WA_BASE}/api/v1/conversations/{_WA_CONV}/messages/"
        data = json.dumps({"message": text}).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"X-API-Key": _WA_KEY, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
        return str(payload.get("id") or payload.get("message_id") or "wassist")
    except Exception:
        return None


def _telegram_send(text: str) -> str | None:
    if not (_TG_TOKEN and _TG_CHAT):
        return None
    try:
        url = f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": _TG_CHAT,
            "text": text,
            "parse_mode": "Markdown",
        }).encode()
        with urllib.request.urlopen(url, data=data, timeout=8) as resp:
            payload = json.loads(resp.read().decode())
        if payload.get("ok"):
            return str(payload["result"]["message_id"])
    except Exception:
        return None
    return None


def _notify(text: str) -> str | None:
    """Push to the highest-priority configured channel; None if dashboard-only."""
    return _wassist_send(text) or _telegram_send(text)


def request_approval(conn, requested_by: str, action_type: str, payload: dict,
                     summary: str = "") -> dict:
    """Record a pending approval and notify the human channel. Returns the row id."""
    if action_type not in GATED_ACTIONS:
        action_type = "other"
    row = db.fetchone(
        conn,
        """insert into pending_approvals (requested_by, action_type, payload, status)
           values (%s,%s,%s,'pending') returning id""",
        (requested_by, action_type, json.dumps(payload)),
    )
    approval_id = row["id"]
    text = (
        f"*zapflex approval needed*\n"
        f"Agent: `{requested_by}`\n"
        f"Type: `{action_type}`\n"
        f"{summary or json.dumps(payload)}\n\n"
        f"Reply `/approve {approval_id}` or `/reject {approval_id}`."
    )
    msg_id = _notify(text)
    if msg_id:
        # telegram_msg_id doubles as the generic external-channel message ref.
        db.execute(conn, "update pending_approvals set telegram_msg_id = %s where id = %s",
                   (msg_id, approval_id))
    return {"approval_id": approval_id, "notified": bool(msg_id)}


def approval_status(conn, approval_id: int) -> str:
    row = db.fetchone(conn, "select status from pending_approvals where id = %s", (approval_id,))
    return row["status"] if row else "pending"


def resolve(conn, approval_id: int, decision: str, by: str = "human") -> None:
    """Used by the Telegram webhook (or a manual override) to resolve an approval."""
    decision = "approved" if decision == "approve" else "rejected" if decision == "reject" else decision
    db.execute(
        conn,
        """update pending_approvals set status = %s, resolved_by = %s, resolved_at = now()
           where id = %s""",
        (decision, by, approval_id),
    )
