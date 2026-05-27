from app.brain import build_prompt, fallback_reply, generate_reply


def test_opening_greeting_introduces_lia_and_asks_name_without_model_call():
    reply = generate_reply("Halo", [], provider="unused", model="unused", timeout=1)

    assert "Lia" in reply
    assert "nama" in reply.lower()
    assert "GrowthForge" in reply


def test_opening_prompt_requires_receptionist_discovery_flow():
    prompt = build_prompt("Halo", [])

    assert "kenalkan diri sebagai Lia" in prompt
    assert "tanya nama" in prompt.lower()
    assert "bisnis" in prompt.lower()


def test_fallback_with_existing_history_does_not_restart_discovery():
    history = [
        {"direction": "inbound", "text": "Aku Rina, aku punya bisnis hijab", "raw": {}},
        {"direction": "outbound", "text": "Siap Kak Rina, salam kenal ya.", "raw": {}},
    ]

    reply = fallback_reply("Klo WA juga sama?", history)

    assert "Aku bantu lanjut" in reply
    assert "WA Agent" in reply
    assert "Boleh aku tahu nama" not in reply
    assert "bisnisnya bergerak" not in reply
