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
app = FastAPI(title="GrowthForge WA Agent Runtime", version="0.1.0")
store = Store(settings.database_url)
evolution = EvolutionClient(settings.evolution_base_url, settings.authentication_api_key)
notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_admin_chat_id)

HUMAN_RESUME_WINDOW = timedelta(hours=1)
HUMAN_HANDOFF_NOTIFY = (
    "Baik Kak, aku akan teruskan chat ini ke tim NusaAI.id kami. "
    "Tim kami aktif pada jam kerja 09.00–17.00 WIB — akan segera menghubungi Kakak ya 🙏"
)
_OUTBOUND_LEDGER_TTL = 60  # seconds — remember sent messages for dedup

_log = logging.getLogger("wa.agent.runtime")

# Idempotency: prevent duplicate webhook processing
_seen_ids: dict[str, str] = {}

# Outbound ledger: track AI-sent messages to detect echo
# Key: hash(instance + message_id), Value: (text, timestamp)
_outbound_ledger: dict[str, tuple[str, datetime]] = {}

# In-flight JIDs: JIDs currently being processed by AI reply tasks
# Any SEND_MESSAGE event from these JIDs during processing = AI echo
_outbound_pending: set[str] = set()

# Content-based dedup: key = hash(jid + text), value = timestamp
# Evolution sends different message_id for same content — message_id dedup alone is insufficient
_seen_content: dict[str, datetime] = {}

# Per-JID lock to prevent parallel processing of the same user's messages
_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _idempotency_key(instance: str, message_id: str) -> str:
    return hashlib.md5(f"{settings.hermes_model_provider}:{instance}:{message_id}".encode()).hexdigest()


def _clean_outbound_ledger():
    """Remove expired entries to prevent memory leak."""
    cutoff = _now_utc() - timedelta(seconds=_OUTBOUND_LEDGER_TTL * 2)
    expired = [k for k, (_, ts) in _outbound_ledger.items() if ts < cutoff]
    for k in expired:
        del _outbound_ledger[k]


@app.on_event("startup")
async def startup():
    store.ensure_schema()
    _log.info("WA Agent runtime started")

    # Start background heartbeat ping to dashboard
    asyncio.create_task(_heartbeat_loop())


async def _heartbeat_loop():
    """Send heartbeat to dashboard every 30 seconds."""
    dashboard_url = "http://43.157.225.184:3200/api/wa-agents?action=heartbeat"
    instance_name = settings.evolution_instance

    # Wait a few seconds for app to fully start
    await asyncio.sleep(5)

    while True:
        try:
            import aiohttp
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.post(dashboard_url, json={"instance": instance_name}) as resp:
                    if resp.status == 200:
                        _log.debug("Heartbeat sent to dashboard")
                    else:
                        _log.warning("Heartbeat failed: %d", resp.status)
        except Exception as e:
            _log.debug("Heartbeat error: %s", str(e)[:100])

        await asyncio.sleep(30)


async def _send_reply(instance_name: str, remote_jid: str, text: str) -> str:
    """Send reply via Evolution API. Returns Evolution message_id."""
    result = await evolution.send_text(instance_name, remote_jid, text)
    # Extract Evolution message_id from response
    msg_id = ""
    if isinstance(result, dict):
        key_data = result.get("key") or result.get("message") or {}
        if isinstance(key_data, dict):
            msg_id = key_data.get("id") or ""
        if not msg_id:
            msg_id = result.get("messageId") or result.get("id") or ""
    return str(msg_id)


async def _handle_incoming(text: str, message_id: str, trigger, from_me: bool = False) -> None:
    remote_jid = trigger.remote_jid  # type: ignore[attr-defined]
    instance_name = trigger.instance_name or settings.evolution_instance  # type: ignore[attr-defined]
    push_name = trigger.push_name  # type: ignore[attr-defined]

    tenant = store.get_tenant_by_instance(instance_name)
    conversation = store.upsert_conversation(tenant["id"], remote_jid, push_name)

    direction = "outbound" if from_me else "inbound"

    # Idempotency check: skip if already processed this message_id
    idem_key = _idempotency_key(instance_name, message_id)
    if store.message_exists(conversation["id"], message_id):
        _log.info("Idempotent skip: %s already in DB (direction=%s)", message_id, direction)
        return

    history = store.get_recent_messages(conversation["id"], limit=8)

    # For outbound messages: just record, don't process
    if from_me:
        store.insert_message(conversation["id"], message_id, direction, remote_jid, text, {"from_me": True})

        # Check if this matches our outbound ledger (AI echo)
        ledger_key = _idempotency_key(instance_name, message_id)
        if ledger_key in _outbound_ledger:
            _log.info("Outbound echo detected (AI-sent): %s — marked delivered", message_id)
            del _outbound_ledger[ledger_key]
            return

        # Check if this JID is currently being processed (in-flight AI reply)
        if remote_jid in _outbound_pending:
            _log.info("Outbound echo from in-flight JID %s — AI is currently replying", remote_jid)
            return

        # Not in ledger & not in-flight → human/admin takeover
        _log.info("Human/admin outbound detected from %s — AI pauses & notifies customer", remote_jid)
        store.set_conversation_state(conversation["id"], "human_active")
        store.create_handoff(
            conversation["id"],
            "human_outbound_detected",
            f"Admin/human mengirim manual dari HP: {text[:200]}",
        )
        # Notify customer that chat is being handed to human team
        try:
            notif = build_handoff_notification(
                customer_text=text, history=[], conversation=conversation,
                remote_jid=remote_jid, push_name=push_name,
                reason="human_outbound_detected",
            )
            await notifier.send(notif)
        except Exception:
            _log.exception("human_active notification failed")
        evo_id = await _send_reply(instance_name, remote_jid, HUMAN_HANDOFF_NOTIFY)
        store.insert_message(
            conversation["id"], "human-notify-" + message_id, "outbound", None,
            HUMAN_HANDOFF_NOTIFY, {"handoff": "human_takeover"},
        )
        if evo_id:
            ledger_key2 = _idempotency_key(instance_name, evo_id)
            _outbound_ledger[ledger_key2] = (HUMAN_HANDOFF_NOTIFY, _now_utc())
            _clean_outbound_ledger()
        return

    # === INBOUND PROCESSING (from_me=False) ===
    # Content-based dedup: skip if same text from same JID within 30s window
    content_key = hashlib.md5(f"{remote_jid}:{text}".encode()).hexdigest()
    last_seen = _seen_content.get(content_key)
    if last_seen and (_now_utc() - last_seen).total_seconds() < 30:
        _log.info("Content duplicate ignored: %s (%.0fs ago)", text[:40], (_now_utc() - last_seen).total_seconds())
        return
    _seen_content[content_key] = _now_utc()

    # Per-JID lock: only one processing task per user at a time
    async with _locks[remote_jid]:
        await _handle_inbound_locked(text, message_id, trigger, conversation, history, tenant, instance_name, remote_jid, push_name, direction)


