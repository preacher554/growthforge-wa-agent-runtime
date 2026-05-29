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

    def upsert_tenant(self, tenant_key: str, business_name: str, package: str, whatsapp_instance: str, admin_private_jid: str | None = None) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                """
                insert into tenants (tenant_key, business_name, package, whatsapp_instance, admin_private_jid)
                values (%s, %s, %s, %s, %s)
                on conflict (tenant_key) do update set
                  business_name = excluded.business_name,
                  package = excluded.package,
                  whatsapp_instance = excluded.whatsapp_instance,
                  admin_private_jid = coalesce(excluded.admin_private_jid, tenants.admin_private_jid),
                  updated_at = now()
                returning *
                """,
                (tenant_key, business_name, package, whatsapp_instance, admin_private_jid),
            ).fetchone()
            return dict(row)

    def ensure_schema(self) -> None:
        """Create tables if they don't exist (idempotent)."""
        schema_sql = """
        create extension if not exists pgcrypto;

        create table if not exists tenants (
          id uuid primary key default gen_random_uuid(),
          tenant_key text not null unique,
          business_name text not null,
          package text not null check (package in ('basic', 'pro', 'custom')),
          whatsapp_instance text not null,
          admin_private_jid text,
          ai_enabled boolean not null default true,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now()
        );

        create table if not exists conversations (
          id uuid primary key default gen_random_uuid(),
          tenant_id uuid not null references tenants(id) on delete cascade,
          remote_jid text not null,
          customer_name text,
          state text not null default 'ai_active' check (state in ('ai_active', 'waiting_human', 'human_active', 'resolved')),
          last_message_at timestamptz,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now(),
          unique (tenant_id, remote_jid)
        );

        create table if not exists messages (
          id uuid primary key default gen_random_uuid(),
          conversation_id uuid not null references conversations(id) on delete cascade,
          evolution_message_id text,
          direction text not null check (direction in ('inbound', 'outbound', 'system')),
          sender_jid text,
          text text not null default '',
          raw jsonb,
          created_at timestamptz not null default now(),
          unique (conversation_id, evolution_message_id)
        );

        create table if not exists handoff_events (
          id uuid primary key default gen_random_uuid(),
          conversation_id uuid not null references conversations(id) on delete cascade,
          reason text not null,
          summary text,
          status text not null default 'open' check (status in ('open', 'notified', 'resolved', 'cancelled')),
          created_at timestamptz not null default now(),
          resolved_at timestamptz
        );

        create table if not exists lead_summaries (
          id uuid primary key default gen_random_uuid(),
          conversation_id uuid not null references conversations(id) on delete cascade,
          lead_status text not null default 'unknown' check (lead_status in ('hot', 'warm', 'cold', 'unknown')),
          intent text,
          need text,
          summary text not null,
          created_at timestamptz not null default now()
        );

        create index if not exists idx_conversations_state on conversations(state);
        create index if not exists idx_messages_conversation_created on messages(conversation_id, created_at);
        """
        with self.connect() as conn:
            conn.execute(schema_sql)
            conn.commit()

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
                select direction, text, raw, created_at
                from messages
                where conversation_id = %s
                order by created_at desc
                limit %s
                """,
                (conversation_id, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def message_exists(self, conversation_id: str, evolution_message_id: str | None) -> bool:
        if not evolution_message_id:
            return False
        with self.connect() as conn:
            row = conn.execute(
                "select id from messages where conversation_id = %s and evolution_message_id = %s",
                (conversation_id, evolution_message_id),
            ).fetchone()
            return bool(row)

    def get_last_human_outbound_at(self, conversation_id: str):
        with self.connect() as conn:
            row = conn.execute(
                """
                select created_at
                from messages
                where conversation_id = %s
                  and direction = 'outbound'
                  and raw #>> '{key,fromMe}' = 'true'
                order by created_at desc
                limit 1
                """,
                (conversation_id,),
            ).fetchone()
            return row["created_at"] if row else None

    def get_last_handoff_at(self, conversation_id: str):
        with self.connect() as conn:
            row = conn.execute(
                """
                select created_at
                from handoff_events
                where conversation_id = %s
                order by created_at desc
                limit 1
                """,
                (conversation_id,),
            ).fetchone()
            return row["created_at"] if row else None

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
