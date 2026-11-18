"""Mailko for Python.

::

    import mailko

    client = mailko.Mailko("mk_live_…")
    client.emails.send(
        from_="Tienda <pedidos@tienda.co>",
        to="cliente@correo.com",
        subject="Tu pedido #1234",
        html="<p>Gracias por tu compra</p>",
    )

Django: ``EMAIL_BACKEND = "mailko.contrib.django.EmailBackend"`` (see the README).
"""

from mailko._version import __version__
from mailko.client import Mailko
from mailko.constants import API_VERSION, MARKETING, TRANSACTIONAL
from mailko.errors import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    BadRequestError,
    ConfigurationError,
    ConflictError,
    IdempotencyError,
    MailkoError,
    MessageRejectedError,
    NotFoundError,
    PaymentRequiredError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    SignatureVerificationError,
    SuppressedError,
)
from mailko.message import Attachment, build_email, check_email
from mailko.webhooks import construct_event, is_valid_signature, verify_signature

__all__ = [
    "API_VERSION",
    "MARKETING",
    "TRANSACTIONAL",
    "APIConnectionError",
    "APIError",
    "Attachment",
    "AuthenticationError",
    "BadRequestError",
    "ConfigurationError",
    "ConflictError",
    "IdempotencyError",
    "Mailko",
    "MailkoError",
    "MessageRejectedError",
    "NotFoundError",
    "PaymentRequiredError",
    "PermissionDeniedError",
    "RateLimitError",
    "ServerError",
    "SignatureVerificationError",
    "SuppressedError",
    "__version__",
    "build_email",
    "check_email",
    "construct_event",
    "is_valid_signature",
    "verify_signature",
]
