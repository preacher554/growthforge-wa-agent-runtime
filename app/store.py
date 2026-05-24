from __future__ import annotations

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


class Store:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def get_tenant_by_instance(self, instance_name: str) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                "select * from tenants where whatsapp_instance = %s",
                (instance_name,),
            ).fetchone()
            if not row:
                raise RuntimeError(f"No tenant configured for instance {instance_name}")
            return dict(row)

    def upsert_conversation(self, tenant_id: str, remote_jid: str, customer_name: str | None) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                """
                insert into conversations (tenant_id, remote_jid, customer_name, last_message_at, updated_at)
                values (%s, %s, %s, now(), now())
                on conflict (tenant_id, remote_jid) do update set
                  customer_name = coalesce(excluded.customer_name, conversations.customer_name),
                  last_message_at = now(),
                  updated_at = now()
                returning *
                """,
                (tenant_id, remote_jid, customer_name),
            ).fetchone()
            return dict(row)

    def get_recent_messages(self, conversation_id: str, limit: int = 8) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                select direction, text, created_at
                from messages
                where conversation_id = %s
                order by created_at desc
                limit %s
                """,
                (conversation_id, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def insert_message(self, conversation_id: str, evolution_message_id: str | None, direction: str, sender_jid: str | None, text: str, raw: dict | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into messages (conversation_id, evolution_message_id, direction, sender_jid, text, raw)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (conversation_id, evolution_message_id) do nothing
                """,
                (conversation_id, evolution_message_id, direction, sender_jid, text, Jsonb(raw) if raw is not None else None),
            )

    def set_conversation_state(self, conversation_id: str, state: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "update conversations set state=%s, updated_at=now() where id=%s",
                (state, conversation_id),
            )

    def create_handoff(self, conversation_id: str, reason: str, summary: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into handoff_events (conversation_id, reason, summary, status)
                values (%s, %s, %s, 'open')
                """,
                (conversation_id, reason, summary),
            )
