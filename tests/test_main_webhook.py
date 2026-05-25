from fastapi.testclient import TestClient

from app import main


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

    def connect(self):
        return FakeConn(self)

    def get_tenant_by_instance(self, instance_name):
        return {"id": "tenant-1"}

    def upsert_conversation(self, tenant_id, remote_jid, customer_name):
        return {"id": "conversation-1", "state": self.conversation_state, "customer_name": customer_name}

    def get_recent_messages(self, conversation_id, limit=8):
        return []

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

    async def send_text(self, instance, number_or_jid, text):
        self.sent.append({"instance": instance, "number": number_or_jid, "text": text})
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
