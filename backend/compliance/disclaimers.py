"""MAXIA multilingual disclaimers — Plan CEO V7.

Used by Telegram bot, Mini App, and email outreach to display legal
disclaimers in the user's language.

Languages covered (13):
    EN, FR, JA, KO, ZH-TW, TH, VI, ID, HI, AR, HE, PT-BR, ES
"""
from __future__ import annotations

from typing import Final

# Canonical language codes (lower-case, Telegram-style).
SUPPORTED_LANGUAGES: Final[tuple[str, ...]] = (
    "en", "fr", "ja", "ko", "zh-tw", "th", "vi",
    "id", "hi", "ar", "he", "pt-br", "es",
)

# Long disclaimer — shown at /start and in legal footer.
_LONG: Final[dict[str, str]] = {
    "en": (
        "MAXIA — Legal notice\n\n"
        "MAXIA is an AI-to-AI marketplace. Services shown are paper trading "
        "unless you explicitly opt in. This is NOT financial advice.\n\n"
        "Restricted regions: CN, KP, IR, SY, CU, MM, AF, RU, BY, US (MAXIA ToS).\n"
        "India (IN): read-only discovery only pending VASP registration.\n\n"
        "Crypto assets are volatile. You may lose capital. No guarantees."
    ),
    "fr": (
        "MAXIA — Mention legale\n\n"
        "MAXIA est une marketplace AI-to-AI. Les services affiches sont en "
        "paper trading sauf opt-in explicite. Ceci n'est PAS un conseil "
        "financier.\n\n"
        "Regions restreintes : CN, KP, IR, SY, CU, MM, AF, RU, BY, US (CGU MAXIA).\n"
        "Inde (IN) : lecture seule tant que l'enregistrement VASP n'est pas fait.\n\n"
        "Les crypto-actifs sont volatils. Vous pouvez perdre votre capital."
    ),
    "ja": (
        "MAXIA - 法的通知\n\n"
        "MAXIAはAI対AIのマーケットプレイスです。明示的にオプトインしない限り、"
        "表示されるサービスはペーパートレードです。これは金融アドバイスでは"
        "ありません。\n\n"
        "制限地域: CN, KP, IR, SY, CU, MM, AF, RU, BY, US (MAXIA利用規約)\n"
        "インド(IN): VASP登録待ちのため閲覧のみ\n\n"
        "暗号資産は価格変動が大きく、元本を失う可能性があります。"
    ),
    "ko": (
        "MAXIA - 법적 고지\n\n"
        "MAXIA는 AI 대 AI 마켓플레이스입니다. 명시적으로 동의하지 않는 한 "
        "표시된 서비스는 페이퍼 트레이딩입니다. 이는 금융 조언이 아닙니다.\n\n"
        "제한 지역: CN, KP, IR, SY, CU, MM, AF, RU, BY, US (MAXIA 약관)\n"
        "인도(IN): VASP 등록 대기 중 조회 전용\n\n"
        "암호화폐는 변동성이 크며 원금 손실이 가능합니다."
    ),
    "zh-tw": (
        "MAXIA — 法律聲明\n\n"
        "MAXIA 是一個 AI 對 AI 的市場平台。除非您明確選擇加入，否則所顯示的"
        "服務均為紙上交易。此非財務建議。\n\n"
        "限制地區: CN, KP, IR, SY, CU, MM, AF, RU, BY, US (MAXIA 服務條款)\n"
        "印度 (IN): 等待 VASP 註冊，僅供瀏覽\n\n"
        "加密資產波動性高，您可能會損失本金。"
    ),
    "th": (
        "MAXIA — ประกาศทางกฎหมาย\n\n"
        "MAXIA เป็นตลาดกลาง AI-to-AI บริการที่แสดงเป็นการซื้อขายกระดาษ "
        "เว้นแต่คุณจะเลือกเข้าร่วมอย่างชัดแจ้ง นี่ไม่ใช่คำแนะนำทางการเงิน\n\n"
        "ภูมิภาคที่จำกัด: CN, KP, IR, SY, CU, MM, AF, RU, BY, US\n"
        "อินเดีย (IN): เฉพาะการค้นพบแบบอ่านอย่างเดียว\n\n"
        "สินทรัพย์คริปโตมีความผันผวน คุณอาจสูญเสียเงินต้น"
    ),
    "vi": (
        "MAXIA - Thông báo pháp lý\n\n"
        "MAXIA là thị trường AI-to-AI. Dịch vụ hiển thị là giao dịch giấy "
        "trừ khi bạn chọn tham gia rõ ràng. Đây KHÔNG phải lời khuyên tài chính.\n\n"
        "Khu vực hạn chế: CN, KP, IR, SY, CU, MM, AF, RU, BY, US\n"
        "Ấn Độ (IN): chỉ khám phá, chờ đăng ký VASP\n\n"
        "Tài sản tiền điện tử biến động. Bạn có thể mất vốn."
    ),
    "id": (
        "MAXIA - Pemberitahuan hukum\n\n"
        "MAXIA adalah pasar AI-ke-AI. Layanan yang ditampilkan adalah paper "
        "trading kecuali Anda memilih secara eksplisit. Ini BUKAN nasihat "
        "keuangan.\n\n"
        "Wilayah terbatas: CN, KP, IR, SY, CU, MM, AF, RU, BY, US\n"
        "India (IN): hanya eksplorasi baca-saja menunggu pendaftaran VASP\n\n"
        "Aset kripto berfluktuasi. Anda dapat kehilangan modal."
    ),
    "hi": (
        "MAXIA - कानूनी सूचना\n\n"
        "MAXIA एक AI-से-AI मार्केटप्लेस है। दिखाई गई सेवाएँ पेपर ट्रेडिंग "
        "हैं जब तक आप स्पष्ट रूप से ऑप्ट-इन नहीं करते। यह वित्तीय सलाह नहीं है।\n\n"
        "प्रतिबंधित क्षेत्र: CN, KP, IR, SY, CU, MM, AF, RU, BY, US\n"
        "भारत (IN): VASP पंजीकरण तक केवल खोज (केवल पठन)।\n\n"
        "क्रिप्टो संपत्तियाँ अस्थिर हैं। आप पूंजी खो सकते हैं।"
    ),
    "ar": (
        "MAXIA - اشعار قانوني\n\n"
        "MAXIA هو سوق AI إلى AI. الخدمات المعروضة هي تداول ورقي ما لم توافق "
        "صراحة. هذه ليست نصيحة مالية.\n\n"
        "المناطق المقيدة: CN, KP, IR, SY, CU, MM, AF, RU, BY, US\n"
        "الهند (IN): اكتشاف للقراءة فقط في انتظار تسجيل VASP\n\n"
        "الأصول المشفرة متقلبة. قد تخسر رأس المال."
    ),
    "he": (
        "MAXIA - הודעה משפטית\n\n"
        "MAXIA הוא שוק AI ל-AI. השירותים המוצגים הם מסחר על נייר אלא אם "
        "בחרת במפורש. זו אינה עצה פיננסית.\n\n"
        "אזורים מוגבלים: CN, KP, IR, SY, CU, MM, AF, RU, BY, US\n"
        "הודו (IN): גילוי לקריאה בלבד עד רישום VASP\n\n"
        "נכסי קריפטו תנודתיים. אתה עלול להפסיד את ההון."
    ),
    "pt-br": (
        "MAXIA - Aviso legal\n\n"
        "MAXIA é um marketplace AI-to-AI. Os servicos exibidos são paper "
        "trading a menos que voce opte explicitamente. Isto NÃO é conselho "
        "financeiro.\n\n"
        "Regioes restritas: CN, KP, IR, SY, CU, MM, AF, RU, BY, US (ToS MAXIA)\n"
        "India (IN): apenas descoberta de leitura aguardando registro VASP\n\n"
        "Criptoativos sao volateis. Voce pode perder o capital."
    ),
    "es": (
        "MAXIA - Aviso legal\n\n"
        "MAXIA es un mercado AI-to-AI. Los servicios mostrados son paper "
        "trading a menos que opte explicitamente. Esto NO es asesoramiento "
        "financiero.\n\n"
        "Regiones restringidas: CN, KP, IR, SY, CU, MM, AF, RU, BY, US\n"
        "India (IN): solo descubrimiento de lectura pendiente registro VASP\n\n"
        "Los criptoactivos son volatiles. Puede perder capital."
    ),
}

