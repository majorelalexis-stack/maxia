"""MAXIA i18n — Multi-language support"""

TRANSLATIONS = {
    "en": {
        "welcome": "Welcome to MAXIA — AI-to-AI Marketplace",
        "register_success": "Registration successful! Your API key: {api_key}",
        "service_listed": "Service '{name}' listed at ${price} USDC",
        "buy_success": "Purchased '{service}' for ${price} USDC",
        "insufficient_funds": "Insufficient funds",
        "invalid_key": "Invalid API key",
        "rate_limited": "Rate limit exceeded. Try again later.",
        "not_found": "Not found",
        "error": "An error occurred: {error}",
    },
    "fr": {
        "welcome": "Bienvenue sur MAXIA — Marketplace IA-to-IA",
        "register_success": "Inscription reussie ! Votre cle API : {api_key}",
        "service_listed": "Service '{name}' liste a {price} USDC",
        "buy_success": "Achat de '{service}' pour {price} USDC",
        "insufficient_funds": "Fonds insuffisants",
        "invalid_key": "Cle API invalide",
        "rate_limited": "Limite de requetes atteinte. Reessayez plus tard.",
        "not_found": "Non trouve",
        "error": "Une erreur est survenue : {error}",
    },
    "es": {
        "welcome": "Bienvenido a MAXIA — Marketplace IA-a-IA",
        "register_success": "Registro exitoso! Tu clave API: {api_key}",
        "service_listed": "Servicio '{name}' listado a ${price} USDC",
        "buy_success": "Compra de '{service}' por ${price} USDC",
        "insufficient_funds": "Fondos insuficientes",
        "invalid_key": "Clave API invalida",
        "rate_limited": "Limite de solicitudes excedido. Intente mas tarde.",
        "not_found": "No encontrado",
        "error": "Ocurrio un error: {error}",
    },
    "zh": {
        "welcome": "MAXIA — AI-to-AI",
        "register_success": "API: {api_key}",
        "service_listed": "'{name}' ${price} USDC",
        "buy_success": "'{service}' {price} USDC",
        "insufficient_funds": "",
        "invalid_key": "API",
        "rate_limited": "",
        "not_found": "",
        "error": ": {error}",
    },
}

SUPPORTED_LANGS = list(TRANSLATIONS.keys())

def t(key: str, lang: str = "en", **kwargs) -> str:
    """Translate a key to the given language."""
    lang = lang if lang in TRANSLATIONS else "en"
    text = TRANSLATIONS[lang].get(key, TRANSLATIONS["en"].get(key, key))
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text

def detect_lang(accept_language: str = "") -> str:
    """Detect language from Accept-Language header."""
    if not accept_language:
        return "en"
    for part in accept_language.split(","):
        lang = part.strip().split(";")[0].strip()[:2].lower()
        if lang in SUPPORTED_LANGS:
            return lang
    return "en"
