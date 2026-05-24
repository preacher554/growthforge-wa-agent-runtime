from app.evolution import extract_message


def test_extract_message_from_messages_upsert_payload():
    payload = {
        "event": "messages.upsert",
        "instance": "lia-growthforge",
        "data": {
            "key": {
                "remoteJid": "628111222333@s.whatsapp.net",
                "fromMe": False,
                "id": "ABC123",
            },
            "pushName": "Budi",
            "message": {"conversation": "Halo, GrowthForge bisa bantu WhatsApp agent?"},
            "messageTimestamp": 1770000000,
        },
    }

    msg = extract_message(payload)

    assert msg.instance_name == "lia-growthforge"
    assert msg.remote_jid == "628111222333@s.whatsapp.net"
    assert msg.from_me is False
    assert msg.message_id == "ABC123"
    assert msg.push_name == "Budi"
    assert msg.text == "Halo, GrowthForge bisa bantu WhatsApp agent?"


def test_extract_message_ignores_from_me():
    payload = {
        "instance": "lia-growthforge",
        "data": {
            "key": {"remoteJid": "628111222333@s.whatsapp.net", "fromMe": True, "id": "OUT1"},
            "message": {"conversation": "outbound"},
        },
    }

    assert extract_message(payload).should_process is False


def test_extract_message_supports_extended_text():
    payload = {
        "instance": "lia-growthforge",
        "data": {
            "key": {"remoteJid": "628111222333@s.whatsapp.net", "fromMe": False, "id": "EXT1"},
            "message": {"extendedTextMessage": {"text": "Mau tanya paket Pro"}},
        },
    }

    assert extract_message(payload).text == "Mau tanya paket Pro"
