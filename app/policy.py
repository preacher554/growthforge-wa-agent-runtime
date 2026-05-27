from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class HandoffDecision:
    should_handoff: bool
    reason: str = ""


HANDOFF_KEYWORDS = [
    "harga custom",
    "custom",
    "integrasi",
    "payment gateway",
    "qris",
    "invoice",
    "kontrak",
    "meeting",
    "demo",
    "diskon",
    "refund",
    "komplain",
    "marah",
    "legal",
]

RESUME_COMMANDS = {"/resume", "/lanjut", "/release", "/ai"}


def classify_handoff(text: str) -> HandoffDecision:
    lowered = text.lower()
    for keyword in HANDOFF_KEYWORDS:
        if re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", lowered):
            return HandoffDecision(True, f"Customer asked about {keyword}; human admin should review.")
    return HandoffDecision(False, "")


def should_resume_from_admin_command(text: str) -> bool:
    lowered = text.strip().lower()
    if lowered in RESUME_COMMANDS:
        return True
    return "lanjutkan ai" in lowered or "ai lanjut" in lowered
