from __future__ import annotations

import httpx


class EvolutionClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def send_text(self, instance: str, number_or_jid: str, text: str) -> dict:
        number = number_or_jid.replace("@s.whatsapp.net", "")
        payload = {"number": number, "text": text}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base_url}/message/sendText/{instance}",
                headers={"apikey": self.api_key},
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
