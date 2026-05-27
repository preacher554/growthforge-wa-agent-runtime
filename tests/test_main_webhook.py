from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture(autouse=True)
def enable_wa_agents_for_tests(monkeypatch):
    monkeypatch.setattr(main.settings, "wa_agents_enabled", True)


class FakeConn:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params):
        message_id = params[1]
        self._row = {"id": "existing"} if message_id in self.store.message_ids else None
        return self

    def fetchone(self):
        return self._row


class FakeStore:
    def __init__(self):
        self.message_ids = set()
        self.inserted = []
        self.handoffs = []
        self.state_changes = []
        self.conversation_state = "ai_active"
        self.last_human_outbound_at = None
        self.last_handoff_at = None

    def connect(self):
        return FakeConn(self)

    def get_tenant_by_instance(self, instance_name):
        return {"id": "tenant-1"}

    def upsert_conversation(self, tenant_id, remote_jid, customer_name):
        return {"id": "conversation-1", "state": self.conversation_state, "customer_name": customer_name}

    def get_recent_messages(self, conversation_id, limit=8):
        return []

    def message_exists(self, conversation_id, evolution_message_id):
        return bool(evolution_message_id and evolution_message_id in self.message_ids)

    def get_last_human_outbound_at(self, conversation_id):
        return self.last_human_outbound_at

    def get_last_handoff_at(self, conversation_id):
        return self.last_handoff_at

    def insert_message(self, conversation_id, evolution_message_id, direction, sender_jid, text, raw=None):
        self.inserted.append({"id": evolution_message_id, "direction": direction, "text": text})
        if evolution_message_id:
            self.message_ids.add(evolution_message_id)

    def create_handoff(self, conversation_id, reason, summary=None):
        self.handoffs.append({"conversation_id": conversation_id, "reason": reason, "summary": summary})

    def set_conversation_state(self, conversation_id, state):
        self.state_changes.append({"conversation_id": conversation_id, "state": state})


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)
        return {"ok": True}


class FakeEvolution:
    def __init__(self):
        self.sent = []
        self.read = []

    async def send_text(self, instance, number_or_jid, text):
        self.sent.append({"instance": instance, "number": number_or_jid, "text": text})
        return {"ok": True}

    async def mark_message_as_read(self, instance, remote_jid, message_id):
        self.read.append({"instance": instance, "remote_jid": remote_jid, "message_id": message_id})
        return {"ok": True}


def test_webhook_dedupe_checks_before_insert_and_replies(monkeypatch):
    fake_store = FakeStore()
    fake_evolution = FakeEvolution()
    monkeypatch.setattr(main, "store", fake_store)
    monkeypatch.setattr(main, "evolution", fake_evolution)
    monkeypatch.setattr(main, "notifier", FakeNotifier())
    monkeypatch.setattr(main, "generate_reply", lambda *args, **kwargs: "Balasan Lia")

    client = TestClient(main.app)
    payload = {
        "event": "messages.upsert",
        "instance": "lia-growthforge",
        "data": {
            "key": {"remoteJid": "628111222333@s.whatsapp.net", "fromMe": False, "id": "MSG-NEW"},
            "pushName": "Budi",
            "message": {"conversation": "Saya mau tanya WA Agent"},
        },
    }

    response = client.post("/webhook/evolution", json=payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "handoff": False}
    assert fake_store.inserted[0]["id"] == "MSG-NEW"
    assert fake_store.inserted[0]["direction"] == "inbound"
    assert fake_store.inserted[1]["id"] == "lia-reply-MSG-NEW"
    assert fake_store.inserted[1]["direction"] == "outbound"
    assert fake_evolution.sent == [
        {"instance": "lia-growthforge", "number": "628111222333@s.whatsapp.net", "text": "Balasan Lia"}
    ]


