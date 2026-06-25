"""Modal web endpoints for the approval gate's inbound channels (Wassist + Telegram).

Wassist BYOA POSTs {message, phone_number, reply_callback} to /wassist when the
founder replies on WhatsApp; a `/approve <id>` or `/reject <id>` reply flips the
matching pending_approvals row via gate.handle_inbound. Telegram replies/buttons
resolve the same way at /telegram, so oversight works on whichever channel is live.

Deploy:
    modal deploy app/modal_app.py
Then register the printed URL with your Wassist BYOA agent
(POST /api/v1/agents/byoa/ {"webhookUrl": "<url>/wassist"}) and, for Telegram,
point setWebhook at "<url>/telegram". The "zapflex" Modal secret must carry
DATABASE_URL (and, for outbound notify, the WASSIST_*/TELEGRAM_* vars).
"""
import modal

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("fastapi[standard]", "psycopg[binary]", "python-dotenv")
    .add_local_python_source("agents")
)

app = modal.App("zapflex-gate")


@app.function(image=image, secrets=[modal.Secret.from_name("zapflex")])
@modal.asgi_app()
def web():
    from fastapi import FastAPI, Request

    from agents import db, gate

    api = FastAPI()
    _hint = "Reply `/approve <id>` or `/reject <id>` to act on a pending approval."

    def _resolve(text: str, by: str) -> str | None:
        conn = db.connect()
        try:
            return gate.handle_inbound(conn, text, by=by)
        finally:
            conn.close()

    @api.post("/wassist")
    async def wassist_inbound(request: Request):
        body = await request.json()
        reply = _resolve(str((body or {}).get("message") or ""), by="whatsapp")
        return {"content": reply or _hint}

    @api.post("/telegram")
    async def telegram_inbound(request: Request):
        body = await request.json() or {}
        msg = body.get("message") or body.get("edited_message") or {}
        cb = body.get("callback_query") or {}
        text = msg.get("text") or cb.get("data") or ""
        return {"ok": True, "reply": _resolve(str(text), by="telegram")}

    @api.get("/health")
    async def health():
        return {"ok": True}

    return api
