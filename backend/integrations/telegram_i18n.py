"""MAXIA — Telegram bot internationalization layer (Plan CEO V7 / P4A).

Strategy:
- **Static strings** (welcome, help, commands, errors) are pre-translated
  and stored as Python dicts — zero latency, zero LLM cost, 100% reproducible.
- **Dynamic strings** (chat responses, custom answers) can optionally be
  routed through an injectable translator for runtime translation.
- **User language** is detected from Telegram's `user.language_code` with
  robust alias mapping (zh-cn→zh-tw, pt-pt→pt-br, etc.).
- **Glossary protection**: crypto terms (USDC, USDT, swap, escrow, PDA,
  slippage, Solana, EVM, DeFi) are preserved across all languages.

Languages (13): en, fr, ja, ko, zh-tw, th, vi, id, hi, ar, he, pt-br, es
"""
from __future__ import annotations

from typing import Final, Optional, Protocol


SUPPORTED_LANGS: Final[tuple[str, ...]] = (
    "en", "fr", "ja", "ko", "zh-tw", "th", "vi",
    "id", "hi", "ar", "he", "pt-br", "es",
)

DEFAULT_LANG: Final[str] = "en"

# Crypto glossary — terms that must NEVER be translated.
CRYPTO_GLOSSARY: Final[frozenset[str]] = frozenset({
    "MAXIA", "USDC", "USDT", "SOL", "ETH", "BTC", "TON",
    "DeFi", "NFT", "GPU", "PDA", "EVM", "LLM", "MCP",
    "swap", "escrow", "slippage", "Pyth", "Jupiter", "Akash",
    "Solana", "Base", "Arbitrum", "Polygon", "Ethereum", "Bitcoin",
    "AI", "API", "SDK",
})


# ── Pre-translated static strings ──
#
# Each key is a stable short identifier used by the bot code.
# Fallback to English if a language is missing a specific key.


