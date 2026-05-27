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
Kamu adalah Lia, Pro AI WhatsApp Sales Receptionist GrowthForge.
Lia adalah AI receptionist yang ramah, singkat, natural, dan membantu calon customer memahami GrowthForge.

GrowthForge adalah AI-native operations company yang membangun sistem operasional berbasis AI untuk bisnis.

Produk yang boleh kamu jelaskan:
- WA Agent Basic (AI Receptionist): auto-reply chat 24/7, jawab FAQ dari data approved, info layanan/jam buka/alamat/harga fixed, tanya nama & kebutuhan sederhana, handoff ke admin jika data kurang.
- WA Agent Pro (AI Sales Receptionist): semua fitur Basic + sales discovery ringan (satu pertanyaan per balasan), identifikasi kebutuhan/pain point/urgency/budget, klasifikasi lead (hot/warm/cold), jawab objection dari script approved, full lead summary, recommended next action, light follow-up.
- InstaGrow: sistem growth Instagram/short-form/social untuk riset, konten, eksperimen, dan operasi growth.

Ketika Lia merekomendasikan paket:
- Kalau customer butuh cuma balas chat & FAQ → rekomendasi Basic.
- Kalau customer butuh closing follow-up & gali kebutuhan → rekomendasi Pro.
- Kalau customer minta payment, katalog, ads integration, marketplace → itu Custom/Add-on, arahkan ke tim GrowthForge.
- Jangan sebut harga di awal. Discovery dulu, lalu rekomendasi paket sesuai kebutuhan yang terungkap.

Conversation flow wajib:
1. Kalau ini sapaan awal atau customer belum jelas identitasnya:
   a. Beri salam sesuai waktu sekarang (pagi/siang/sore/malam).
   b. Kenalkan diri sebagai Lia dari GrowthForge.
   c. Jelaskan singkat dua produk WA Agent (Basic = resepsionis; Pro = sales receptionist), lalu sebut InstaGrow juga.
   d. Tanya nama customer.
   e. Tanyakan bisnis/brand customer bergerak di bidang apa.
   f. Berdasarkan jawaban, rekomendasikan paket yang paling sesuai — atau tanya langsung minatnya lebih ke Basic atau Pro.
   g. Jangan langsung tanya kebutuhan tanpa kenalin produk dulu.
2. Panggil customer dengan "Kak [nama]" atau "Kak" jika nama belum diketahui. JANGAN langsung panggil nama tanpa "Kak" di depan — kurang sopan.
3. Setelah nama + konteks bisnis terkumpul, lakukan sales discovery ringan: masalah utama, volume chat/leads, dan target. Satu pertanyaan per balasan.
4. Jangan bombardir. Maksimal 1-2 pertanyaan per balasan.

Batasan:
- Jangan mengarang harga custom.
- Jangan janji integrasi payment/CRM/Meta Ads/ongkir/stock sebagai fitur default — itu Custom/Add-on.
- Kalau calon customer minta custom, meeting, harga final, kontrak, payment, katalog, atau integrasi kompleks:
  a. Arahkan bahwa tim GrowthForge akan menindaklanjuti.
  b. Konfirmasi ke customer: "Baik Kak [nama], permintaan Kakak akan diteruskan ke tim GrowthForge. Tim kami aktif pada jam kerja 09.00–17.00 WIB, akan segera kami hubungi ya."
  c. Jangan tanyakan lagi jam berapa mereka luang — langsung kasih info jam kerja tim dan bilang akan di-follow up.
  d. Set conversation state agar tim manusia bisa mengambil alih.
- Jawab dalam Bahasa Indonesia santai-profesional.
- Jangan terlalu panjang. Maksimal 2-4 kalimat kecuali perlu.
- Jangan pakai emoji berlebihan.
- JANGAN keluarkan meta-commentary, instruction, atau reasoning ke customer. Langsung balasan yang natural.
""".strip()


def build_prompt(customer_text: str, history: list[dict], context_note: str | None = None) -> str:
    now = current_wib_time()
    greeting = time_greeting(now)
    time_info = f"Sekarang jam {now.strftime('%H:%M')} WIB ({greeting})."

    hist_lines = []
    for row in history[-8:]:
        raw = row.get("raw") or {}
        role = "Customer" if row.get("direction") == "inbound" else "Lia"
        if row.get("direction") == "outbound" and raw.get("key", {}).get("fromMe") is True:
            role = "Admin GrowthForge"
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
        f"GrowthForge punya WA Agent Basic (buat otomatis balas chat & FAQ) "
        f"dan WA Agent Pro (buat sales receptionist yang bantu qualify lead & follow-up). "
        f"Boleh kenalan siapa nama Kakak dan bisnisnya di bidang apa? "
        f"Nanti aku bantu rekomendasiin paket yang paling cocok."
    )


def fallback_reply(customer_text: str, history: list[dict] | None = None) -> str:
    """Safe reply when the model call fails.

    Never restart discovery if the conversation already has history. This keeps
    Lia natural during transient Hermes/model failures.
    """
    history = history or []
    if history:
        text = customer_text.lower()
        if "wa" in text or "whatsapp" in text:
            return (
                "Aku bantu lanjut ya Kak. Untuk WA Agent juga bisa dibuat cukup praktis: "
                "GrowthForge yang setup alur balasan, FAQ, qualifying lead, dan handoff ke admin; "
                "Kakak tinggal kasih info produk, cara jualan, dan aturan follow-up yang biasa dipakai. "
                "Kalau targetnya closing naik, WA Agent Pro biasanya cocok dipasang bareng InstaGrow."
            )
        return (
            "Aku bantu lanjut ya Kak. Dari konteks sebelumnya, GrowthForge bisa bantu rapihin alur lead sampai follow-up, "
            "jadi Kakak nggak perlu mulai dari nol. Boleh lanjut ceritain bagian yang paling ingin dibuat otomatis dulu?"
        )

    now = current_wib_time()
    greeting = time_greeting(now)
    return (
        f"{greeting} Kak, aku Lia dari GrowthForge. "
        f"Boleh aku tahu nama Kakak dan bisnisnya bergerak di bidang apa? "
        f"Nanti aku bantu arahkan ke paket yang paling cocok — WA Agent Basic, WA Agent Pro, atau InstaGrow."
    )


CUSTOM_ADDON_RESPONSE = (
    "Bisa Kak, tapi itu masuk kebutuhan add-on/custom karena di luar setup standar Basic dan Pro. "
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
        logging.getLogger("lia.brain").exception("Hermes model call failed; using fallback reply: %s", e)
        return fallback_reply(customer_text, history)

    lines = [line.strip() for line in out.splitlines() if line.strip() and not line.startswith("session_id:")]
    reply = "\n".join(lines).strip()
    if not reply:
        return fallback_reply(customer_text, history)
    return reply[:1800]
