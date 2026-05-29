from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
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
_BUFFER_SECONDS = 4
_DEDUP_TTL = 30  # seconds — ignore same content from same JID within this window

# Per-JID message buffer
_buf: dict[str, list[tuple[str, str, datetime]]] = defaultdict(list)
_tasks: dict[str, asyncio.Task] = {}
# Per-JID lock to prevent parallel processing
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
# Content-based dedup: key = hash(jid + text), value = timestamp
_seen_content: dict[str, datetime] = {}
_log = logging.getLogger("aulia.runtime")

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


async def _process_buffer(jid: str, trigger) -> None:
    """Wait for buffer window then process combined messages."""
    try:
        await asyncio.sleep(_BUFFER_SECONDS)
        batch = _buf.pop(jid, [])
        _tasks.pop(jid, None)
        if not batch:
            return
        combined = " ".join(t for _, t, _ in sorted(batch, key=lambda x: x[2]))
        first_id = batch[0][0]
        _log.info("Processing buffered msg: %s (from %d bubbles)", combined[:80], len(batch))

        # Per-JID lock: only one processing task per user at a time
        async with _locks[jid]:
            await _handle_incoming(combined, first_id, trigger)
    except asyncio.CancelledError:
        pass
    except Exception:
        _log.exception("process_buffer error")


async def _handle_incoming(text: str, message_id: str, trigger) -> None:
    remote_jid = trigger.remote_jid  # type: ignore[attr-defined]
    instance_name = trigger.instance_name or settings.evolution_instance  # type: ignore[attr-defined]
    push_name = trigger.push_name  # type: ignore[attr-defined]

    tenant = store.get_tenant_by_instance(instance_name)
    conversation = store.upsert_conversation(tenant["id"], remote_jid, push_name)

    if store.message_exists(conversation["id"], message_id):
        _log.info("Duplicate message ignored: %s", message_id)
        return

    # Also check: did we ALREADY send an outbound reply for this conversation recently?
    # This catches same message_id arriving via different Evolution events
    history = store.get_recent_messages(conversation["id"], limit=8)
    if has_recent_reply(history, seconds=_BUFFER_SECONDS):
        _log.info("Already replied recently (DB check), skipping")
        return

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
        store.set_conversation_state(conversation["id"], "waiting_human")
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
    if has_recent_reply(history, seconds=_BUFFER_SECONDS):
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
    event = str(payload.get("event") or "")
    event_l = event.lower()
    if event and "message" not in event_l and "send" not in event_l:
        return {"ok": True, "ignored": event}

    if not settings.wa_agents_enabled:
        return {"ok": True, "ignored": "wa_agents_disabled"}

    incoming = extract_message(payload)
    if not incoming.text.strip() or not incoming.remote_jid.endswith("@s.whatsapp.net"):
        return {"ok": True, "ignored": "not_processable"}

    jid = incoming.remote_jid
    text = incoming.text.strip()
    now = _now_utc()

    # Content-based dedup: skip if same text from same JID within TTL window
    content_key = hashlib.md5(f"{jid}:{text}".encode()).hexdigest()
    last_seen = _seen_content.get(content_key)
    if last_seen and (now - last_seen).total_seconds() < _DEDUP_TTL:
        _log.info("Duplicate content ignored: %s (%.0fs ago)", text[:40], (now - last_seen).total_seconds())
        return {"ok": True, "ignored": "duplicate_content"}
    _seen_content[content_key] = now

    # Cleanup old dedup entries (prevent memory leak)
    if len(_seen_content) > 1000:
        cutoff = now - timedelta(seconds=_DEDUP_TTL * 2)
        _seen_content = {k: v for k, v in _seen_content.items() if v > cutoff}

    _buf[jid].append((incoming.message_id, text, now))

    # Cancel previous timer for this user
    if jid in _tasks:
        _tasks[jid].cancel()

    # Schedule delayed processing
    _tasks[jid] = asyncio.create_task(_process_buffer(jid, incoming))

    return {"ok": True, "buffered": True}


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
