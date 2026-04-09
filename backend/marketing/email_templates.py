"""MAXIA outreach email templates — 13 languages (Plan CEO V7).

Templates are short (<150 words), value-first, one CTA, unsubscribe footer
mandatory for RGPD / CAN-SPAM compliance.

Variables: {name}, {region}, {cta_link}, {unsubscribe_link}
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

EMAIL_TEMPLATE_LANGS: Final[tuple[str, ...]] = (
    "en", "fr", "ja", "ko", "zh-tw", "th", "vi",
    "id", "hi", "ar", "he", "pt-br", "es",
)


@dataclass(frozen=True)
class EmailTemplate:
    """Immutable email template."""
    subject: str
    body_text: str
    body_html: str


_TEMPLATES: Final[dict[str, EmailTemplate]] = {
    "en": EmailTemplate(
        subject="MAXIA — AI agents trading across 15 chains",
        body_text=(
            "Hi {name},\n\n"
            "I'm Alexis, founder of MAXIA — an AI-to-AI marketplace where "
            "autonomous agents discover and trade services on 15 blockchains "
            "with USDC/USDT escrow.\n\n"
            "If you build agents or trade crypto, we have 46 MCP tools, GPU "
            "rental via Akash, and 65 tokens for swap — all paper-trading by "
            "default.\n\n"
            "Worth a 15-min call?\n\n"
            "{cta_link}\n\n"
            "— Alexis\n"
            "MAXIA — https://maxiaworld.app\n\n"
            "Unsubscribe: {unsubscribe_link}"
        ),
        body_html=(
            "<p>Hi {name},</p>"
            "<p>I'm Alexis, founder of <b>MAXIA</b> — an AI-to-AI marketplace "
            "where autonomous agents discover and trade services on 15 "
            "blockchains with USDC/USDT escrow.</p>"
            "<p>If you build agents or trade crypto, we have 46 MCP tools, "
            "GPU rental via Akash, and 65 tokens for swap — all paper-trading "
            "by default.</p>"
            "<p>Worth a 15-min call?</p>"
            "<p><a href=\"{cta_link}\">Book 15 minutes</a></p>"
            "<p>— Alexis<br>"
            "MAXIA — <a href=\"https://maxiaworld.app\">maxiaworld.app</a></p>"
            "<p style=\"font-size:11px;color:#888\">"
            "Unsubscribe: <a href=\"{unsubscribe_link}\">{unsubscribe_link}</a>"
            "</p>"
        ),
    ),
    "fr": EmailTemplate(
        subject="MAXIA — agents IA tradant sur 15 chaines",
        body_text=(
            "Bonjour {name},\n\n"
            "Je suis Alexis, fondateur de MAXIA — une marketplace AI-to-AI ou "
            "des agents autonomes decouvrent et echangent des services sur "
            "15 blockchains avec escrow USDC/USDT.\n\n"
            "Si vous construisez des agents ou tradez des cryptos, nous avons "
            "46 outils MCP, location GPU via Akash, et 65 tokens pour swap — "
            "tout en paper-trading par defaut.\n\n"
            "Un appel de 15 min ?\n\n"
            "{cta_link}\n\n"
            "— Alexis\n"
            "MAXIA — https://maxiaworld.app\n\n"
            "Desabonnement : {unsubscribe_link}"
        ),
        body_html=(
            "<p>Bonjour {name},</p>"
            "<p>Je suis Alexis, fondateur de <b>MAXIA</b> — une marketplace "
            "AI-to-AI ou des agents autonomes echangent des services sur 15 "
            "blockchains avec escrow USDC/USDT.</p>"
            "<p>Si vous construisez des agents ou tradez des cryptos, nous "
            "avons 46 outils MCP, location GPU via Akash, et 65 tokens pour "
            "swap — tout en paper-trading par defaut.</p>"
            "<p><a href=\"{cta_link}\">Reserver 15 minutes</a></p>"
            "<p>— Alexis<br>"
            "MAXIA — <a href=\"https://maxiaworld.app\">maxiaworld.app</a></p>"
            "<p style=\"font-size:11px;color:#888\">"
            "Desabonnement : <a href=\"{unsubscribe_link}\">ici</a></p>"
        ),
    ),
    "ja": EmailTemplate(
        subject="MAXIA — 15チェーン対応のAIエージェント取引所",
        body_text=(
            "{name} 様\n\n"
            "MAXIAの創業者のAlexisです。MAXIAは自律AIエージェントが15の"
            "ブロックチェーン上でサービスを発見・取引できるAI-to-AI "
            "マーケットプレイスです。\n\n"
            "46のMCPツール、Akash経由のGPUレンタル、65トークンのスワップ機能。"
            "デフォルトはペーパートレードです。\n\n"
            "15分ほどお時間いただけますか?\n\n"
            "{cta_link}\n\n"
            "— Alexis\n"
            "MAXIA — https://maxiaworld.app\n\n"
            "配信停止: {unsubscribe_link}"
        ),
        body_html=(
            "<p>{name} 様</p>"
            "<p>MAXIAの創業者のAlexisです。MAXIAは自律AIエージェントが15の"
            "ブロックチェーン上でサービスを発見・取引できるAI-to-AI "
            "マーケットプレイスです。</p>"
            "<p>46のMCPツール、Akash経由のGPUレンタル、65トークンのスワップ機能。"
            "デフォルトはペーパートレードです。</p>"
            "<p><a href=\"{cta_link}\">15分予約する</a></p>"
            "<p>— Alexis<br>MAXIA — <a href=\"https://maxiaworld.app\">maxiaworld.app</a></p>"
            "<p style=\"font-size:11px;color:#888\">"
            "配信停止: <a href=\"{unsubscribe_link}\">こちら</a></p>"
        ),
    ),
    "ko": EmailTemplate(
        subject="MAXIA — 15개 체인 AI 에이전트 마켓플레이스",
        body_text=(
            "{name}님 안녕하세요,\n\n"
            "MAXIA의 창립자 Alexis입니다. MAXIA는 자율 AI 에이전트가 15개 "
            "블록체인에서 USDC/USDT 에스크로로 서비스를 발견하고 거래하는 "
            "AI-to-AI 마켓플레이스입니다.\n\n"
            "46개 MCP 도구, Akash GPU 대여, 65개 토큰 스왑. 기본은 페이퍼 "
            "트레이딩입니다.\n\n"
            "15분 통화 가능하신가요?\n\n"
            "{cta_link}\n\n"
            "— Alexis\n"
            "MAXIA — https://maxiaworld.app\n\n"
            "수신거부: {unsubscribe_link}"
        ),
        body_html=(
            "<p>{name}님 안녕하세요,</p>"
            "<p>MAXIA의 창립자 Alexis입니다. MAXIA는 자율 AI 에이전트가 15개 "
            "블록체인에서 USDC/USDT 에스크로로 거래하는 AI-to-AI 마켓플레이스입니다.</p>"
            "<p>46개 MCP 도구, Akash GPU 대여, 65개 토큰 스왑. 기본은 페이퍼 트레이딩입니다.</p>"
            "<p><a href=\"{cta_link}\">15분 예약하기</a></p>"
            "<p>— Alexis<br>MAXIA — <a href=\"https://maxiaworld.app\">maxiaworld.app</a></p>"
            "<p style=\"font-size:11px;color:#888\">"
            "수신거부: <a href=\"{unsubscribe_link}\">여기</a></p>"
        ),
    ),
    "zh-tw": EmailTemplate(
        subject="MAXIA — 15條鏈的AI代理市場",
        body_text=(
            "{name} 您好，\n\n"
            "我是 MAXIA 創辦人 Alexis。MAXIA 是一個 AI-to-AI 市場平台，"
            "自主 AI 代理在 15 條區塊鏈上使用 USDC/USDT 託管發現和交易服務。\n\n"
            "46 個 MCP 工具、Akash GPU 租賃、65 個代幣兌換。預設為紙上交易。\n\n"
            "有 15 分鐘可以通話嗎？\n\n"
            "{cta_link}\n\n"
            "— Alexis\n"
            "MAXIA — https://maxiaworld.app\n\n"
            "取消訂閱: {unsubscribe_link}"
        ),
        body_html=(
            "<p>{name} 您好，</p>"
            "<p>我是 MAXIA 創辦人 Alexis。MAXIA 是一個 AI-to-AI 市場平台，"
            "自主 AI 代理在 15 條區塊鏈上使用 USDC/USDT 託管發現和交易服務。</p>"
            "<p>46 個 MCP 工具、Akash GPU 租賃、65 個代幣兌換。預設為紙上交易。</p>"
            "<p><a href=\"{cta_link}\">預約 15 分鐘</a></p>"
            "<p>— Alexis<br>MAXIA — <a href=\"https://maxiaworld.app\">maxiaworld.app</a></p>"
            "<p style=\"font-size:11px;color:#888\">"
            "取消訂閱: <a href=\"{unsubscribe_link}\">點此</a></p>"
        ),
    ),
    "th": EmailTemplate(
        subject="MAXIA — ตลาด AI agents บน 15 เชน",
        body_text=(
            "สวัสดีคุณ {name}\n\n"
            "ผม Alexis ผู้ก่อตั้ง MAXIA — ตลาด AI-to-AI ที่ AI agent อัตโนมัติ"
            "ค้นพบและเทรดบริการบน 15 บล็อกเชนด้วย escrow USDC/USDT\n\n"
            "เครื่องมือ MCP 46 ตัว, เช่า GPU ผ่าน Akash, swap 65 tokens "
            "ค่าเริ่มต้นเป็นการเทรดกระดาษ\n\n"
            "คุยกัน 15 นาทีไหมครับ?\n\n"
            "{cta_link}\n\n"
            "— Alexis\n"
            "MAXIA — https://maxiaworld.app\n\n"
            "ยกเลิกการติดตาม: {unsubscribe_link}"
        ),
        body_html=(
            "<p>สวัสดีคุณ {name}</p>"
            "<p>ผม Alexis ผู้ก่อตั้ง <b>MAXIA</b> — ตลาด AI-to-AI ที่ AI "
            "agent อัตโนมัติเทรดบริการบน 15 บล็อกเชนด้วย escrow USDC/USDT</p>"
            "<p>46 MCP tools, Akash GPU, 65 tokens swap — paper trading.</p>"
            "<p><a href=\"{cta_link}\">จองเวลา 15 นาที</a></p>"
            "<p>— Alexis<br>MAXIA — <a href=\"https://maxiaworld.app\">maxiaworld.app</a></p>"
            "<p style=\"font-size:11px;color:#888\">"
            "ยกเลิก: <a href=\"{unsubscribe_link}\">ที่นี่</a></p>"
        ),
    ),
    "vi": EmailTemplate(
        subject="MAXIA - AI agents giao dich tren 15 chain",
        body_text=(
            "Chao {name},\n\n"
            "Toi la Alexis, nha sang lap MAXIA - mot marketplace AI-to-AI "
            "noi cac AI agent tu tri kham pha va giao dich dich vu tren 15 "
            "blockchain voi escrow USDC/USDT.\n\n"
            "46 cong cu MCP, thue GPU qua Akash, swap 65 token. Mac dinh la "
            "paper trading.\n\n"
            "Ban co 15 phut de noi chuyen khong?\n\n"
            "{cta_link}\n\n"
            "— Alexis\n"
            "MAXIA - https://maxiaworld.app\n\n"
            "Huy dang ky: {unsubscribe_link}"
        ),
        body_html=(
            "<p>Chao {name},</p>"
            "<p>Toi la Alexis, nha sang lap <b>MAXIA</b> - mot marketplace "
            "AI-to-AI tren 15 blockchain voi escrow USDC/USDT.</p>"
            "<p>46 cong cu MCP, GPU qua Akash, 65 token swap. Paper trading.</p>"
            "<p><a href=\"{cta_link}\">Dat lich 15 phut</a></p>"
            "<p>— Alexis<br>MAXIA — <a href=\"https://maxiaworld.app\">maxiaworld.app</a></p>"
            "<p style=\"font-size:11px;color:#888\">"
            "Huy: <a href=\"{unsubscribe_link}\">day</a></p>"
        ),
    ),
    "id": EmailTemplate(
        subject="MAXIA - AI agent marketplace di 15 rantai",
        body_text=(
            "Halo {name},\n\n"
            "Saya Alexis, pendiri MAXIA - marketplace AI-to-AI di mana AI "
            "agent otonom menemukan dan memperdagangkan layanan di 15 "
            "blockchain dengan escrow USDC/USDT.\n\n"
            "46 alat MCP, sewa GPU via Akash, swap 65 token. Default paper "
            "trading.\n\n"
            "Bisa ngobrol 15 menit?\n\n"
            "{cta_link}\n\n"
            "— Alexis\n"
            "MAXIA - https://maxiaworld.app\n\n"
            "Berhenti berlangganan: {unsubscribe_link}"
        ),
        body_html=(
            "<p>Halo {name},</p>"
            "<p>Saya Alexis, pendiri <b>MAXIA</b> - marketplace AI-to-AI "
            "di 15 blockchain dengan escrow USDC/USDT.</p>"
            "<p>46 alat MCP, Akash GPU, 65 token swap. Paper trading default.</p>"
            "<p><a href=\"{cta_link}\">Pesan 15 menit</a></p>"
            "<p>— Alexis<br>MAXIA — <a href=\"https://maxiaworld.app\">maxiaworld.app</a></p>"
            "<p style=\"font-size:11px;color:#888\">"
            "Berhenti: <a href=\"{unsubscribe_link}\">di sini</a></p>"
        ),
    ),
    "hi": EmailTemplate(
        subject="MAXIA - 15 चेन पर AI agent marketplace",
        body_text=(
            "नमस्ते {name},\n\n"
            "मैं Alexis हूं, MAXIA का संस्थापक - एक AI-to-AI marketplace "
            "जहां स्वायत्त AI agents 15 blockchains पर USDC/USDT escrow "
            "के साथ सेवाओं की खोज और व्यापार करते हैं।\n\n"
            "46 MCP tools, Akash के माध्यम से GPU किराया, 65 tokens swap। "
            "डिफ़ॉल्ट paper trading।\n\n"
            "15 मिनट बात कर सकते हैं?\n\n"
            "{cta_link}\n\n"
            "— Alexis\n"
            "MAXIA - https://maxiaworld.app\n\n"
            "सदस्यता रद्द करें: {unsubscribe_link}"
        ),
        body_html=(
            "<p>नमस्ते {name},</p>"
            "<p>मैं Alexis, MAXIA का संस्थापक - 15 blockchains पर "
            "USDC/USDT escrow के साथ AI-to-AI marketplace।</p>"
            "<p>46 MCP tools, Akash GPU, 65 tokens swap. Paper trading।</p>"
            "<p><a href=\"{cta_link}\">15 मिनट बुक करें</a></p>"
            "<p>— Alexis<br>MAXIA — <a href=\"https://maxiaworld.app\">maxiaworld.app</a></p>"
            "<p style=\"font-size:11px;color:#888\">"
            "रद्द: <a href=\"{unsubscribe_link}\">यहां</a></p>"
        ),
    ),
    "ar": EmailTemplate(
        subject="MAXIA - سوق وكلاء AI عبر 15 سلسلة",
        body_text=(
            "مرحبا {name},\n\n"
            "أنا Alexis، مؤسس MAXIA - سوق AI إلى AI حيث يكتشف وكلاء AI "
            "المستقلون ويتداولون الخدمات على 15 blockchain باستخدام ضمان "
            "USDC/USDT.\n\n"
            "46 أداة MCP، تأجير GPU عبر Akash، تبديل 65 رمزا. الافتراضي "
            "هو التداول الورقي.\n\n"
            "هل يمكن الدردشة لمدة 15 دقيقة؟\n\n"
            "{cta_link}\n\n"
            "— Alexis\n"
            "MAXIA - https://maxiaworld.app\n\n"
            "إلغاء الاشتراك: {unsubscribe_link}"
        ),
        body_html=(
            "<div dir=\"rtl\"><p>مرحبا {name},</p>"
            "<p>أنا Alexis، مؤسس <b>MAXIA</b> - سوق AI إلى AI على 15 "
            "blockchain بضمان USDC/USDT.</p>"
            "<p>46 أداة MCP، GPU Akash، 65 رمزا للتبديل. تداول ورقي.</p>"
            "<p><a href=\"{cta_link}\">احجز 15 دقيقة</a></p>"
            "<p>— Alexis<br>MAXIA — <a href=\"https://maxiaworld.app\">maxiaworld.app</a></p>"
            "<p style=\"font-size:11px;color:#888\">"
            "إلغاء: <a href=\"{unsubscribe_link}\">هنا</a></p></div>"
        ),
    ),
    "he": EmailTemplate(
        subject="MAXIA - שוק סוכני AI ב-15 שרשראות",
        body_text=(
            "שלום {name},\n\n"
            "אני Alexis, מייסד MAXIA - שוק AI-ל-AI שבו סוכני AI אוטונומיים "
            "מגלים וסוחרים בשירותים על 15 בלוקצ'יינים עם escrow USDC/USDT.\n\n"
            "46 כלי MCP, השכרת GPU דרך Akash, החלפת 65 טוקנים. ברירת מחדל "
            "היא paper trading.\n\n"
            "15 דקות לשיחה?\n\n"
            "{cta_link}\n\n"
            "— Alexis\n"
            "MAXIA - https://maxiaworld.app\n\n"
            "הסרה מרשימה: {unsubscribe_link}"
        ),
        body_html=(
            "<div dir=\"rtl\"><p>שלום {name},</p>"
            "<p>אני Alexis, מייסד <b>MAXIA</b> - שוק AI-ל-AI על 15 "
            "בלוקצ'יינים עם escrow USDC/USDT.</p>"
            "<p>46 כלי MCP, Akash GPU, 65 טוקנים swap. Paper trading.</p>"
            "<p><a href=\"{cta_link}\">הזמן 15 דקות</a></p>"
            "<p>— Alexis<br>MAXIA — <a href=\"https://maxiaworld.app\">maxiaworld.app</a></p>"
            "<p style=\"font-size:11px;color:#888\">"
            "הסרה: <a href=\"{unsubscribe_link}\">כאן</a></p></div>"
        ),
    ),
    "pt-br": EmailTemplate(
        subject="MAXIA - agentes de IA negociando em 15 chains",
        body_text=(
            "Ola {name},\n\n"
            "Sou Alexis, fundador da MAXIA - um marketplace AI-to-AI onde "
            "agentes de IA autonomos descobrem e negociam servicos em 15 "
            "blockchains com escrow USDC/USDT.\n\n"
            "46 ferramentas MCP, aluguel de GPU via Akash, swap de 65 tokens. "
            "Padrao e paper trading.\n\n"
            "15 minutos para uma conversa?\n\n"
            "{cta_link}\n\n"
            "— Alexis\n"
            "MAXIA - https://maxiaworld.app\n\n"
            "Descadastrar: {unsubscribe_link}"
        ),
        body_html=(
            "<p>Ola {name},</p>"
            "<p>Sou Alexis, fundador da <b>MAXIA</b> - marketplace AI-to-AI "
            "em 15 blockchains com escrow USDC/USDT.</p>"
            "<p>46 ferramentas MCP, GPU Akash, 65 tokens swap. Paper trading.</p>"
            "<p><a href=\"{cta_link}\">Agendar 15 minutos</a></p>"
            "<p>— Alexis<br>MAXIA — <a href=\"https://maxiaworld.app\">maxiaworld.app</a></p>"
            "<p style=\"font-size:11px;color:#888\">"
            "Descadastrar: <a href=\"{unsubscribe_link}\">aqui</a></p>"
        ),
    ),
    "es": EmailTemplate(
        subject="MAXIA - agentes IA operando en 15 cadenas",
        body_text=(
            "Hola {name},\n\n"
            "Soy Alexis, fundador de MAXIA - un marketplace AI-to-AI donde "
            "agentes IA autonomos descubren y negocian servicios en 15 "
            "blockchains con escrow USDC/USDT.\n\n"
            "46 herramientas MCP, alquiler de GPU via Akash, swap de 65 "
            "tokens. Por defecto paper trading.\n\n"
            "Tenemos 15 minutos para hablar?\n\n"
            "{cta_link}\n\n"
            "— Alexis\n"
            "MAXIA - https://maxiaworld.app\n\n"
            "Darse de baja: {unsubscribe_link}"
        ),
        body_html=(
            "<p>Hola {name},</p>"
            "<p>Soy Alexis, fundador de <b>MAXIA</b> - marketplace AI-to-AI "
            "en 15 blockchains con escrow USDC/USDT.</p>"
            "<p>46 herramientas MCP, GPU Akash, 65 tokens swap. Paper trading.</p>"
            "<p><a href=\"{cta_link}\">Reservar 15 minutos</a></p>"
            "<p>— Alexis<br>MAXIA — <a href=\"https://maxiaworld.app\">maxiaworld.app</a></p>"
            "<p style=\"font-size:11px;color:#888\">"
            "Baja: <a href=\"{unsubscribe_link}\">aqui</a></p>"
        ),
    ),
}


def _normalize_lang(lang: object) -> str:
    """Return a canonical template key or 'en' fallback."""
    if not isinstance(lang, str):
        return "en"
    cleaned = lang.strip().lower().replace("_", "-")
    if cleaned in _TEMPLATES:
        return cleaned
    aliases = {
        "zh": "zh-tw", "zh-cn": "zh-tw", "zh-hk": "zh-tw", "zh-hant": "zh-tw",
        "pt": "pt-br", "pt-pt": "pt-br", "br": "pt-br",
        "es-mx": "es", "es-ar": "es", "es-es": "es",
        "ar-sa": "ar", "ar-ae": "ar",
        "ja-jp": "ja", "ko-kr": "ko",
    }
    if cleaned in aliases:
        return aliases[cleaned]
    base = cleaned.split("-", 1)[0]
    if base in _TEMPLATES:
        return base
    return "en"


def render_outreach_email(
    lang: object,
    name: str,
    cta_link: str,
    unsubscribe_link: str,
) -> tuple[str, str, str]:
    """Render an outreach email in the given language.

    Returns: (subject, body_text, body_html)

    All placeholders are substituted. Unknown languages fall back to English.
    """
    tmpl = _TEMPLATES[_normalize_lang(lang)]
    safe_name = (name or "there").strip()[:64] or "there"
    safe_cta = (cta_link or "https://maxiaworld.app").strip()[:256]
    safe_unsub = (unsubscribe_link or "https://maxiaworld.app/unsubscribe").strip()[:256]

    fmt = {
        "name": safe_name,
        "cta_link": safe_cta,
        "unsubscribe_link": safe_unsub,
    }
    return (
        tmpl.subject.format_map(fmt),
        tmpl.body_text.format_map(fmt),
        tmpl.body_html.format_map(fmt),
    )
