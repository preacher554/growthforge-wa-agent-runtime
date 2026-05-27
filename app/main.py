from __future__ import annotations

from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.brain import generate_reply
from app.config import load_settings
from app.evolution import extract_message
from app.evolution_client import EvolutionClient
from app.notifier import TelegramNotifier, build_handoff_notification
from app.policy import classify_handoff, should_resume_from_admin_command
from app.store import Store

settings = load_settings()
app = FastAPI(title="Nusavox Lia Runtime", version="0.1.0")
store = Store(settings.database_url)
evolution = EvolutionClient(settings.evolution_base_url, settings.authentication_api_key)
notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_admin_chat_id)

HUMAN_RESUME_WINDOW = timedelta(hours=1)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def _mark_incoming_read(incoming) -> None:
    if not incoming.message_id:
        return
    try:
        await evolution.mark_message_as_read(incoming.instance_name, incoming.remote_jid, incoming.message_id)
    except Exception as e:
        import logging
        logging.getLogger("lia.runtime").warning("mark_message_as_read failed: %s", e)


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "lia-runtime",
        "instance": settings.evolution_instance,
        "wa_agents_enabled": settings.wa_agents_enabled,
    }


@app.post("/webhook/evolution")
async def evolution_webhook(request: Request):
    payload = await request.json()
    event = str(payload.get("event") or "")
    event_l = event.lower()
    if event and "message" not in event_l and "send" not in event_l:
        return {"ok": True, "ignored": event}

    if not settings.wa_agents_enabled:
        return {"ok": True, "ignored": "wa_agents_disabled"}

    incoming = extract_message(payload)
    if not incoming.text.strip() or not incoming.remote_jid.endswith("@s.whatsapp.net"):
        return {"ok": True, "ignored": "not_processable"}

    tenant = store.get_tenant_by_instance(incoming.instance_name or settings.evolution_instance)
    conversation = store.upsert_conversation(tenant["id"], incoming.remote_jid, incoming.push_name)

    # Dedupe must run before inserting the inbound message. Otherwise every new
    # message is immediately found again and incorrectly ignored.
    if store.message_exists(conversation["id"], incoming.message_id):
        return {"ok": True, "reply": "duplicate_ignored"}

    history = store.get_recent_messages(conversation["id"], limit=8)

    if incoming.from_me:
        store.insert_message(
            conversation["id"],
            incoming.message_id or None,
            "outbound",
            None,
            incoming.text,
            incoming.raw,
        )
        if conversation.get("state") in {"waiting_human", "human_active"}:
            store.set_conversation_state(conversation["id"], "human_active")
        return {"ok": True, "human_takeover": True}

    store.insert_message(
        conversation["id"],
        incoming.message_id or None,
        "inbound",
        incoming.remote_jid,
        incoming.text,
        incoming.raw,
    )

    resume_context_note = None
    if conversation.get("state") in {"waiting_human", "human_active"}:
        if should_resume_from_admin_command(incoming.text):
            store.set_conversation_state(conversation["id"], "ai_active")
        elif conversation.get("state") == "human_active":
            last_human_at = store.get_last_human_outbound_at(conversation["id"])
            if last_human_at and _now_utc() - last_human_at >= HUMAN_RESUME_WINDOW:
                store.set_conversation_state(conversation["id"], "ai_active")
                resume_context_note = (
                    "Percakapan ini baru di-resume otomatis setelah admin/human Nusavox mengambil alih. "
                    "Balas sebagai Lia/WA Agent yang aktif kembali. Jangan ulang dari awal; lanjutkan natural dari konteks chat. "
                    "Jika cocok, awali singkat dengan 'Aku Lia bantu lanjut ya Kak.'"
                )
            else:
                return {"ok": True, "state": conversation.get("state"), "reply": "paused"}
        elif conversation.get("state") == "waiting_human":
            last_handoff_at = store.get_last_handoff_at(conversation["id"])
            if last_handoff_at and _now_utc() - last_handoff_at >= HUMAN_RESUME_WINDOW:
                store.set_conversation_state(conversation["id"], "ai_active")
                resume_context_note = (
                    "Percakapan ini sebelumnya sempat handoff ke human dan sekarang sudah melewati window 1 jam. "
                    "Balas sebagai Lia yang aktif kembali. Jangan ulang dari awal; lanjutkan natural dari konteks chat."
                )
            else:
                return {"ok": True, "state": conversation.get("state"), "reply": "paused"}
        else:
            return {"ok": True, "state": conversation.get("state"), "reply": "paused"}

    decision = classify_handoff(incoming.text)
    if decision.should_handoff:
        await _mark_incoming_read(incoming)
        reply = f"Baik Kak, permintaan kamu akan diteruskan ke tim Nusavox. Tim kami aktif pada jam kerja 09.00–17.00 WIB, akan segera kami hubungi ya."
        store.create_handoff(conversation["id"], decision.reason, incoming.text)
        store.set_conversation_state(conversation["id"], "waiting_human")
        try:
            notification = build_handoff_notification(
                customer_text=incoming.text,
                history=history,
                conversation=conversation,
                remote_jid=incoming.remote_jid,
                push_name=incoming.push_name,
                reason=decision.reason,
            )
            await notifier.send(notification)
        except Exception as e:
            import logging
            logging.getLogger("lia.runtime").error("telegram handoff notification failed: %s", e)
        try:
            await evolution.send_text(incoming.instance_name, incoming.remote_jid, reply)
            store.insert_message(conversation["id"], f"lia-handoff-{incoming.message_id}", "outbound", None, reply, {"handoff": decision.reason})
        except Exception as e:
            import logging
            logging.getLogger("lia.runtime").error("send_text failed (handoff): %s", e)
        return {"ok": True, "handoff": True}

    await _mark_incoming_read(incoming)
    reply = generate_reply(
        incoming.text,
        history,
        provider=settings.hermes_model_provider,
        model=settings.hermes_model,
        timeout=settings.hermes_timeout_seconds,
        context_note=resume_context_note,
    )
    try:
        await evolution.send_text(incoming.instance_name, incoming.remote_jid, reply)
        store.insert_message(conversation["id"], f"lia-reply-{incoming.message_id}", "outbound", None, reply, {"provider": settings.hermes_model_provider, "model": settings.hermes_model})
    except Exception as e:
        import logging
        logging.getLogger("lia.runtime").error("send_text failed (reply): %s", e)
    return {"ok": True, "handoff": False}


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"ok": False, "error": type(exc).__name__, "message": str(exc)[:300]})
