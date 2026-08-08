"""Outbound SMS / WhatsApp helpers.

This module used to build a Twilio client and send a message at import time,
which meant importing it billed the account and texted a hardcoded number.
It now exposes explicit functions that read credentials from settings and
fail soft: a messaging outage must never take a page down with it.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def _client():
    """Return a configured Twilio client, or None when messaging is off."""
    if not settings.TWILIO_ENABLED:
        return None
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN):
        logger.warning("Twilio is enabled but credentials are missing; skipping send.")
        return None
    from twilio.rest import Client  # imported lazily so the app boots without twilio
    return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def format_number(number):
    """Normalise a stored phone number into E.164 form."""
    if not number:
        return None
    digits = str(number).strip().replace(" ", "").replace("-", "")
    if digits.startswith("+"):
        return digits
    return f"{settings.SMS_DEFAULT_COUNTRY_CODE}{digits}"


def send_sms(to, body):
    """Send an SMS. Returns the message SID, or None if nothing was sent."""
    return _send(to, body, settings.TWILIO_FROM_NUMBER)


def send_whatsapp(to, body):
    """Send a WhatsApp message. Returns the message SID, or None."""
    to = format_number(to)
    if not to:
        return None
    return _send(f"whatsapp:{to}", body, settings.TWILIO_WHATSAPP_FROM, already_formatted=True)


def _send(to, body, from_, already_formatted=False):
    client = _client()
    if client is None:
        logger.info("Messaging disabled - would have sent to %s: %s", to, body)
        return None
    if not already_formatted:
        to = format_number(to)
    if not (to and from_):
        logger.warning("Missing sender or recipient; skipping send.")
        return None
    try:
        message = client.messages.create(body=body, from_=from_, to=to)
        logger.info("Sent message %s to %s", message.sid, to)
        return message.sid
    except Exception:
        # A failed notification must not break the request that triggered it.
        logger.exception("Failed to send message to %s", to)
        return None