def test_webhook_duplicate_message_is_ignored(monkeypatch):
    fake_store = FakeStore()
    fake_store.message_ids.add("MSG-DUP")
    fake_evolution = FakeEvolution()
    monkeypatch.setattr(main, "store", fake_store)
    monkeypatch.setattr(main, "evolution", fake_evolution)
    monkeypatch.setattr(main, "notifier", FakeNotifier())
    monkeypatch.setattr(main, "generate_reply", lambda *args, **kwargs: "SHOULD_NOT_RUN")

    client = TestClient(main.app)
    payload = {
        "event": "messages.upsert",
        "instance": "lia-growthforge",
        "data": {
            "key": {"remoteJid": "628111222333@s.whatsapp.net", "fromMe": False, "id": "MSG-DUP"},
            "message": {"conversation": "Halo"},
        },
    }

    response = client.post("/webhook/evolution", json=payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "reply": "duplicate_ignored"}
    assert fake_store.inserted == []
    assert fake_evolution.sent == []


def test_webhook_ignores_everything_when_wa_agents_disabled(monkeypatch):
    fake_store = FakeStore()
    fake_evolution = FakeEvolution()
    monkeypatch.setattr(main, "store", fake_store)
    monkeypatch.setattr(main, "evolution", fake_evolution)
    monkeypatch.setattr(main.settings, "wa_agents_enabled", False)
    monkeypatch.setattr(main, "generate_reply", lambda *args, **kwargs: "SHOULD_NOT_RUN")

    client = TestClient(main.app)
    payload = {
        "event": "messages.upsert",
        "instance": "lia-growthforge",
        "data": {
            "key": {"remoteJid": "628111222333@s.whatsapp.net", "fromMe": False, "id": "MSG-DISABLED"},
            "message": {"conversation": "Halo, mau tanya"},
        },
    }

    response = client.post("/webhook/evolution", json=payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "ignored": "wa_agents_disabled"}
    assert fake_store.inserted == []
    assert fake_evolution.sent == []


def test_webhook_handoff_notifies_telegram_operator(monkeypatch):
    fake_store = FakeStore()
    fake_evolution = FakeEvolution()
    fake_notifier = FakeNotifier()
    monkeypatch.setattr(main, "store", fake_store)
    monkeypatch.setattr(main, "evolution", fake_evolution)
    monkeypatch.setattr(main, "notifier", fake_notifier)

    client = TestClient(main.app)
    payload = {
        "event": "messages.upsert",
        "instance": "lia-growthforge",
        "data": {
            "key": {"remoteJid": "628111222333@s.whatsapp.net", "fromMe": False, "id": "MSG-HANDOFF"},
            "pushName": "Supri",
            "message": {"conversation": "Saya mau harga custom dan meeting"},
        },
    }

    response = client.post("/webhook/evolution", json=payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "handoff": True}
    assert fake_store.handoffs
    assert fake_store.state_changes == [{"conversation_id": "conversation-1", "state": "waiting_human"}]
    assert len(fake_notifier.sent) == 1
    assert "Chief, Lia butuh handoff manusia" in fake_notifier.sent[0]
    assert "Supri" in fake_notifier.sent[0]
    assert "628111222333" in fake_notifier.sent[0]
    assert "Saya mau harga custom dan meeting" in fake_notifier.sent[0]
    assert fake_evolution.sent



def test_human_sent_message_sets_human_active_without_ai_reply(monkeypatch):
    fake_store = FakeStore()
    fake_store.conversation_state = "waiting_human"
    fake_evolution = FakeEvolution()
    monkeypatch.setattr(main, "store", fake_store)
    monkeypatch.setattr(main, "evolution", fake_evolution)
    monkeypatch.setattr(main, "notifier", FakeNotifier())
    monkeypatch.setattr(main, "generate_reply", lambda *args, **kwargs: "SHOULD_NOT_RUN")

    client = TestClient(main.app)
    payload = {
        "event": "send.message",
        "instance": "lia-growthforge",
        "data": {
            "key": {"remoteJid": "628111222333@s.whatsapp.net", "fromMe": True, "id": "MSG-HUMAN"},
            "message": {"conversation": "Siap Kak, saya bantu proses dealnya ya."},
        },
    }

    response = client.post("/webhook/evolution", json=payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "human_takeover": True}
    assert fake_store.inserted == [{"id": "MSG-HUMAN", "direction": "outbound", "text": "Siap Kak, saya bantu proses dealnya ya."}]
    assert fake_store.state_changes == [{"conversation_id": "conversation-1", "state": "human_active"}]
    assert fake_evolution.sent == []


