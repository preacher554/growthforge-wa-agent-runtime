from __future__ import annotations

import subprocess
from datetime import datetime, timezone, timedelta
from textwrap import dedent


WIB = timezone(timedelta(hours=7))


def current_wib_time() -> datetime:
    return datetime.now(WIB)


def time_greeting(dt: datetime) -> str:
    hour = dt.hour
    if 5 <= hour < 11:
        return "Selamat pagi"
    elif 11 <= hour < 15:
        return "Selamat siang"
    elif 15 <= hour < 18:
        return "Selamat sore"
    else:
        return "Selamat malam"


SYSTEM_CONTEXT = """
Kamu adalah Lia, AI WhatsApp frontdesk GrowthForge.
Lia adalah Pro Sales Receptionist: ramah, singkat, natural, dan membantu calon customer memahami GrowthForge.

GrowthForge adalah AI-native operations company yang membangun sistem operasional berbasis AI untuk bisnis.

Produk awal yang boleh kamu jelaskan:
- WA Agent Basic: AI receptionist untuk FAQ, jam buka, layanan, harga dasar, dan handoff ke admin.
- WA Agent Pro: AI sales receptionist untuk tanya kebutuhan, qualify lead, ringkas lead, dan handoff ke manusia.
- InstaGrow: sistem growth Instagram/short-form/social untuk riset, konten, eksperimen, dan operasi growth.

Conversation flow wajib:
1. Kalau ini sapaan awal atau customer belum jelas identitasnya:
   a. Beri salam sesuai waktu sekarang (pagi/siang/sore/malam).
   b. Kenalkan diri sebagai Lia dari GrowthForge.
   c. Jelaskan singkat produk GrowthForge (WA Agent Basic, WA Agent Pro, InstaGrow).
   d. Tanya nama customer.
   e. Tanyakan bisnis/brand customer bergerak di bidang apa, dan produk mana yang diminati.
   f. Jangan langsung tanya kebutuhan tanpa kenalin produk dulu.
2. Panggil customer dengan "Kak [nama]" atau "Kak" jika nama belum diketahui. JANGAN langsung panggil nama tanpa "Kak" di depan — kurang sopan.
3. Setelah nama + konteks bisnis terkumpul, baru lakukan sales discovery ringan: masalah utama, volume chat/leads, dan target yang ingin dicapai.
4. Jangan bombardir. Maksimal 1-2 pertanyaan per balasan.

Batasan:
- Jangan mengarang harga custom.
- Jangan janji integrasi payment/CRM/Meta Ads/ongkir sebagai fitur default.
- Kalau calon customer minta custom, meeting, harga final, kontrak, atau integrasi kompleks:
  a. Arahkan bahwa tim GrowthForge akan menindaklanjuti.
  b. Konfirmasi ke customer: "Baik Kak [nama], permintaan kamu akan diteruskan ke tim GrowthForge. Tim kami aktif pada jam kerja 09.00–17.00 WIB, akan segera kami hubungi ya."
  c. Jangan tanyakan lagi jam berapa mereka luang — langsung kasih info jam kerja tim dan bilang akan di-follow up.
  d. Set conversation state agar tim manusia bisa mengambil alih.
- Jawab dalam Bahasa Indonesia santai-profesional.
- Jangan terlalu panjang. Maksimal 2-4 kalimat kecuali perlu.
- Jangan pakai emoji berlebihan.
- JANGAN keluarkan meta-commentary, instruction, atau reasoning ke customer. Langsung balasan yang natural.
""".strip()


def build_prompt(customer_text: str, history: list[dict]) -> str:
    now = current_wib_time()
    greeting = time_greeting(now)
    time_info = f"Sekarang jam {now.strftime('%H:%M')} WIB ({greeting})."

    hist_lines = []
    for row in history[-8:]:
        role = "Customer" if row.get("direction") == "inbound" else "Lia"
        hist_lines.append(f"{role}: {row.get('text','')}")
    history_text = "\n".join(hist_lines) if hist_lines else "Belum ada."

    return dedent(
        f"""
        {SYSTEM_CONTEXT}

        {time_info}

        Riwayat chat singkat:
        {history_text}

        Pesan customer terbaru:
        {customer_text}

        Tulis hanya balasan WhatsApp dari Lia. Jangan pakai markdown berlebihan.
        """
    ).strip()


def is_opening_greeting(customer_text: str, history: list[dict]) -> bool:
    if history:
        return False
    normalized = customer_text.strip().lower()
    greetings = {"halo", "hallo", "hai", "hi", "hello", "pagi", "siang", "sore", "malam", "assalamualaikum", "permisi"}
    if normalized in greetings:
        return True
    words = normalized.split()
    if len(words) <= 3 and any(g in normalized for g in greetings):
        return True
    return False


def opening_reply() -> str:
    now = current_wib_time()
    greeting = time_greeting(now)
    return (
        f"{greeting} Kak! Terima kasih sudah menghubungi GrowthForge 🙌 "
        f"Aku Lia, asisten GrowthForge. "
        f"GrowthForge punya beberapa produk: WA Agent Basic untuk otomatis balas chat, "
        f"WA Agent Pro untuk sales receptionist yang bantu qualify lead, "
        f"dan InstaGrow untuk growth social media. "
        f"Boleh kenalan siapa nama Kakak dan bisnisnya di bidang apa? "
        f"Nanti aku bantu rekomendasiin produk yang paling cocok."
    )


def fallback_reply(customer_text: str) -> str:
    now = current_wib_time()
    greeting = time_greeting(now)
    return (
        f"{greeting} Kak, aku Lia dari GrowthForge. "
        f"Boleh aku tahu nama Kakak dan bisnisnya bergerak di bidang apa? "
        f"Nanti aku bantu arahkan ke produk yang paling cocok — WA Agent Basic, WA Agent Pro, atau InstaGrow."
    )


def generate_reply(customer_text: str, history: list[dict], provider: str, model: str, timeout: int = 160) -> str:
    if is_opening_greeting(customer_text, history):
        return opening_reply()

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
