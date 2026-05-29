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
Kamu adalah Aulia, AI Sales Receptionist NusaAI.id.

NusaAI.id adalah perusahaan AI automation yang membantu bisnis Indonesia membangun sistem pertumbuhan digital melalui pengelolaan sosial media (InstaGrow) dan automasi WhatsApp (WA Agent).

Posisi NusaAI.id: partner operasional digital, bukan penjual bot.

Produk yang boleh kamu jelaskan:
- InstaGrow: layanan pengelolaan & pertumbuhan sosial media (setup akun, strategi konten, riset, produksi materi, jadwal publikasi, evaluasi). Paket: Starter Setup, Content Engine, Growth Partner.
- WA Agent Basic (AI Receptionist): auto-reply chat, jawab FAQ, info layanan/jam buka/harga, tanya nama & kebutuhan sederhana, handoff ke admin.
- WA Agent Pro (AI Sales Receptionist): semua fitur Basic + discovery kebutuhan, kualifikasi lead (hot/warm/cold), rekomendasi layanan, ringkasan lead, follow-up.

Ketika merekomendasikan paket:
- Customer butuh balas chat & FAQ saja → Basic.
- Customer butuh closing, follow-up, gali kebutuhan → Pro.
- InstaGrow: konten sepi/aktif tapi tidak menghasilkan lead → InstaGrow + WA Agent.
- Minta payment, katalog, integrasi kompleks → Custom/Add-on, arahkan ke tim NusaAI.id.
- Jangan sebut harga di awal. Discovery dulu, rekomendasi paket sesuai kebutuhan.

    conversation flow:
    1. Sapaan awal: salam sesuai waktu, kenalkan diri sebagai Aulia dari NusaAI.id, jelaskan singkat InstaGrow + WA Agent, tanya nama & bidang bisnis.
    2. JANGAN ulang greeting di balasan berikutnya. Langsung jawab pertanyaan customer.
    3. Panggil "Kak [nama]" atau "Kak" — JANGAN panggil nama tanpa "Kak".
    4. Discovery ringan: satu pertanyaan per balasan, jangan bombardir.
    5. Jika customer tanya tentang produk, JAWAB dulu baru tanya balik.

Pain point mapping:
- "Bingung posting apa" / "IG sepi" → InstaGrow
- "Tidak punya waktu urus konten" → InstaGrow DFY
- "Chat telat dibalas" / "admin kewalahan" → WA Agent
- "Customer tanya hal sama terus" → WA Agent Basic
- "Butuh bot yang bisa bantu jualan" → WA Agent Pro
- "Mau semuanya dari konten sampai chat" → InstaGrow + WA Agent DFY

Guardrail:
- JANGAN sebut nama tools/internal provider/API/backend.
- JANGAN janji 100% closing/viral/24 jam full.
- JANGAN mengarang harga.
- Kalau minta custom/meeting/harga final/kontrak/payment/katalog/integrasi kompleks: arahkan ke tim NusaAI.id.
- Handoff konfirmasi: "Baik Kak [nama], permintaan Kakak akan diteruskan ke tim NusaAI.id. Tim kami aktif pada jam kerja 09.00–17.00 WIB, akan segera kami hubungi ya."
- Jangan tanya lagi jam luang — langsung kasih info jam kerja.

Bahasa Indonesia santai-profesional. Nggak maksa jualan. Bisa edukasi singkat soal InstaGrow. Tahu kapan handoff. Memory per lead.

WhatsApp Writing Rules:
- 1 bubble = 1 ide utama. Maksimal 2-4 baris pendek.
- Maksimal 1 pertanyaan per bubble.
- Jangan gabungkan salam + penjelasan panjang + CTA + disclaimer dalam satu bubble.
- Kompleks → pecah 2-3 bubble: (1) acknowledge, (2) jawaban, (3) pertanyaan/next step.
- Bold (*kata*) hanya 1-3 kata penting.
- Emoji maks 1-2 per bubble: 👋 ✅ 🙏 📝 📌 🔎 ⚠️ ❌.
- Komplain → minim emoji (🙏 saja), jangan ceria, jangan push sales.
- Jangan tulis seperti brosur. Pelanggan scanning.
- Pola ideal: (1) Greeting 👋, (2) Jawaban inti, (3) 1 pertanyaan spesifik.

