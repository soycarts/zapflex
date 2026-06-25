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
import re
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


def _notify(text: str) -> tuple[str, str] | None:
    """Push to the highest-priority configured channel; None if dashboard-only.

    Returns (message_id, channel) so the caller can record the external ref and
    audit which channel actually carried the approval.
    """
    msg_id = _wassist_send(text)
    if msg_id:
        return msg_id, "wassist"
    msg_id = _telegram_send(text)
    if msg_id:
        return msg_id, "telegram"
    return None


def request_approval(conn, requested_by: str, action_type: str, payload: dict,
                     summary: str = "", cycle: int | None = None) -> dict:
    """Record a pending approval, notify the human channel, and audit it."""
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
    sent = _notify(text)
    msg_id, channel = sent if sent else (None, "dashboard")
    if msg_id:
        # telegram_msg_id doubles as the generic external-channel message ref.
        db.execute(conn, "update pending_approvals set telegram_msg_id = %s where id = %s",
                   (msg_id, approval_id))
    db.log_decision(
        conn,
        agent=requested_by,
        action="request_approval",
        rationale=summary or json.dumps(payload),
        cycle=cycle,
        approvals_requested={
            "approval_id": approval_id, "action_type": action_type,
            "channel": channel, "notified": bool(msg_id),
        },
    )
    return {"approval_id": approval_id, "notified": bool(msg_id), "channel": channel}


def approval_status(conn, approval_id: int) -> str:
    row = db.fetchone(conn, "select status from pending_approvals where id = %s", (approval_id,))
    return row["status"] if row else "pending"


def resolve(conn, approval_id: int, decision: str, by: str = "human") -> str:
    """Resolve an approval from any inbound channel (Wassist/Telegram webhook or CLI)."""
    decision = "approved" if decision == "approve" else "rejected" if decision == "reject" else decision
    db.execute(
        conn,
        """update pending_approvals set status = %s, resolved_by = %s, resolved_at = now()
           where id = %s""",
        (decision, by, approval_id),
    )
    db.log_decision(
        conn,
        agent=by,
        action=f"resolve_approval:{decision}",
        rationale=f"approval #{approval_id} -> {decision}",
        approvals_requested={"approval_id": approval_id, "decision": decision},
    )
    return decision


_CMD_RE = re.compile(r"^\s*/?(approve|reject|yes|no)\b\s*#?\s*(\d+)?", re.IGNORECASE)
_DECISION_WORD = {"approve": "approve", "yes": "approve", "reject": "reject", "no": "reject"}


def parse_command(text: str) -> tuple[str, int] | None:
    """Parse an inbound reply into (decision, approval_id), or None if not a command.

    Accepts `/approve 5`, `approve #5`, `reject 5`, and the bare `yes 5` / `no 5`.
    """
    if not text:
        return None
    m = _CMD_RE.match(text)
    if not m or m.group(2) is None:
        return None
    return _DECISION_WORD[m.group(1).lower()], int(m.group(2))


def handle_inbound(conn, text: str, by: str = "human") -> str | None:
    """Resolve an approval from a webhook message; returns a reply string or None.

    The Wassist/Telegram inbound webhook hands the raw message text here. Unknown
    or non-command messages return None so the webhook can stay quiet.
    """
    parsed = parse_command(text)
    if not parsed:
        return None
    decision, approval_id = parsed
    if approval_status(conn, approval_id) != "pending":
        return f"approval #{approval_id} is already resolved."
    resolve(conn, approval_id, decision, by=by)
    verb = "approved" if decision == "approve" else "rejected"
    return f"approval #{approval_id} {verb}. The swarm will proceed accordingly."
