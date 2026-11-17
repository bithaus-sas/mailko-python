"""Django's email backend over Mailko's API: ``send_mail``, ``EmailMessage``
and every library that sends through Django go out by ``POST /v1/emails``.

::

    EMAIL_BACKEND = "mailko.contrib.django.EmailBackend"
    MAILKO_API_KEY = env("MAILKO_API_KEY")

One request per message. What Django has becomes the API's fields: the body
and its HTML alternative, ``cc``, ``bcc``, ``reply_to`` (also a ``Reply-To``
header), attachments (tuples or MIME parts) and the headers Mailko allows —
any other header is left out, since Mailko would refuse the whole email.

What Django has no word for goes as attributes of the message::

    message.mailko_tags = {"order": "1234"}      # echoed in every webhook
    message.mailko_metadata = {...}
    message.mailko_stream = "marketing"          # default: transactional
    message.mailko_template = {"id": "welcome", "variables": {...}}
    message.mailko_idempotency_key = "order-1234-receipt"

A stable ``mailko_idempotency_key`` (one per logical email, the same on every
retry of a task) is what makes a retry safe: Mailko never sends twice under
one key. After the send, ``message.mailko_id`` and ``message.mailko_status``
say what Mailko answered.

Errors are the library's (``mailko.APIError`` and its subclasses,
``MessageRejectedError`` before the call, ``SuppressedError`` when every
recipient is on the project's suppression list), unless ``fail_silently``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from django.core.mail.backends.base import BaseEmailBackend

from mailko.client import Mailko
from mailko.constants import ALLOWED_HEADERS, MAX_LINE
from mailko.contrib.django import conf
from mailko.errors import MailkoError, MessageRejectedError, SuppressedError
from mailko.message import (
    UNNAMED_ATTACHMENT,
    Attachment,
    address_list,
    bare_address,
    build_email,
    check_email,
    single_line,
)

logger = logging.getLogger("mailko")


def _domain(address: Any) -> str:
    return bare_address(address).rpartition("@")[2]


def _content_of(message: Any) -> tuple[str, str]:
    """``(html, text)``: the body (plain or, with ``content_subtype = "html"``,
    HTML) plus the first alternative of the other kind. Other alternatives
    (``text/calendar``…) have no place in Mailko's API and are left out."""
    html, text = "", ""
    if getattr(message, "content_subtype", "plain") == "html":
        html = message.body or ""
    else:
        text = message.body or ""
    for alternative in getattr(message, "alternatives", None) or ():
        content, mimetype = alternative[0], str(alternative[1] or "").split(";", 1)[0].strip().lower()
        if isinstance(content, bytes):
            content = content.decode("utf-8", "replace")
        if mimetype == "text/html" and not html:
            html = content or ""
        elif mimetype == "text/plain" and not text:
            text = content or ""
        elif mimetype not in ("text/html", "text/plain"):
            logger.warning("Alternative %s left out: Mailko sends only html and text", mimetype)
    return html, text


def _attachment(item: Any) -> Attachment:
    """A Django attachment: a tuple (``EmailAttachment``) or a MIME part."""
    if hasattr(item, "get_content_type"):
        name = item.get_filename() or UNNAMED_ATTACHMENT
        mimetype = item.get_content_type()
        if mimetype == "message/rfc822":
            content = item.get_payload(0).as_bytes()
        else:
            content = item.get_payload(decode=True) or b""
        return Attachment(name, content, mimetype)
    name, content, mimetype = item[0], item[1], item[2] if len(item) > 2 else ""
    return Attachment(name or UNNAMED_ATTACHMENT, content if content is not None else b"", mimetype or "")


def _headers_of(message: Any) -> dict[str, str]:
    allowed = {}
    for name, value in (getattr(message, "extra_headers", None) or {}).items():
        line = single_line(value)
        if str(name).lower() in ALLOWED_HEADERS and line and len(line) <= MAX_LINE:
            allowed[str(name)] = line
        elif str(name).lower() != "reply-to":
            logger.debug("Header %s left out: Mailko does not let a sender set it", name)
    return allowed


def _reply_to(message: Any) -> list[str]:
    reply_to = address_list(getattr(message, "reply_to", None))
    if reply_to:
        return reply_to
    for name, value in (getattr(message, "extra_headers", None) or {}).items():
        if str(name).lower() == "reply-to" and value:
            return [single_line(value)]
    return []


