from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.brain import generate_reply
from app.config import load_settings
from app.evolution import extract_message
from app.evolution_client import EvolutionClient
from app.policy import classify_handoff, should_resume_from_admin_command
from app.store import Store

settings = load_settings()
app = FastAPI(title="GrowthForge Lia Runtime", version="0.1.0")
store = Store(settings.database_url)
evolution = EvolutionClient(settings.evolution_base_url, settings.authentication_api_key)


@app.get("/health")
def health():
    return {"ok": True, "service": "lia-runtime", "instance": settings.evolution_instance}


@app.post("/webhook/evolution")
async def evolution_webhook(request: Request):
    payload = await request.json()
    event = str(payload.get("event") or "")
    if event and "messages" not in event.lower():
        return {"ok": True, "ignored": event}

    incoming = extract_message(payload)
    if not incoming.should_process:
        return {"ok": True, "ignored": "not_processable"}

    tenant = store.get_tenant_by_instance(incoming.instance_name or settings.evolution_instance)
    conversation = store.upsert_conversation(tenant["id"], incoming.remote_jid, incoming.push_name)
    store.insert_message(
        conversation["id"],
        incoming.message_id or None,
        "inbound",
        incoming.remote_jid,
        incoming.text,
        incoming.raw,
    )

    if conversation.get("state") in {"waiting_human", "human_active"}:
        if should_resume_from_admin_command(incoming.text):
            store.set_conversation_state(conversation["id"], "ai_active")
        else:
            return {"ok": True, "state": conversation.get("state"), "reply": "paused"}

    decision = classify_handoff(incoming.text)
    if decision.should_handoff:
        reply = "Baik Kak, untuk bagian itu aku bantu teruskan ke tim GrowthForge ya supaya jawabannya lebih tepat. Mohon tunggu sebentar."
        store.create_handoff(conversation["id"], decision.reason, incoming.text)
        store.set_conversation_state(conversation["id"], "waiting_human")
        await evolution.send_text(incoming.instance_name, incoming.remote_jid, reply)
        store.insert_message(conversation["id"], f"lia-handoff-{incoming.message_id}", "outbound", None, reply, {"handoff": decision.reason})
        return {"ok": True, "handoff": True}

    history = store.get_recent_messages(conversation["id"], limit=8)
    reply = generate_reply(
        incoming.text,
        history,
        provider=settings.hermes_model_provider,
        model=settings.hermes_model,
        timeout=settings.hermes_timeout_seconds,
    )
    await evolution.send_text(incoming.instance_name, incoming.remote_jid, reply)
    store.insert_message(conversation["id"], f"lia-reply-{incoming.message_id}", "outbound", None, reply, {"provider": settings.hermes_model_provider, "model": settings.hermes_model})
    return {"ok": True, "handoff": False}


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"ok": False, "error": type(exc).__name__, "message": str(exc)[:300]})