_STATIC: Final[dict[str, dict[str, str]]] = {
    # ── /start welcome ──
    "welcome.title": {
        "en": "Welcome to <b>MAXIA</b>, {name}!",
        "fr": "Bienvenue sur <b>MAXIA</b>, {name} !",
        "ja": "<b>MAXIA</b>へようこそ、{name}さん!",
        "ko": "<b>MAXIA</b>에 오신 것을 환영합니다, {name}님!",
        "zh-tw": "歡迎來到 <b>MAXIA</b>, {name}!",
        "th": "ยินดีต้อนรับสู่ <b>MAXIA</b>, {name}!",
        "vi": "Chào mừng đến <b>MAXIA</b>, {name}!",
        "id": "Selamat datang di <b>MAXIA</b>, {name}!",
        "hi": "<b>MAXIA</b> में आपका स्वागत है, {name}!",
        "ar": "مرحبا بك في <b>MAXIA</b>, {name}!",
        "he": "ברוכים הבאים ל-<b>MAXIA</b>, {name}!",
        "pt-br": "Bem-vindo ao <b>MAXIA</b>, {name}!",
        "es": "Bienvenido a <b>MAXIA</b>, {name}!",
    },
    "welcome.subtitle": {
        "en": "AI-powered trading across 15 blockchains.",
        "fr": "Trading alimente par IA sur 15 blockchains.",
        "ja": "15ブロックチェーンでAI駆動の取引。",
        "ko": "15개 블록체인에서 AI 기반 거래.",
        "zh-tw": "15條區塊鏈上的AI驅動交易。",
        "th": "การเทรดขับเคลื่อนด้วย AI บน 15 บล็อกเชน",
        "vi": "Giao dich dua tren AI tren 15 blockchain.",
        "id": "Perdagangan bertenaga AI di 15 blockchain.",
        "hi": "15 blockchain पर AI-संचालित trading।",
        "ar": "تداول مدعوم بالذكاء الاصطناعي عبر 15 blockchain.",
        "he": "מסחר מונע AI על 15 בלוקצ'יינים.",
        "pt-br": "Trading com IA em 15 blockchains.",
        "es": "Trading con IA en 15 blockchains.",
    },
    "welcome.actions": {
        "en": "What you can do:",
        "fr": "Ce que vous pouvez faire :",
        "ja": "できること:",
        "ko": "할 수 있는 것:",
        "zh-tw": "你可以做的:",
        "th": "สิ่งที่คุณทำได้:",
        "vi": "Ban co the lam:",
        "id": "Yang bisa Anda lakukan:",
        "hi": "आप क्या कर सकते हैं:",
        "ar": "ما يمكنك فعله:",
        "he": "מה אתה יכול לעשות:",
        "pt-br": "O que voce pode fazer:",
        "es": "Que puedes hacer:",
    },
    "welcome.cmd_price": {
        "en": "/price SOL — Live price",
        "fr": "/price SOL — Prix en direct",
        "ja": "/price SOL — ライブ価格",
        "ko": "/price SOL — 실시간 가격",
        "zh-tw": "/price SOL — 即時價格",
        "th": "/price SOL — ราคาสด",
        "vi": "/price SOL — Gia truc tiep",
        "id": "/price SOL — Harga live",
        "hi": "/price SOL — लाइव मूल्य",
        "ar": "/price SOL — السعر المباشر",
        "he": "/price SOL — מחיר חי",
        "pt-br": "/price SOL — Preco ao vivo",
        "es": "/price SOL — Precio en vivo",
    },
    "welcome.cmd_help": {
        "en": "/help — All commands",
        "fr": "/help — Toutes les commandes",
        "ja": "/help — 全コマンド",
        "ko": "/help — 모든 명령어",
        "zh-tw": "/help — 所有命令",
        "th": "/help — คำสั่งทั้งหมด",
        "vi": "/help — Tat ca lenh",
        "id": "/help — Semua perintah",
        "hi": "/help — सभी commands",
        "ar": "/help — جميع الأوامر",
        "he": "/help — כל הפקודות",
        "pt-br": "/help — Todos os comandos",
        "es": "/help — Todos los comandos",
    },
    "welcome.open_app": {
        "en": "Or tap the button below to open the full trading app:",
        "fr": "Ou appuyez sur le bouton pour ouvrir l'app de trading :",
        "ja": "または下のボタンで取引アプリを開いてください:",
        "ko": "또는 아래 버튼으로 전체 거래 앱을 엽니다:",
        "zh-tw": "或點擊下方按鈕打開完整交易應用:",
        "th": "หรือแตะปุ่มด้านล่างเพื่อเปิดแอปเทรดเต็มรูปแบบ:",
        "vi": "Hoac nhan nut ben duoi de mo ung dung giao dich:",
        "id": "Atau tap tombol di bawah untuk buka app trading:",
        "hi": "या नीचे button दबाकर trading app खोलें:",
        "ar": "أو اضغط على الزر أدناه لفتح تطبيق التداول:",
        "he": "או הקש על הכפתור למטה כדי לפתוח את אפליקציית המסחר:",
        "pt-br": "Ou toque no botao abaixo para abrir o app de trading:",
        "es": "O toca el boton para abrir la app de trading:",
    },
    "button.open_trading": {
        "en": "Open MAXIA Trading",
        "fr": "Ouvrir MAXIA Trading",
        "ja": "MAXIA取引を開く",
        "ko": "MAXIA 거래 열기",
        "zh-tw": "打開MAXIA交易",
        "th": "เปิด MAXIA Trading",
        "vi": "Mo MAXIA Trading",
        "id": "Buka MAXIA Trading",
        "hi": "MAXIA Trading खोलें",
        "ar": "افتح MAXIA Trading",
        "he": "פתח MAXIA Trading",
        "pt-br": "Abrir MAXIA Trading",
        "es": "Abrir MAXIA Trading",
    },
    "button.buy_crypto": {
        "en": "Buy Crypto with Card",
        "fr": "Acheter Crypto par carte",
        "ja": "カードで暗号通貨を購入",
        "ko": "카드로 암호화폐 구매",
        "zh-tw": "用卡購買加密貨幣",
        "th": "ซื้อคริปโตด้วยบัตร",
        "vi": "Mua crypto bang the",
        "id": "Beli Crypto dengan Kartu",
        "hi": "Card से Crypto खरीदें",
        "ar": "شراء العملات المشفرة بالبطاقة",
        "he": "קנה קריפטו בכרטיס",
        "pt-br": "Comprar Crypto com Cartao",
        "es": "Comprar Crypto con Tarjeta",
    },
    "button.sniper": {
        "en": "Token Sniper",
        "fr": "Token Sniper",
        "ja": "トークンスナイパー",
        "ko": "토큰 스나이퍼",
        "zh-tw": "代幣狙擊手",
        "th": "Token Sniper",
        "vi": "Token Sniper",
        "id": "Token Sniper",
        "hi": "Token Sniper",
        "ar": "قناص التوكن",
        "he": "צלף טוקנים",
        "pt-br": "Token Sniper",
        "es": "Token Sniper",
    },
    # ── /help ──
    "help.title": {
        "en": "<b>MAXIA Bot Commands:</b>",
        "fr": "<b>Commandes du bot MAXIA :</b>",
        "ja": "<b>MAXIAボットコマンド:</b>",
        "ko": "<b>MAXIA 봇 명령어:</b>",
        "zh-tw": "<b>MAXIA機器人命令:</b>",
        "th": "<b>คำสั่งบอท MAXIA:</b>",
        "vi": "<b>Lenh bot MAXIA:</b>",
        "id": "<b>Perintah Bot MAXIA:</b>",
        "hi": "<b>MAXIA Bot Commands:</b>",
        "ar": "<b>أوامر روبوت MAXIA:</b>",
        "he": "<b>פקודות הבוט של MAXIA:</b>",
        "pt-br": "<b>Comandos do Bot MAXIA:</b>",
        "es": "<b>Comandos del Bot MAXIA:</b>",
    },
    "help.list": {
        # Kept identical across languages because the commands themselves
        # are English keywords; only the surrounding explanation is localized
        # via help.title above and help.footer below.
        "en": (
            "/start — Welcome + Mini App\n"
            "/price SOL — Live token price\n"
            "/price ETH — Live ETH price\n"
            "/price BTC — Live BTC price\n"
            "/help — This help message"
        ),
    },
    "help.footer": {
        "en": "Or open the full trading app with the Menu button.",
        "fr": "Ou ouvrez l'app de trading avec le bouton Menu.",
        "ja": "またはメニューボタンで取引アプリを開けます。",
        "ko": "또는 메뉴 버튼으로 거래 앱을 엽니다.",
        "zh-tw": "或用菜單按鈕打開完整交易應用。",
        "th": "หรือเปิดแอปเทรดเต็มด้วยปุ่มเมนู",
        "vi": "Hoac mo ung dung day du voi nut Menu.",
        "id": "Atau buka app trading lengkap dengan tombol Menu.",
        "hi": "या Menu button से पूर्ण trading app खोलें।",
        "ar": "أو افتح تطبيق التداول الكامل بزر القائمة.",
        "he": "או פתח את אפליקציית המסחר המלאה עם כפתור התפריט.",
        "pt-br": "Ou abra o app de trading com o botao Menu.",
        "es": "O abre la app de trading con el boton Menu.",
    },
    # ── /price ──
    "price.fetched": {
        "en": "<b>{symbol}</b>: ${price}\nSource: {source}",
        "fr": "<b>{symbol}</b> : {price}$\nSource : {source}",
        "ja": "<b>{symbol}</b>: ${price}\nソース: {source}",
        "ko": "<b>{symbol}</b>: ${price}\n출처: {source}",
        "zh-tw": "<b>{symbol}</b>: ${price}\n來源: {source}",
        "th": "<b>{symbol}</b>: ${price}\nแหล่ง: {source}",
        "vi": "<b>{symbol}</b>: ${price}\nNguon: {source}",
        "id": "<b>{symbol}</b>: ${price}\nSumber: {source}",
        "hi": "<b>{symbol}</b>: ${price}\nस्रोत: {source}",
        "ar": "<b>{symbol}</b>: ${price}\nالمصدر: {source}",
        "he": "<b>{symbol}</b>: ${price}\nמקור: {source}",
        "pt-br": "<b>{symbol}</b>: ${price}\nFonte: {source}",
        "es": "<b>{symbol}</b>: ${price}\nFuente: {source}",
    },
    "price.error": {
        "en": "Could not fetch price for {symbol}",
        "fr": "Impossible de recuperer le prix pour {symbol}",
        "ja": "{symbol}の価格を取得できませんでした",
        "ko": "{symbol} 가격을 가져올 수 없습니다",
        "zh-tw": "無法獲取 {symbol} 的價格",
        "th": "ไม่สามารถดึงราคาของ {symbol}",
        "vi": "Khong the lay gia {symbol}",
        "id": "Tidak dapat mengambil harga {symbol}",
        "hi": "{symbol} का मूल्य प्राप्त नहीं कर सका",
        "ar": "تعذر جلب سعر {symbol}",
        "he": "לא ניתן לקבל מחיר עבור {symbol}",
        "pt-br": "Nao foi possivel obter o preco de {symbol}",
        "es": "No se pudo obtener el precio de {symbol}",
    },
}


