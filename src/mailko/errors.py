"""Every error the library raises.

Mailko answers every refusal with the same envelope::

    {"error": {"type": "...", "code": "...", "message": "...", "request_id": "req_…", "fields": {...}}}

``message`` is a sentence in Spanish written for the person who sent the
request; ``code`` is stable and is what code should branch on. Each HTTP
status maps to one class, so ``except mailko.RateLimitError`` reads well, and
``exc.code`` tells the rest (``domain_not_verified``, ``insufficient_balance``…).
"""

from __future__ import annotations

import contextlib
from typing import Any

import requests


class MailkoError(Exception):
    """Base of everything this library raises. ``str(exc)`` is a sentence."""

    def __init__(self, message: str = "", *, code: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class APIConnectionError(MailkoError):
    """Mailko did not answer (network, timeout). Safe to retry with the same
    ``idempotency_key``."""


class SignatureVerificationError(MailkoError):
    """A webhook whose ``Mailko-Signature`` is missing, wrong or too old."""


class MessageRejectedError(MailkoError):
    """The library refused the email before calling Mailko: Mailko would
    have refused it anyway (no recipient, too many, too large…)."""


class SuppressedError(MailkoError):
    """Mailko took the request and sent nothing: every recipient is on the
    project's suppression list (a hard bounce or a complaint before).
    ``email`` is Mailko's answer (``status: "suppressed"``)."""

    def __init__(self, message: str = "", *, email: dict[str, Any] | None = None) -> None:
        super().__init__(message, code="suppressed")
        self.email = email or {}


class APIError(MailkoError):
    """Mailko answered with an error. ``status``, ``code``, ``type``,
    ``request_id`` (quote it to support), ``fields`` (what was wrong with each
    field of a ``validation_error``), ``extra`` (what else the error says:
    ``required_scope``, a batch's ``index`` and ``accepted``…) and ``body``
    (the whole answer)."""

    def __init__(
        self,
        message: str = "",
        *,
        status: int | None = None,
        code: str = "",
        type: str = "",  # noqa: A002 — the API's own name
        request_id: str | None = None,
        fields: dict[str, Any] | None = None,
        body: Any = None,
        headers: Any = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.extra = extra or {}
        self.status = status
        self.type = type
        self.request_id = request_id
        self.fields = fields or {}
        self.body = body
        self.headers = dict(headers or {})

    def __str__(self) -> str:
        text = self.message or f"Mailko respondió {self.status}."
        if self.request_id:
            text = f"{text} (request_id: {self.request_id})"
        return text


class BadRequestError(APIError):
    """400, 413, 415, 422: the request itself is wrong; fix it, do not retry."""


class AuthenticationError(APIError):
    """401: the key is missing, wrong, revoked or expired."""


class PaymentRequiredError(APIError):
    """402: the plan or the balance does not allow it (``insufficient_balance``,
    ``subscription_read_only``…)."""


class PermissionDeniedError(APIError):
    """403: the key or the project may not do this (``missing_scope``,
    ``domain_not_verified``, ``workspace_paused``…)."""


class NotFoundError(APIError):
    """404."""


class ConflictError(APIError):
    """409 (``confirmation_required``, ``suppression_not_removable``…)."""


class IdempotencyError(APIError):
    """The ``Idempotency-Key`` is still running (409) or was used with
    another request (422 ``idempotency_key_reused``)."""


class RateLimitError(APIError):
    """429: too fast (``rate_limited``), over the project's limit
    (``limit_exceeded``) or the month's (``monthly_quota_exceeded``).
    ``retry_after`` is Mailko's ``Retry-After`` in seconds, when it said one."""

    def __init__(self, *args: Any, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class ServerError(APIError):
    """5xx: Mailko's own trouble. Safe to retry with the same key."""


_BY_STATUS: dict[int, type[APIError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    402: PaymentRequiredError,
    403: PermissionDeniedError,
    404: NotFoundError,
    405: BadRequestError,
    409: ConflictError,
    410: NotFoundError,
    413: BadRequestError,
    415: BadRequestError,
    422: BadRequestError,
    429: RateLimitError,
}


_ENVELOPE_KEYS = frozenset({"type", "code", "message", "request_id", "fields"})


def parse_retry_after(value: Any) -> float | None:
    """``Retry-After`` in seconds (Mailko sends seconds, never a date)."""
    with contextlib.suppress(TypeError, ValueError):
        seconds = float(value)
        if seconds >= 0:
            return seconds
    return None


def error_from_response(response: requests.Response) -> APIError:
    """The ``APIError`` subclass for a Mailko refusal."""
    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        error = {}
    code = str(error.get("code") or "")
    message = str(error.get("message") or "").strip()
    if not message:
        message = f"Mailko respondió {response.status_code} sin explicación."
    status = response.status_code
    if code.startswith("idempotency_key_") and status in (409, 422):
        cls: type[APIError] = IdempotencyError
    elif status >= 500:
        cls = ServerError
    else:
        cls = _BY_STATUS.get(status, APIError)
    kwargs: dict[str, Any] = {
        "status": status,
        "code": code,
        "type": str(error.get("type") or ""),
        "request_id": error.get("request_id") or response.headers.get("X-Request-Id"),
        "fields": error.get("fields") if isinstance(error.get("fields"), dict) else None,
        "body": body,
        "headers": response.headers,
        "extra": {key: value for key, value in error.items() if key not in _ENVELOPE_KEYS},
    }
    if cls is RateLimitError:
        kwargs["retry_after"] = parse_retry_after(response.headers.get("Retry-After"))
    return cls(message, **kwargs)