async def _handle_inbound_locked(text, message_id, trigger, conversation, history, tenant, instance_name, remote_jid, push_name, direction):
    """Core inbound message processing — runs inside per-JID lock."""
    store.insert_message(conversation["id"], message_id, direction, remote_jid, text, None)

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
        store.set_conversation_state(conversation["id"], "waiting_human")
        store.create_handoff(conversation["id"], decision.reason, text)
        try:
            notif = build_handoff_notification(
                customer_text=text, history=history, conversation=conversation,
                remote_jid=remote_jid, push_name=push_name, reason=decision.reason,
            )
            await notifier.send(notif)
        except Exception:
            _log.exception("handoff notification failed")
        evo_id = await _send_reply(instance_name, remote_jid, reply)
        store.insert_message(conversation["id"], "handoff-" + message_id, "outbound", None, reply, {"handoff": decision.reason})
        if evo_id:
            ledger_key = _idempotency_key(instance_name, evo_id)
            _outbound_ledger[ledger_key] = (reply, _now_utc())
            _clean_outbound_ledger()
        return

    # Dedup: already replied recently
    if has_recent_reply(history, seconds=30):
        _log.info("Already replied recently, skipping")
        return

    # Mark JID as in-flight to prevent SEND_MESSAGE race
    _outbound_pending.add(remote_jid)
    try:
        try:
            reply = generate_reply(
                text, history,
                provider=settings.hermes_model_provider,
                model=settings.hermes_model,
                timeout=settings.hermes_timeout_seconds,
            )
        except Exception:
            _log.exception("generate_reply crashed; using fallback reply")
            reply = fallback_reply(text, history)

        # Handle multi-bubble reply (opening) or single reply
        if isinstance(reply, list):
            combined = "\n\n".join(reply)
            _log.info("Multi-bubble reply: %d bubbles", len(reply))
            for i, bubble in enumerate(reply):
                evo_id = await _send_reply(instance_name, remote_jid, bubble)
                if evo_id:
                    ledger_key = _idempotency_key(instance_name, evo_id)
                    _outbound_ledger[ledger_key] = (bubble, _now_utc())
                if i < len(reply) - 1:
                    await asyncio.sleep(0.5)
            store.insert_message(
                conversation["id"], "reply-" + message_id, "outbound", None, combined,
                {"provider": settings.hermes_model_provider, "model": settings.hermes_model, "bubbles": len(reply)},
            )
        else:
            _log.info("Reply: %s", reply[:100])
            evo_id = await _send_reply(instance_name, remote_jid, reply)
            store.insert_message(
                conversation["id"], "reply-" + message_id, "outbound", None, reply,
                {"provider": settings.hermes_model_provider, "model": settings.hermes_model},
            )
            if evo_id:
                ledger_key = _idempotency_key(instance_name, evo_id)
                _outbound_ledger[ledger_key] = (reply, _now_utc())
        _clean_outbound_ledger()
    finally:
        _outbound_pending.discard(remote_jid)

@app.post("/webhook/evolution")
async def webhook(request: Request):
    payload = await request.json()

    incoming = extract_message(payload)
    if not incoming.text.strip() or not incoming.remote_jid.endswith("@s.whatsapp.net"):
        return {"ok": True, "ignored": "not_processable"}

    jid = incoming.remote_jid
    mid = incoming.message_id
    instance = incoming.instance_name or settings.evolution_instance

    # Idempotency dedup (in-memory, fast path)
    if _seen_ids.get(jid) == mid:
        _log.info("Duplicate webhook ignored: %s", mid)
        return {"ok": True, "ignored": "duplicate"}
    _seen_ids[jid] = mid

    # Handle outbound echo (fromMe=True)
    if incoming.from_me:
        asyncio.create_task(_handle_incoming(incoming.text.strip(), mid, incoming, from_me=True))
        return {"ok": True, "handled": "outbound_echo"}

    # Handle real inbound
    asyncio.create_task(_handle_incoming(incoming.text.strip(), mid, incoming, from_me=False))

    return {"ok": True, "queued": True}


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "wa-agent-runtime",
        "instance": settings.evolution_instance,
        "wa_agents_enabled": settings.wa_agents_enabled,
    }


@app.exception_handler(Exception)
async def on_error(request: Request, exc: Exception):
    _log.exception("unhandled error")
    return JSONResponse(500, {"ok": False, "error": type(exc).__name__, "message": str(exc)[:300]})
