from app.brain import build_prompt, generate_reply


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
