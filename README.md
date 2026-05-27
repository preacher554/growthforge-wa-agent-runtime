# GrowthForge WA Agent Runtime

Runtime backend for Lia, GrowthForge's WhatsApp AI frontdesk.

## Local run

```bash
cd /root/repos/whatsapp-agent-architect-runtime
. .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 3300
```

## Health

```bash
curl http://127.0.0.1:3300/health
```

## Env sources

The runtime reads:

- `/root/services/evolution-growthforge/.env`
- `/root/services/evolution-growthforge/supabase.env`

Do not commit secrets.
