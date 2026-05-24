from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IncomingMessage:
    instance_name: str
    remote_jid: str
    from_me: bool
    message_id: str
    push_name: str | None
    text: str
    raw: dict[str, Any]

    @property
    def should_process(self) -> bool:
        return bool(self.text.strip()) and not self.from_me and self.remote_jid.endswith("@s.whatsapp.net")


def _message_text(message: dict[str, Any]) -> str:
    if not message:
        return ""
    if isinstance(message.get("conversation"), str):
        return message["conversation"]
    extended = message.get("extendedTextMessage") or {}
    if isinstance(extended.get("text"), str):
        return extended["text"]
    image = message.get("imageMessage") or {}
    if isinstance(image.get("caption"), str):
        return image["caption"]
    video = message.get("videoMessage") or {}
    if isinstance(video.get("caption"), str):
        return video["caption"]
    buttons = message.get("buttonsResponseMessage") or {}
    if isinstance(buttons.get("selectedDisplayText"), str):
        return buttons["selectedDisplayText"]
    list_response = message.get("listResponseMessage") or {}
    if isinstance(list_response.get("title"), str):
        return list_response["title"]
    return ""


def extract_message(payload: dict[str, Any]) -> IncomingMessage:
    data = payload.get("data") or payload
    key = data.get("key") or {}
    message = data.get("message") or {}
    return IncomingMessage(
        instance_name=payload.get("instance") or data.get("instance") or "",
        remote_jid=key.get("remoteJid") or "",
        from_me=bool(key.get("fromMe")),
        message_id=key.get("id") or data.get("messageId") or "",
        push_name=data.get("pushName") or data.get("push_name"),
        text=_message_text(message).strip(),
        raw=payload,
    )
