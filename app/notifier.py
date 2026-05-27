from __future__ import annotations

from typing import Any

import httpx


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def send(self, text: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
            )
            resp.raise_for_status()
            return resp.json()


def _phone_from_jid(jid: str) -> str:
    return (jid or "").replace("@s.whatsapp.net", "") or "-"


def build_handoff_notification(
    *,
    customer_text: str,
    history: list[dict],
    conversation: dict,
    remote_jid: str,
    push_name: str | None,
    reason: str,
) -> str:
    name = conversation.get("customer_name") or push_name or "Unknown"
    phone = _phone_from_jid(remote_jid)
    transcript_lines: list[str] = []
    for row in history[-6:]:
        role = "Customer" if row.get("direction") == "inbound" else "Lia"
        text = str(row.get("text") or "").strip().replace("\n", " ")
        if text:
            transcript_lines.append(f"- {role}: {text[:220]}")
    if customer_text.strip():
        transcript_lines.append(f"- Customer: {customer_text.strip()[:220]}")

    transcript = "\n".join(transcript_lines) if transcript_lines else "- Belum ada riwayat singkat."
    return (
        "Chief, Lia butuh handoff manusia.\n\n"
        f"Nama/customer: {name}\n"
        f"Nomor WA: {phone}\n"
        f"State: waiting_human\n"
        f"Reason: {reason}\n\n"
        "Ringkasan chat terakhir:\n"
        f"{transcript}\n\n"
        "Aksi yang disarankan: follow-up dari nomor Nusavox yang sama. "
        "Lia sudah pause auto-reply untuk customer ini."
    )