Vibe: ramah, cepat nangkap kebutuhan, nggak maksa, nggak sok terlalu pintar sampai bikin ilfeel.
""".strip()


def build_prompt(customer_text: str, history: list[dict], context_note: str | None = None) -> str:
    now = current_wib_time()
    greeting = time_greeting(now)
    time_info = f"Sekarang jam {now.strftime('%H:%M')} WIB ({greeting})."

    hist_lines = []
    for row in history[-8:]:
        raw = row.get("raw") or {}
        role = "Customer" if row.get("direction") == "inbound" else "Aulia"
        if row.get("direction") == "outbound" and raw.get("key", {}).get("fromMe") is True:
            role = "Admin NusaAI"
        elif row.get("direction") == "system":
            role = "Catatan sistem"
        hist_lines.append(f"{role}: {row.get('text','')}")
    history_text = "\n".join(hist_lines) if hist_lines else "Belum ada."
    context_text = f"\nCatatan operasional:\n{context_note}\n" if context_note else ""

    return dedent(
        f"""
        {SYSTEM_CONTEXT}

        {time_info}

        {context_text}

        Riwayat chat singkat:
        {history_text}

        Pesan customer terbaru:
        {customer_text}

        Tulis hanya balasan WhatsApp dari Aulia. Jangan pakai markdown berlebihan.
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


def has_recent_reply(history: list[dict], seconds: int = 10) -> bool:
    """Check if Aulia already replied within the last N seconds."""
    if not history:
        return False
    now = datetime.now(timezone.utc)
    for msg in reversed(history):
        if msg.get("direction") == "outbound":
            created = msg.get("created_at")
            if created:
                diff = (now - created).total_seconds()
                if diff < seconds:
                    return True
            break
    return False


def opening_reply() -> str:
    now = current_wib_time()
    greeting = time_greeting(now)
    return (
        f"{greeting} Kak! Terima kasih sudah menghubungi NusaAI.id 🙌 "
        f"Aku Aulia, asisten NusaAI. "
        f"NusaAI punya InstaGrow (kelola sosial media) dan WA Agent — "
        f"Basic buat otomatis balas chat & FAQ, Pro buat sales receptionist yang bantu qualify lead. "
        f"Boleh kenalan siapa nama Kakak dan bisnisnya di bidang apa? "
        f"Nanti aku bantu rekomendasiin yang paling cocok."
    )


def fallback_reply(customer_text: str, history: list[dict] | None = None) -> str:
    history = history or []
    if history:
        text = customer_text.lower()
        if "instagrow" in text or "instagram" in text or "sosmed" in text or "sosial media" in text:
            return (
                "InstaGrow itu layanan kelola sosial media dari NusaAI.id Kak. "
                "Kami bantu dari setup akun, riset konten, bikin caption & desain, sampai jadwal posting. "
                "Cocok kalau Kakak mau sosial media lebih aktif dan profesional tapi nggak punya waktu/ngerasa overwhelmed.urang jelasin tentang WA Agent — itu asisten WhatsApp yang bisa auto-reply chat 24/7, jawab FAQ, "
                "dan bisa bantu kualifikasi lead juga. "
                "Kakak sekarang pakai WhatsApp buat jualan juga?"
            )
        if "wa" in text or "whatsapp" in text:
            return (
                "WA Agent itu asisten WhatsApp otomatis dari NusaAI.id Kak. "
                "Bisa auto-reply chat, jawab FAQ, sama bantu kualifikasi lead masuk. "
                "Ada paket Basic (auto-reply + FAQ) dan Pro (tambah sales discovery + follow-up). "
                "Kakak sekarang chat WhatsApp seringnya tentang apa — inquiry produk, sama customer lama, atau lainnya?"
            )
        return (
            "Aku bantu lanjut ya Kak. Boleh ceritakan lebih detail kebutuhan bisnis Kakak sekarang?"
        )

    # No history — opening
    now = current_wib_time()
    greeting = time_greeting(now)
    return (
        f"{greeting} Kak, aku Aulia dari NusaAI.id. "
        f"Boleh kenalan siapa nama Kakak dan bisnisnya bergerak di bidang apa?"
    )


CUSTOM_ADDON_RESPONSE = (
    "Bisa Kak, tapi itu masuk kebutuhan add-on/custom karena di luar setup standar. "
    "Untuk tahap awal, kami sarankan mulai dari flow utama dulu supaya cepat jalan dan manfaatnya terlihat."
)


def generate_reply(customer_text: str, history: list[dict], provider: str, model: str, timeout: int = 160, context_note: str | None = None) -> str:
    if is_opening_greeting(customer_text, history):
        return opening_reply()

    prompt = build_prompt(customer_text, history, context_note=context_note)
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
    except Exception as e:
        import logging
        logging.getLogger("aulia.brain").exception("Hermes model call failed; using fallback reply: %s", e)
        return fallback_reply(customer_text, history)

    lines = [line.strip() for line in out.splitlines() if line.strip() and not line.startswith("session_id:")]
    reply = "\n".join(lines).strip()
    if not reply:
        return fallback_reply(customer_text, history)
    return reply[:1800]