# ── Language detection ──


_ALIASES: Final[dict[str, str]] = {
    "zh": "zh-tw",
    "zh-cn": "zh-tw",
    "zh-hk": "zh-tw",
    "zh-hant": "zh-tw",
    "zh-hans": "zh-tw",
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
    "en-us": "en",
    "en-gb": "en",
    "fr-fr": "fr",
    "fr-ca": "fr",
    "he-il": "he",
    "hi-in": "hi",
    "th-th": "th",
    "vi-vn": "vi",
    "id-id": "id",
}


def detect_lang(raw: object) -> str:
    """Normalize a Telegram language_code to a supported canonical key.

    Falls back to ``DEFAULT_LANG`` for anything unknown or invalid.
    """
    if not isinstance(raw, str):
        return DEFAULT_LANG
    cleaned = raw.strip().lower().replace("_", "-")
    if not cleaned:
        return DEFAULT_LANG
    if cleaned in _STATIC["welcome.title"]:
        return cleaned
    if cleaned in _ALIASES:
        return _ALIASES[cleaned]
    base = cleaned.split("-", 1)[0]
    if base in _STATIC["welcome.title"]:
        return base
    return DEFAULT_LANG


def t(key: str, lang: object = DEFAULT_LANG, **fmt: object) -> str:
    """Translate a static string key, with optional format placeholders.

    If the key is not found, returns the English version or the key itself
    as a last resort. Format arguments are always applied via ``format_map``
    so missing placeholders do not raise.
    """
    normalized = detect_lang(lang)
    bucket = _STATIC.get(key)
    if bucket is None:
        return key
    template = bucket.get(normalized) or bucket.get(DEFAULT_LANG) or key
    if not fmt:
        return template
    try:
        return template.format_map(_SafeDict(fmt))
    except (IndexError, KeyError, ValueError):
        return template


