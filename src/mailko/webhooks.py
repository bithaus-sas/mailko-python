"""Checking that a webhook comes from Mailko.

Mailko signs each delivery with the endpoint's secret (``whsec_…``)::

    Mailko-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<raw body>">

Verify the RAW body as it arrived (never a re-serialised one), refuse a ``t``
older than five minutes, and deduplicate by the event's ``id``: Mailko
delivers at least once, with the same id on every retry.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Iterable
from typing import Any

from mailko.constants import WEBHOOK_TOLERANCE_SECONDS
from mailko.errors import SignatureVerificationError


def _secrets(secret: str | Iterable[str] | None) -> list[str]:
    if secret is None:
        return []
    items = [secret] if isinstance(secret, str) else list(secret)
    return [item.strip() for item in items if isinstance(item, str) and item.strip()]


def _as_bytes(payload: bytes | str) -> bytes:
    return payload.encode() if isinstance(payload, str) else bytes(payload)


def parse_signature(header: str | None) -> tuple[int | None, list[str]]:
    """``(timestamp, [v1 signatures])`` of a ``Mailko-Signature`` header."""
    timestamp: int | None = None
    signatures: list[str] = []
    for part in str(header or "").split(","):
        name, _, value = part.strip().partition("=")
        value = value.strip()
        if name == "t":
            try:
                timestamp = int(value)
            except ValueError:
                timestamp = None
        elif name == "v1" and value:
            signatures.append(value)
    return timestamp, signatures


def sign(payload: bytes | str, secret: str, timestamp: int | None = None) -> str:
    """The ``Mailko-Signature`` Mailko would send for ``payload`` (for tests)."""
    moment = int(time.time()) if timestamp is None else int(timestamp)
    digest = hmac.new(secret.encode(), str(moment).encode() + b"." + _as_bytes(payload), hashlib.sha256).hexdigest()
    return f"t={moment},v1={digest}"


def verify_signature(
    payload: bytes | str,
    header: str | None,
    secret: str | Iterable[str],
    *,
    tolerance: int = WEBHOOK_TOLERANCE_SECONDS,
    now: float | None = None,
) -> None:
    """Raise ``SignatureVerificationError`` unless ``header`` signs ``payload``
    with ``secret`` recently. ``secret`` may be a list: one per Mailko project
    posting to the same URL, or the old and the new one while it rotates."""
    secrets = _secrets(secret)
    if not secrets:
        raise SignatureVerificationError("Falta el secreto de firma del webhook (whsec_…).", code="no_secret")
    timestamp, signatures = parse_signature(header)
    if timestamp is None or not signatures:
        raise SignatureVerificationError("La cabecera Mailko-Signature falta o no se entiende.", code="bad_header")
    current = time.time() if now is None else now
    if tolerance and abs(current - timestamp) > tolerance:
        raise SignatureVerificationError("La firma venció: el aviso tiene más de 5 minutos.", code="expired")
    signed = str(timestamp).encode() + b"." + _as_bytes(payload)
    for item in secrets:
        expected = hmac.new(item.encode(), signed, hashlib.sha256).hexdigest()
        # Bytes: a str with non-ASCII characters makes compare_digest raise.
        if any(hmac.compare_digest(expected.encode(), candidate.encode()) for candidate in signatures):
            return
    raise SignatureVerificationError("La firma no corresponde: el aviso no es de Mailko.", code="mismatch")


def is_valid_signature(payload: bytes | str, header: str | None, secret: str | Iterable[str], **kwargs: Any) -> bool:
    """``verify_signature`` as a boolean."""
    try:
        verify_signature(payload, header, secret, **kwargs)
    except SignatureVerificationError:
        return False
    return True


def construct_event(
    payload: bytes | str,
    header: str | None,
    secret: str | Iterable[str],
    *,
    tolerance: int = WEBHOOK_TOLERANCE_SECONDS,
) -> dict[str, Any]:
    """Verify, then parse: the event (``id``, ``type``, ``created_at``,
    ``data.object``…). Raises ``SignatureVerificationError`` for a forged,
    stale or malformed delivery."""
    verify_signature(payload, header, secret, tolerance=tolerance)
    try:
        event = json.loads(_as_bytes(payload))
    except ValueError as exc:
        raise SignatureVerificationError("El aviso no es JSON.", code="bad_payload") from exc
    if not isinstance(event, dict) or not event.get("id") or not event.get("type"):
        raise SignatureVerificationError("El aviso no es un evento de Mailko.", code="bad_payload")
    return event
