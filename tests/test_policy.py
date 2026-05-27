from app.policy import classify_handoff, should_resume_from_admin_command


def test_classify_handoff_for_price_custom_request():
    result = classify_handoff("Bisa integrasi payment gateway dan kasih harga custom?")

    assert result.should_handoff is True
    assert "custom" in result.reason.lower() or "harga" in result.reason.lower()


def test_classify_no_handoff_for_basic_intro_question():
    result = classify_handoff("GrowthForge itu apa?")

    assert result.should_handoff is False


def test_classify_no_handoff_for_customer_word():
    result = classify_handoff("Sementara ini chat perhari cuma 1-2 customer")

    assert result.should_handoff is False


def test_resume_command_detection():
    assert should_resume_from_admin_command("/resume") is True
    assert should_resume_from_admin_command("/lanjut") is True
    assert should_resume_from_admin_command("oke lanjutkan ai") is True
    assert should_resume_from_admin_command("halo kak") is False
