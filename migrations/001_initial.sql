-- GrowthForge WA Agent Runtime schema v1

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

insert into tenants (tenant_key, business_name, package, whatsapp_instance, admin_private_jid)
values ('growthforge_lia', 'GrowthForge', 'pro', 'lia-growthforge', null)
on conflict (tenant_key) do update set
  business_name = excluded.business_name,
  package = excluded.package,
  whatsapp_instance = excluded.whatsapp_instance,
  updated_at = now();

create index if not exists idx_conversations_state on conversations(state);
create index if not exists idx_messages_conversation_created on messages(conversation_id, created_at);
