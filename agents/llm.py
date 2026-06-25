"""LLM decider: drives the same scoped tools when an inference endpoint is set.

The runtime swarm needs its own inference endpoint (separate from any build-time Cursor
or Claude subscription). Point it at any OpenAI-compatible endpoint via env — Modal's
inference endpoints, OpenRouter, etc.:

    INFERENCE_BASE_URL     = https://<your-modal-endpoint>/v1   (no default for Modal)
    INFERENCE_API_KEY      = the token (sent as 'Authorization: Bearer <key>')
    INFERENCE_MODEL        = the model slug (CEO can use a stronger one, see runner)
    INFERENCE_EXTRA_HEADERS= optional JSON of extra headers (e.g. Modal proxy auth)

With no endpoint set, agents/runner.py uses the deterministic scripted policies, so the
company still runs. On any error here the runner also falls back to scripted — the LLM
is the brain, the scripted policy is the brainstem that keeps it alive.
"""
from __future__ import annotations

import json
import os
import urllib.request

from dotenv import load_dotenv

load_dotenv()

_KEY = os.environ.get("INFERENCE_API_KEY")
_BASE = os.environ.get("INFERENCE_BASE_URL", "").rstrip("/")
try:
    _EXTRA_HEADERS = json.loads(os.environ.get("INFERENCE_EXTRA_HEADERS", "") or "{}")
except Exception:
    _EXTRA_HEADERS = {}


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if _KEY:
        h["Authorization"] = f"Bearer {_KEY}"
    h.update(_EXTRA_HEADERS)
    return h

# Compact tool catalogue the model may call, with the exact arg names per tool.
TOOL_ARGS = {
    "create_task": {"title": "str", "category": "str", "priority": "int 1-5",
                    "assigned_to": "str (agent to own it, e.g. trading)"},
    "update_task": {"task_id": "int", "status": "todo|doing|done|cancelled", "result": "str"},
    "request_approval": {"action_type": "spend|pricing_change|external_send|licensing",
                         "summary": "str", "payload": "object"},
    "learn_routine": {"handle": "str"},
    "set_forecast_model": {"handle": "str", "model": "naive|seasonal|learned"},
    "book_revenue": {},
    "book_cost": {"amount_gbp": "number", "note": "str"},
    "trip_kill_switch": {"reason": "str"},
    "onboard_customer": {"profile": "object {handle, household{annual_kwh,has_solar,solar_kwp,has_ev,load_volatility}, battery{capacity_kwh,max_charge_kw,max_discharge_kw}, forecast_model}"},
    "post_community": {"body": "str", "author_name": "str"},
    "open_ticket": {"customer_id": "int|null", "subject": "str", "body": "str", "priority": "int"},
    "answer_ticket": {"ticket_id": "int", "resolution": "str"},
    "escalate_ticket": {"ticket_id": "int", "reason": "str"},
    "resolve_connection": {"connection_id": "int"},
}


def enabled() -> bool:
    # An endpoint is what matters; some self-hosted Modal endpoints need no key.
    return bool(_BASE)


def _catalog(allowed: set[str]) -> str:
    lines = []
    for name in sorted(allowed):
        lines.append(f"- {name}({json.dumps(TOOL_ARGS.get(name, {}))})")
    return "\n".join(lines)


def decide(soul: str, model: str, context: str, allowed: set[str]) -> dict:
    """Ask the model for a rationale and a list of tool calls. Returns
    {"rationale": str, "actions": [{"tool": str, "args": {...}}]}. Raises on failure."""
    system = (
        f"{soul}\n\n"
        "You act by calling tools. You are scoped to ONLY these tools:\n"
        f"{_catalog(allowed)}\n\n"
        "Respond with STRICT JSON only, no prose, of the form:\n"
        '{"rationale": "<one or two sentences>", '
        '"actions": [{"tool": "<name>", "args": {<args>}}]}\n'
        "Take at most 3 actions. If nothing is warranted this cycle, return an empty actions list."
    )
    body = json.dumps({
        "model": model,
        "temperature": 0.4,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": context},
        ],
    }).encode()
    req = urllib.request.Request(f"{_BASE}/chat/completions", data=body, headers=_headers())
    with urllib.request.urlopen(req, timeout=40) as resp:
        out = json.loads(resp.read().decode())
    content = out["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.split("```", 2)[1]
        if content.startswith("json"):
            content = content[4:]
        content = content.strip().rstrip("`").strip()
    parsed = json.loads(content)
    parsed.setdefault("actions", [])
    parsed.setdefault("rationale", "")
    return parsed


def ping(model: str | None = None) -> str:
    """One-shot connectivity check against the configured endpoint."""
    model = model or os.environ.get("INFERENCE_MODEL", "")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: zapflex online"}],
        "max_tokens": 16, "temperature": 0,
    }).encode()
    req = urllib.request.Request(f"{_BASE}/chat/completions", data=body, headers=_headers())
    with urllib.request.urlopen(req, timeout=40) as resp:
        out = json.loads(resp.read().decode())
    return out["choices"][0]["message"]["content"].strip()


if __name__ == "__main__":
    print(f"enabled={enabled()} base={_BASE!r} model={os.environ.get('INFERENCE_MODEL')!r} "
          f"key={'set' if _KEY else 'none'} extra_headers={list(_EXTRA_HEADERS)}")
    if enabled():
        try:
            print("ping ->", ping())
        except Exception as e:
            print("ping FAILED:", e)