class EmailBackend(BaseEmailBackend):
    """``EMAIL_BACKEND = "mailko.contrib.django.EmailBackend"``, or a mailer
    of ``MAILERS`` (Django 6.1+) whose ``OPTIONS`` may say ``api_key`` and
    ``timeout``. ``api_key=`` overrides ``MAILKO_API_KEY`` for this connection."""

    def __init__(
        self,
        fail_silently: bool = False,
        *,
        api_key: str | None = None,
        timeout: float | tuple[float, float] | None = None,
        **kwargs: Any,
    ) -> None:
        # Django 6.1 passes ``alias`` (and drops unknown options on its
        # own); older ones take and ignore what they do not know.
        super().__init__(**kwargs)
        # Our own attribute: Django 6.1 deprecates the base class's.
        self.fail_silently = fail_silently
        self.api_key = api_key
        self.timeout = timeout
        self._clients: dict[str, Mailko] = {}

    # -- what to send ---------------------------------------------------------

    def message_body(self, message: Any) -> dict[str, Any]:
        """The JSON of ``message`` for ``POST /v1/emails``. Override to add to it."""
        template = getattr(message, "mailko_template", None)
        # A template brings its own content: Mailko refuses both at once.
        html, text = ("", "") if template else _content_of(message)
        body = build_email(
            from_=str(message.from_email or ""),
            to=message.to,
            cc=getattr(message, "cc", None),
            bcc=getattr(message, "bcc", None),
            reply_to=_reply_to(message),
            subject=message.subject or "",
            html=html,
            text=text,
            template=template,
            headers=_headers_of(message),
            attachments=[_attachment(item) for item in getattr(message, "attachments", None) or ()],
            tags=getattr(message, "mailko_tags", None),
            metadata=getattr(message, "mailko_metadata", None),
            stream=getattr(message, "mailko_stream", None),
        )
        if len(body.get("subject") or "") > MAX_LINE:
            body["subject"] = body["subject"][:MAX_LINE]
        return body

    def api_key_for(self, message: Any) -> str:
        """The key of the project that sends ``message``: the one of its
        sender's domain in ``MAILKO_API_KEYS_BY_DOMAIN``, else ``MAILKO_API_KEY``."""
        if self.api_key:
            return self.api_key
        by_domain = conf.keys_by_domain()
        if by_domain:
            key = by_domain.get(_domain(message.from_email))
            if key:
                return key
        return str(conf.get("MAILKO_API_KEY") or "")

    def idempotency_key_for(self, message: Any) -> str:
        """The message's ``mailko_idempotency_key`` (the same on every attempt
        of this email, with the same content), or a fresh one."""
        stable = getattr(message, "mailko_idempotency_key", None)
        if stable:
            return str(stable)[:255]
        return str(uuid.uuid4())

    # -- sending --------------------------------------------------------------

    def client_for(self, api_key: str) -> Mailko:
        client = self._clients.get(api_key)
        if client is None:
            client = Mailko(
                api_key,
                base_url=conf.get("MAILKO_API_URL"),
                timeout=self.timeout or conf.get("MAILKO_TIMEOUT"),
                max_retries=conf.get("MAILKO_MAX_RETRIES"),
                max_retry_after=conf.get("MAILKO_MAX_RETRY_AFTER"),
            )
            self._clients[api_key] = client
        return client

    def close(self) -> None:
        for client in self._clients.values():
            client.session.close()
        self._clients = {}

    def send_messages(self, email_messages: Any) -> int:
        sent = 0
        suppressed: SuppressedError | None = None
        for message in email_messages or ():
            try:
                sent += self._send(message)
            except SuppressedError as exc:
                # Mailko took it and had nobody to send it to: the rest of
                # the batch still goes; it is raised once they did.
                suppressed = suppressed or exc
            except (MailkoError, ValueError):
                # ValueError: a missing or malformed key (Mailko(...) refuses it).
                if not self.fail_silently:
                    raise
        if suppressed is not None and not self.fail_silently:
            raise suppressed
        return sent

    def _send(self, message: Any) -> int:
        if not message.recipients():
            return 0
        if not message.to:
            # Moving a blind copy to «to» would show it to everybody else.
            raise MessageRejectedError(
                "El correo necesita al menos un destinatario en «para»; solo tenía copias.", code="to_required"
            )
        body = self.message_body(message)
        check_email(body)
        api_key = self.api_key_for(message)
        if not api_key:
            raise MessageRejectedError("Falta MAILKO_API_KEY: el envío por Mailko no está configurado.", code="no_key")
        key = self.idempotency_key_for(message)
        # The same key with the same body is answered with the first answer
        # (Mailko replays it); with another body it is an IdempotencyError —
        # never read as «already sent»: it may be another email.
        answer = self.client_for(api_key).emails.send_raw(body, idempotency_key=key) or {}
        message.mailko_id = answer.get("id")
        message.mailko_status = answer.get("status")
        message.mailko_response = answer
        if answer.get("status") == "suppressed":
            raise SuppressedError(
                "No se envió: Mailko tiene bloqueadas todas las direcciones de este correo "
                "(rebotaron o lo marcaron como spam antes).",
                email=answer,
            )
        return 1
