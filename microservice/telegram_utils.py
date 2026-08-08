import os
import logging
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("inboxpilot.telegram")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

# Legacy Telegram Markdown (parse_mode="Markdown") treats these as formatting
# control characters. Any of them appearing unescaped in dynamic content
# (email subjects, sender names, summaries, etc.) causes Telegram to reject
# the whole message with a 400 "can't parse entities" error.
_MARKDOWN_SPECIAL_CHARS = ["_", "*", "`", "["]


def escape_markdown(text: str) -> str:
    """
    Escapes legacy Telegram Markdown special characters in untrusted/dynamic
    text (email subjects, sender/company names, summaries) so they render as
    literal characters instead of breaking message formatting.

    Call this on every piece of dynamic content interpolated into a
    Markdown-formatted Telegram message. Static template text (the parts you
    write yourself, like "*Low Confidence Item*") should NOT be passed
    through this, or its intentional formatting will be escaped too.
    """
    if not text:
        return text
    text = str(text)
    for char in _MARKDOWN_SPECIAL_CHARS:
        text = text.replace(char, f"\\{char}")
    return text


def send_telegram_message(text: str) -> dict:
    """
    Sends a Telegram message. Returns Telegram's response JSON.
    Unlike before, this now logs (rather than silently swallows) any
    delivery failure, so a malformed/rejected message doesn't disappear
    without a trace.
    """
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(TELEGRAM_API_URL, json=payload, timeout=10)
    except requests.RequestException as e:
        logger.error(f"Telegram request failed (network/timeout): {e}")
        return {"ok": False, "error": str(e)}

    try:
        result = response.json()
    except ValueError:
        logger.error(f"Telegram returned non-JSON response: {response.text}")
        return {"ok": False, "error": "non_json_response", "raw": response.text}

    if not result.get("ok"):
        logger.error(
            f"Telegram send failed (status {response.status_code}): "
            f"{result.get('description', result)}"
        )

    return result