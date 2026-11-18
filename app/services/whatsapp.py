"""WhatsApp Cloud API (Meta): send messages from the business number."""

import hashlib
import hmac
import logging

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)
# WhatsApp caps a text message at 4096 characters, a button title at 20.
MAX_TEXT = 4096


def enabled() -> bool:
    return bool(settings.whatsapp_access_token and settings.whatsapp_phone_number_id)


def valid_signature(body: bytes, header: str) -> bool:
    """Meta signs each webhook call with the app secret (X-Hub-Signature-256: sha256=<hex>)."""
    if not settings.whatsapp_app_secret or not header.startswith("sha256="):
        return False
    expected = hmac.new(settings.whatsapp_app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[7:], expected)


def call(payload: dict) -> dict | None:
    if not enabled():
        return None
    try:
        resp = httpx.post(
            f"https://graph.facebook.com/{settings.whatsapp_api_version}/"
            f"{settings.whatsapp_phone_number_id}/messages",
            json={"messaging_product": "whatsapp", **payload},
            headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"},
            timeout=10,
        )
        body = resp.json()
        if resp.status_code >= 400 or "error" in body:
            log.warning("WhatsApp send failed: %s", body.get("error", {}).get("message", resp.status_code))
            return None
        return body
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("WhatsApp send failed: %s", exc)
        return None


def send_text(to: str, text: str) -> bool:
    payload = {"to": to, "type": "text", "text": {"body": text[:MAX_TEXT], "preview_url": False}}
    return call(payload) is not None


def send_buttons(to: str, text: str, buttons: list[tuple[str, str]]) -> bool:
    """Up to 3 reply buttons, as (id, title)."""
    payload = {
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": text[:1024]},
            "action": {
                "buttons": [{"type": "reply", "reply": {"id": i, "title": t[:20]}} for i, t in buttons[:3]]
            },
        },
    }
    return call(payload) is not None


def send_template(to: str, name: str, language: str, params: list[str]) -> bool:
    payload = {
        "to": to,
        "type": "template",
        "template": {
            "name": name,
            "language": {"code": language},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}],
        },
    }
    return call(payload) is not None


def mark_read(message_id: str) -> None:
    """Blue ticks plus the "typing..." bubble while the assistant thinks."""
    call({"status": "read", "message_id": message_id, "typing_indicator": {"type": "text"}})
