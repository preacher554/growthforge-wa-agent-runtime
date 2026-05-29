from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.brain import generate_reply, has_recent_reply
from app.config import load_settings
from app.evolution import extract_message
from app.evolution_client import EvolutionClient
from app.notifier import TelegramNotifier, build_handoff_notification
from app.policy import classify_handoff, should_resume_from_admin_command
from app.store import Store

settings = load_settings()
app = FastAPI(title="NusaAI Aulia Runtime", version="0.1.0")
store = Store(settings.database_url)
evolution = EvolutionClient(settings.evolution_base_url, settings.authentication_api_key)
notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_admin_chat_id)

HUMAN_RESUME_WINDOW = timedelta(hours=1)
_log = logging.getLogger("aulia.runtime")

# Simple dedup: store last message_id per JID (in-memory)
_seen_ids: dict[str, str] = {}


@app.on_event("startup")
async def startup():
    store.ensure_schema()
    _log.info("Aulia runtime started")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def _send_reply(instance_name: str, remote_jid: str, reply: str) -> None:
    try:
        await evolution.send_text(instance_name, remote_jid, reply)
    except Exception:
        _log.exception("send_reply failed")


async def _handle_incoming(text: str, message_id: str, trigger) -> None:
    remote_jid = trigger.remote_jid  # type: ignore[attr-defined]
    instance_name = trigger.instance_name or settings.evolution_instance  # type: ignore[attr-defined]
    push_name = trigger.push_name  # type: ignore[attr-defined]

    tenant = store.get_tenant_by_instance(instance_name)
    conversation = store.upsert_conversation(tenant["id"], remote_jid, push_name)

    if store.message_exists(conversation["id"], message_id):
        _log.info("Duplicate message ignored: %s", message_id)
        return

    history = store.get_recent_messages(conversation["id"], limit=8)
    store.insert_message(conversation["id"], message_id, "inbound", remote_jid, text, None)

    # Resume logic
    state = conversation.get("state")
    if state in {"waiting_human", "human_active"}:
        if should_resume_from_admin_command(text):
            store.set_conversation_state(conversation["id"], "ai_active")
        elif state == "human_active":
            last = store.get_last_human_outbound_at(conversation["id"])
            if last and _now_utc() - last >= HUMAN_RESUME_WINDOW:
                store.set_conversation_state(conversation["id"], "ai_active")
            else:
                return
        elif state == "waiting_human":
            last_ho = store.get_last_handoff_at(conversation["id"])
            if last_ho and _now_utc() - last_ho >= HUMAN_RESUME_WINDOW:
                store.set_conversation_state(conversation["id"], "ai_active")
            else:
                return

    decision = classify_handoff(text)
    if decision.should_handoff:
        reply = "Baik Kak, permintaan kamu akan diteruskan ke tim NusaAI. Tim kami aktif pada jam kerja 09.00–17.00 WIB, akan segera kami hubungi ya."
        store.create_handoff(conversation["id"], decision.reason, text)
        try:
            notif = build_handoff_notification(
                customer_text=text, history=history, conversation=conversation,
                remote_jid=remote_jid, push_name=push_name, reason=decision.reason,
            )
            await notifier.send(notif)
        except Exception:
            _log.exception("handoff notification failed")
        await _send_reply(instance_name, remote_jid, reply)
        store.insert_message(conversation["id"], "handoff-" + message_id, "outbound", None, reply, {"handoff": decision.reason})
        return

    # Dedup: already replied recently
    if has_recent_reply(history, seconds=10):
        _log.info("Already replied recently, skipping")
        return

    reply = generate_reply(
        text, history,
        provider=settings.hermes_model_provider,
        model=settings.hermes_model,
        timeout=settings.hermes_timeout_seconds,
    )
    _log.info("Reply: %s", reply[:100])
    await _send_reply(instance_name, remote_jid, reply)
    store.insert_message(
        conversation["id"], "reply-" + message_id, "outbound", None, reply,
        {"provider": settings.hermes_model_provider, "model": settings.hermes_model},
    )


@app.post("/webhook/evolution")
async def webhook(request: Request):
    payload = await request.json()

    incoming = extract_message(payload)
    if not incoming.text.strip() or not incoming.remote_jid.endswith("@s.whatsapp.net"):
        return {"ok": True, "ignored": "not_processable"}

    # Simple dedup by message_id
    mid = incoming.message_id
    jid = incoming.remote_jid
    if _seen_ids.get(jid) == mid:
        _log.info("Duplicate message_id ignored: %s", mid)
        return {"ok": True, "ignored": "duplicate"}
    _seen_ids[jid] = mid

    # Process immediately (no buffer)
    asyncio.create_task(_handle_incoming(incoming.text.strip(), mid, incoming))

    return {"ok": True, "queued": True}


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "aulia-runtime",
        "instance": settings.evolution_instance,
        "wa_agents_enabled": settings.wa_agents_enabled,
    }


@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    _log.exception("unhandled error")
    return JSONResponse(500, {"ok": False, "error": type(exc).__name__, "message": str(exc)[:300]})
