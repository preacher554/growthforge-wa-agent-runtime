from __future__ import annotations

import subprocess
from textwrap import dedent


SYSTEM_CONTEXT = """
Kamu adalah Lia, AI WhatsApp frontdesk GrowthForge.
Lia adalah Pro Sales Receptionist: ramah, singkat, natural, dan membantu calon customer memahami GrowthForge.

GrowthForge adalah AI-native operations company yang membangun sistem operasional berbasis AI untuk bisnis.
Produk awal yang boleh kamu jelaskan:
- WA Agent Basic: AI receptionist untuk FAQ, jam buka, layanan, dan handoff ke admin.
- WA Agent Pro: AI sales receptionist untuk tanya kebutuhan, qualify lead, ringkas lead, dan handoff ke manusia.
- InstaGrow: sistem growth Instagram/short-form/social untuk riset, konten, eksperimen, dan operasi growth.
- Website/ops dashboard masih bertahap.

Batasan:
- Jangan mengarang harga custom.
- Jangan janji integrasi payment/CRM/Meta Ads/ongkir sebagai fitur default.
- Kalau calon customer minta custom, meeting, harga final, kontrak, atau integrasi kompleks, arahkan akan diteruskan ke tim.
- Jawab dalam Bahasa Indonesia santai-profesional.
- Jangan terlalu panjang. Maksimal 2-4 kalimat kecuali perlu.
""".strip()


def build_prompt(customer_text: str, history: list[dict]) -> str:
    hist_lines = []
    for row in history[-8:]:
        role = "Customer" if row.get("direction") == "inbound" else "Lia"
        hist_lines.append(f"{role}: {row.get('text','')}")
    history_text = "\n".join(hist_lines) if hist_lines else "Belum ada."
    return dedent(
        f"""
        {SYSTEM_CONTEXT}

        Riwayat chat singkat:
        {history_text}

        Pesan customer terbaru:
        {customer_text}

        Tulis hanya balasan WhatsApp dari Lia. Jangan pakai markdown berlebihan.
        """
    ).strip()


def fallback_reply(customer_text: str) -> str:
    return (
        "Halo Kak, aku Lia dari GrowthForge. Bisa dibantu ya — "
        "kalau boleh tahu, bisnis Kakak bergerak di bidang apa dan ingin dibantu di bagian WhatsApp Agent, InstaGrow, atau sistem AI operasional?"
    )


def generate_reply(customer_text: str, history: list[dict], provider: str, model: str, timeout: int = 160) -> str:
    prompt = build_prompt(customer_text, history)
    cmd = [
        "hermes",
        "chat",
        "-Q",
        "--provider",
        provider,
        "-m",
        model,
        "-q",
        prompt,
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    except Exception:
        return fallback_reply(customer_text)

    lines = [line.strip() for line in out.splitlines() if line.strip() and not line.startswith("session_id:")]
    reply = "\n".join(lines).strip()
    if not reply:
        return fallback_reply(customer_text)
    return reply[:1800]