# Short disclaimer — email footer / inline mode / compact UI.
_SHORT: Final[dict[str, str]] = {
    "en": "Not financial advice. Paper trading unless opt-in. Restricted in CN/KP/IR/SY/CU/MM/AF/RU/BY/US.",
    "fr": "Pas un conseil financier. Paper trading sauf opt-in. Restreint dans CN/KP/IR/SY/CU/MM/AF/RU/BY/US.",
    "ja": "金融アドバイスではありません。明示的な同意がない限りペーパートレード。",
    "ko": "금융 조언이 아닙니다. 명시적 동의 없이는 페이퍼 트레이딩.",
    "zh-tw": "非財務建議。除非選擇加入，否則為紙上交易。",
    "th": "ไม่ใช่คำแนะนำทางการเงิน การซื้อขายกระดาษเว้นแต่เลือกเข้าร่วม",
    "vi": "Không phải lời khuyên tài chính. Giao dịch giấy trừ khi chọn tham gia.",
    "id": "Bukan nasihat keuangan. Paper trading kecuali opt-in.",
    "hi": "वित्तीय सलाह नहीं। ऑप्ट-इन के बिना पेपर ट्रेडिंग।",
    "ar": "ليست نصيحة مالية. تداول ورقي ما لم توافق.",
    "he": "לא עצה פיננסית. מסחר על נייר אלא אם תבחר להצטרף.",
    "pt-br": "Nao é conselho financeiro. Paper trading sem opt-in.",
    "es": "No es asesoramiento financiero. Paper trading sin opt-in.",
}


def _normalize_lang(lang: object) -> str:
    """Return a canonical language key or 'en' fallback."""
    if not isinstance(lang, str):
        return "en"
    cleaned = lang.strip().lower().replace("_", "-")

    # Direct match
    if cleaned in _LONG:
        return cleaned

    # Common aliases
    aliases = {
        "zh": "zh-tw",
        "zh-cn": "zh-tw",   # CN blocked, serve zh-tw
        "zh-hk": "zh-tw",
        "zh-hant": "zh-tw",
        "pt": "pt-br",
        "pt-pt": "pt-br",
        "br": "pt-br",
        "es-mx": "es",
        "es-ar": "es",
        "es-es": "es",
        "ar-sa": "ar",
        "ar-ae": "ar",
        "ja-jp": "ja",
        "ko-kr": "ko",
    }
    if cleaned in aliases:
        return aliases[cleaned]

    # Strip region subtag and retry
    base = cleaned.split("-", 1)[0]
    if base in _LONG:
        return base

    return "en"


def get_disclaimer(lang: object = "en") -> str:
    """Return the long legal disclaimer in the requested language."""
    return _LONG[_normalize_lang(lang)]


def get_short_disclaimer(lang: object = "en") -> str:
    """Return the short disclaimer (footer / inline) in the requested language."""
    return _SHORT[_normalize_lang(lang)]