def test_customer_message_during_human_window_stays_paused(monkeypatch):
    fake_store = FakeStore()
    fake_store.conversation_state = "human_active"
    fake_store.last_human_outbound_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    fake_evolution = FakeEvolution()
    monkeypatch.setattr(main, "store", fake_store)
    monkeypatch.setattr(main, "evolution", fake_evolution)
    monkeypatch.setattr(main, "notifier", FakeNotifier())
    monkeypatch.setattr(main, "generate_reply", lambda *args, **kwargs: "SHOULD_NOT_RUN")

    client = TestClient(main.app)
    payload = {
        "event": "messages.upsert",
        "instance": "lia-growthforge",
        "data": {
            "key": {"remoteJid": "628111222333@s.whatsapp.net", "fromMe": False, "id": "MSG-CUSTOMER-PAUSED"},
            "message": {"conversation": "Oke Kak"},
        },
    }

    response = client.post("/webhook/evolution", json=payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "state": "human_active", "reply": "paused"}
    assert fake_evolution.sent == []


def test_customer_message_after_one_hour_resumes_lia(monkeypatch):
    fake_store = FakeStore()
    fake_store.conversation_state = "human_active"
    fake_store.last_human_outbound_at = datetime.now(timezone.utc) - timedelta(hours=1, minutes=1)
    fake_evolution = FakeEvolution()
    captured = {}
    monkeypatch.setattr(main, "store", fake_store)
    monkeypatch.setattr(main, "evolution", fake_evolution)
    monkeypatch.setattr(main, "notifier", FakeNotifier())

    def fake_generate_reply(*args, **kwargs):
        captured["context_note"] = kwargs.get("context_note")
        return "Aku Lia bantu lanjut ya Kak. Ada yang bisa aku bantu lagi?"

    monkeypatch.setattr(main, "generate_reply", fake_generate_reply)

    client = TestClient(main.app)
    payload = {
        "event": "messages.upsert",
        "instance": "lia-growthforge",
        "data": {
            "key": {"remoteJid": "628111222333@s.whatsapp.net", "fromMe": False, "id": "MSG-CUSTOMER-RESUME"},
            "message": {"conversation": "Kalau mau tambah fitur bisa?"},
        },
    }

    response = client.post("/webhook/evolution", json=payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "handoff": False}
    assert {"conversation_id": "conversation-1", "state": "ai_active"} in fake_store.state_changes
    assert "di-resume otomatis" in captured["context_note"]
    assert fake_evolution.sent == [
        {"instance": "lia-growthforge", "number": "628111222333@s.whatsapp.net", "text": "Aku Lia bantu lanjut ya Kak. Ada yang bisa aku bantu lagi?"}
    ]



def test_customer_message_after_one_hour_from_waiting_human_resumes_lia(monkeypatch):
    fake_store = FakeStore()
    fake_store.conversation_state = "waiting_human"
    fake_store.last_handoff_at = datetime.now(timezone.utc) - timedelta(hours=1, minutes=1)
    fake_evolution = FakeEvolution()
    captured = {}
    monkeypatch.setattr(main, "store", fake_store)
    monkeypatch.setattr(main, "evolution", fake_evolution)
    monkeypatch.setattr(main, "notifier", FakeNotifier())

    def fake_generate_reply(*args, **kwargs):
        captured["context_note"] = kwargs.get("context_note")
        return "Aku Lia bantu lanjut ya Kak. Untuk instalasi nanti dibantu setup awalnya."

    monkeypatch.setattr(main, "generate_reply", fake_generate_reply)

    client = TestClient(main.app)
    payload = {
        "event": "messages.upsert",
        "instance": "lia-growthforge",
        "data": {
            "key": {"remoteJid": "628111222333@s.whatsapp.net", "fromMe": False, "id": "MSG-WAITING-RESUME"},
            "message": {"conversation": "Bos, ini nanti gimana instalasi nya?"},
        },
    }

    response = client.post("/webhook/evolution", json=payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "handoff": False}
    assert {"conversation_id": "conversation-1", "state": "ai_active"} in fake_store.state_changes
    assert "handoff" in captured["context_note"]
    assert fake_evolution.sent == [
        {"instance": "lia-growthforge", "number": "628111222333@s.whatsapp.net", "text": "Aku Lia bantu lanjut ya Kak. Untuk instalasi nanti dibantu setup awalnya."}
    ]
