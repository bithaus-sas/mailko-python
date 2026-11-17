"""The settings the Django integration reads, with their defaults.

=================================  =============================================
``MAILKO_API_KEY``                 The project's key (``mk_live_…``). Required to send.
``MAILKO_API_KEYS_BY_DOMAIN``      ``{"negocios.tienda.co": "mk_live_…"}``: another
                                   project's key for what leaves from that domain.
``MAILKO_API_URL``                 ``https://api.mailko.co``.
``MAILKO_TIMEOUT``                 Seconds, or ``(connect, read)``. ``(5, 30)``.
``MAILKO_MAX_RETRIES``             Retries of a send Mailko could not take now. ``2``.
``MAILKO_MAX_RETRY_AFTER``         Longest ``Retry-After`` waited inside a send. ``10``.
``MAILKO_WEBHOOK_SECRET``          ``whsec_…``; a list (or commas) for several projects.
``MAILKO_WEBHOOK_TOLERANCE``       Seconds a signature stays valid. ``300``.
``MAILKO_WEBHOOK_DEDUP_SECONDS``   How long an event id is remembered. ``86400``; 0 = never.
=================================  =============================================
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

from mailko.constants import DEFAULT_BASE_URL, WEBHOOK_TOLERANCE_SECONDS

DEFAULTS: dict[str, Any] = {
    "MAILKO_API_KEY": "",
    "MAILKO_API_KEYS_BY_DOMAIN": {},
    "MAILKO_API_URL": DEFAULT_BASE_URL,
    "MAILKO_TIMEOUT": (5.0, 30.0),
    "MAILKO_MAX_RETRIES": 2,
    "MAILKO_MAX_RETRY_AFTER": 10.0,
    "MAILKO_WEBHOOK_SECRET": "",
    "MAILKO_WEBHOOK_TOLERANCE": WEBHOOK_TOLERANCE_SECONDS,
    "MAILKO_WEBHOOK_DEDUP_SECONDS": 24 * 60 * 60,
}


def get(name: str) -> Any:
    return getattr(settings, name, DEFAULTS[name])


def webhook_secrets() -> list[str]:
    value = get("MAILKO_WEBHOOK_SECRET")
    if isinstance(value, str):
        value = value.split(",")
    return [str(item).strip() for item in value or () if str(item or "").strip()]


def keys_by_domain() -> dict[str, str]:
    value = get("MAILKO_API_KEYS_BY_DOMAIN") or {}
    return {str(domain).strip().lower(): str(key).strip() for domain, key in value.items() if domain and key}
