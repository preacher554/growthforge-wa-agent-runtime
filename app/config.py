from __future__ import annotations

import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    supabase_url: str = ""
    supabase_rest_url: str = ""
    supabase_service_role_key: str = ""
    database_url: str = ""

    authentication_api_key: str = ""
    evolution_base_url: str = "http://127.0.0.1:8080"
    evolution_instance: str = "lia-growthforge"

    runtime_host: str = "0.0.0.0"
    runtime_port: int = 3300
    admin_private_jid: str = ""
    telegram_bot_token: str = ""
    telegram_admin_chat_id: str = ""

    wa_agents_enabled: bool = False

    hermes_model_provider: str = "openai-codex"
    hermes_model: str = "gpt-5.2"
    hermes_timeout_seconds: int = 160

    model_config = SettingsConfigDict(env_file=(), extra="ignore")


def load_dotenv_file(path: str) -> None:
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def load_settings() -> Settings:
    load_dotenv_file("/root/.hermes/.env")
    load_dotenv_file("/root/services/evolution-growthforge/.env")
    load_dotenv_file("/root/services/evolution-growthforge/supabase.env")
    os.environ.setdefault("EVOLUTION_BASE_URL", "http://127.0.0.1:8080")
    if os.getenv("TELEGRAM_HOME_CHANNEL"):
        os.environ.setdefault("TELEGRAM_ADMIN_CHAT_ID", os.environ["TELEGRAM_HOME_CHANNEL"])
    return Settings()