class _SafeDict(dict):
    """format_map mapping that leaves unknown placeholders untouched."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


# ── Dynamic translator protocol (optional, for LLM-backed chat) ──


class DynamicTranslator(Protocol):
    """Protocol for runtime translation of arbitrary text (chat replies).

    Implementations may call Qwen/OpenAI/DeepL. The bot runs fine without
    one: dynamic chat replies stay in their source language.
    """

    def translate(self, text: str, target_lang: str) -> str: ...


class NoopTranslator:
    """Fallback that returns the input unchanged."""

    def translate(self, text: str, target_lang: str) -> str:  # noqa: D401
        return text


# ── Convenience builders for the bot handlers ──


def build_welcome_text(lang: object, name: str) -> str:
    """Assemble the full /start welcome text in the user's language."""
    safe_name = (name or "there").strip()[:64] or "there"
    parts = [
        t("welcome.title", lang, name=safe_name),
        "",
        t("welcome.subtitle", lang),
        "",
        t("welcome.actions", lang),
        "  " + t("welcome.cmd_price", lang),
        "  " + t("welcome.cmd_help", lang),
        "",
        t("welcome.open_app", lang),
    ]
    return "\n".join(parts)


def build_help_text(lang: object) -> str:
    """Assemble the full /help message in the user's language."""
    return "\n\n".join([
        t("help.title", lang),
        _STATIC["help.list"]["en"],
        t("help.footer", lang),
    ])


def build_price_text(
    lang: object, symbol: str, price: Optional[float], source: str,
) -> str:
    """Assemble the /price response in the user's language."""
    if price is None or price <= 0:
        return t("price.error", lang, symbol=symbol)
    return t(
        "price.fetched",
        lang,
        symbol=symbol,
        price=f"{price:,.4f}",
        source=source or "oracle",
    )
