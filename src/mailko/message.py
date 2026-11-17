"""Building the body of ``POST /emails`` and checking it before the call.

``build_email`` takes Python values (bytes for an attachment, a string or a
list for addresses) and returns the JSON Mailko reads. ``check_email``
refuses, with a sentence, what Mailko would refuse anyway: no «to», more
than 50 recipients, more than 20 attachments, more than 10 MB, a header
outside the allowed ones.
"""

from __future__ import annotations

import base64
import mimetypes
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from email.utils import getaddresses
from typing import Any

from mailko.constants import (
    ALLOWED_HEADERS,
    MAX_ATTACHMENTS,
    MAX_CONTENT_TYPE,
    MAX_FILENAME,
    MAX_LINE,
    MAX_MESSAGE_BYTES,
    MAX_RECIPIENTS,
    MAX_TAG_VALUE,
    MAX_TAGS,
)
from mailko.errors import MessageRejectedError

_CONTENT_TYPE = re.compile(r"^[A-Za-z0-9][\w.+-]*/[A-Za-z0-9][\w.+-]*$")
_TAG_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_LINE_BREAK = re.compile(r"[\r\n\x00]+\s*")
UNNAMED_ATTACHMENT = "adjunto"


@dataclass(frozen=True)
class Attachment:
    """A file for an email. ``content`` is the raw bytes (a ``str`` is
    encoded as UTF-8); ``content_type`` is guessed from the name when left out."""

    filename: str
    content: bytes | str
    content_type: str = ""

    def as_json(self) -> dict[str, str]:
        content = self.content.encode() if isinstance(self.content, str) else bytes(self.content or b"")
        return {
            "filename": single_line(self.filename)[:MAX_FILENAME] or UNNAMED_ATTACHMENT,
            "content": base64.b64encode(content).decode(),
            "content_type": content_type_of(self.filename, self.content_type),
        }


def single_line(value: Any) -> str:
    return _LINE_BREAK.sub(" ", str(value or "")).strip()


def content_type_of(filename: Any, content_type: Any = "") -> str:
    """A type Mailko accepts: the one given (without parameters), else the
    one the name suggests, else ``application/octet-stream``."""
    value = str(content_type or "").split(";", 1)[0].strip()
    if not value:
        value = mimetypes.guess_type(str(filename or ""))[0] or ""
    if not value or len(value) > MAX_CONTENT_TYPE or not _CONTENT_TYPE.match(value):
        value = "application/octet-stream"
    return value.lower()


def address_list(value: Any) -> list[str]:
    """``"a@x.co"``, ``"A <a@x.co>, b@x.co"`` or a list of them → a list."""
    if value is None:
        return []
    items = [value] if isinstance(value, str) else list(value)
    return [str(item).strip() for item in items if str(item or "").strip()]


def bare_address(value: Any) -> str:
    parsed = getaddresses([str(value or "")])
    return (parsed[0][1] if parsed else "").strip().lower()


def _attachment_json(item: Any) -> dict[str, str]:
    if isinstance(item, Attachment):
        return item.as_json()
    if isinstance(item, Mapping):
        content = item.get("content", "")
        if isinstance(content, bytes):
            return Attachment(item.get("filename", ""), content, item.get("content_type", "")).as_json()
        # Already base64, as the API takes it.
        return {
            "filename": single_line(item.get("filename"))[:MAX_FILENAME] or UNNAMED_ATTACHMENT,
            "content": str(content),
            "content_type": content_type_of(item.get("filename"), item.get("content_type")),
        }
    if isinstance(item, tuple | list) and len(item) in (2, 3):
        return Attachment(item[0], item[1], item[2] if len(item) == 3 else "").as_json()
    raise TypeError("Un adjunto es mailko.Attachment, un dict o una tupla (nombre, contenido, tipo).")


def build_email(
    *,
    from_: str,
    to: str | Iterable[str],
    subject: str | None = None,
    html: str | None = None,
    text: str | None = None,
    cc: str | Iterable[str] | None = None,
    bcc: str | Iterable[str] | None = None,
    reply_to: str | Iterable[str] | None = None,
    template: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    attachments: Iterable[Any] | None = None,
    tags: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    stream: str | None = None,
) -> dict[str, Any]:
    """The JSON of one email. Empty values are left out."""
    body: dict[str, Any] = {"from": str(from_ or "").strip(), "to": address_list(to)}
    for name, value in (("cc", cc), ("bcc", bcc), ("reply_to", reply_to)):
        addresses = address_list(value)
        if addresses:
            body[name] = addresses
    if subject is not None:
        body["subject"] = single_line(subject)
    if html:
        body["html"] = html
    if text:
        body["text"] = text
    if template:
        body["template"] = dict(template)
    if headers:
        body["headers"] = {str(name): single_line(value) for name, value in headers.items()}
    if attachments:
        body["attachments"] = [_attachment_json(item) for item in attachments]
    if tags:
        body["tags"] = {str(name): str(value) for name, value in tags.items()}
    if metadata:
        body["metadata"] = dict(metadata)
    if stream:
        body["stream"] = stream
    return body


def _decoded_size(content: str) -> int:
    """Bytes a base64 string decodes to, without decoding it."""
    content = content.strip()
    return len(content) * 3 // 4 - len(content) + len(content.rstrip("="))


def message_size(body: Mapping[str, Any]) -> int:
    """Bytes of the email as Mailko counts them (``sending._size``): subject,
    html and text, plus each file's bytes × 4/3 (its weight as base64)."""
    size = sum(len(str(body.get(name) or "").encode()) for name in ("subject", "html", "text"))
    for attachment in body.get("attachments") or ():
        size += _decoded_size(str(attachment.get("content") or "")) * 4 // 3
    return size


def check_email(body: Mapping[str, Any]) -> None:
    """Raise ``MessageRejectedError`` for what Mailko would refuse."""
    if not body.get("from"):
        raise MessageRejectedError("Falta el remitente («from»).", code="from_required")
    to = body.get("to") or []
    if not to:
        raise MessageRejectedError("El correo necesita al menos un destinatario en «to».", code="to_required")
    recipients = {bare_address(item) for item in [*to, *(body.get("cc") or []), *(body.get("bcc") or [])]}
    recipients.discard("")
    if len(recipients) > MAX_RECIPIENTS:
        raise MessageRejectedError(
            f"Un correo puede tener hasta {MAX_RECIPIENTS} destinatarios entre to, cc y bcc; este tiene "
            f"{len(recipients)}. Para más, envía uno por destinatario o usa /emails/batch.",
            code="too_many_recipients",
        )
    if body.get("template"):
        if body.get("html") or body.get("text"):
            raise MessageRejectedError(
                "Envía una plantilla o un contenido propio (html/text), no ambos.", code="template_and_content"
            )
    else:
        if not str(body.get("subject") or "").strip():
            raise MessageRejectedError("El asunto es obligatorio (salvo con una plantilla).", code="subject_required")
        if not (body.get("html") or body.get("text")):
            raise MessageRejectedError("El correo no tiene contenido: falta html, text o template.", code="no_content")
    if len(str(body.get("subject") or "")) > MAX_LINE:
        raise MessageRejectedError(f"El asunto pasa de {MAX_LINE} caracteres.", code="subject_too_long")
    attachments = body.get("attachments") or []
    if len(attachments) > MAX_ATTACHMENTS:
        raise MessageRejectedError(
            f"Un correo puede llevar hasta {MAX_ATTACHMENTS} adjuntos; este lleva {len(attachments)}.",
            code="too_many_attachments",
        )
    for name, value in (body.get("headers") or {}).items():
        if str(name).lower() not in ALLOWED_HEADERS:
            raise MessageRejectedError(
                f"Mailko no deja definir la cabecera «{name}». Permitidas: {', '.join(sorted(ALLOWED_HEADERS))}.",
                code="invalid_header",
            )
        if not value or len(str(value)) > MAX_LINE:
            raise MessageRejectedError(f"La cabecera «{name}» está vacía o es demasiado larga.", code="invalid_header")
    tags = body.get("tags") or {}
    if len(tags) > MAX_TAGS:
        raise MessageRejectedError(f"Máximo {MAX_TAGS} etiquetas por correo.", code="too_many_tags")
    for name, value in tags.items():
        if not _TAG_NAME.match(str(name)):
            raise MessageRejectedError(
                f"La etiqueta «{name}» solo puede tener letras, números, guion y guion bajo (hasta 64).",
                code="invalid_tag",
            )
        if len(str(value)) > MAX_TAG_VALUE:
            raise MessageRejectedError(
                f"El valor de la etiqueta «{name}» pasa de {MAX_TAG_VALUE} caracteres.", code="invalid_tag"
            )
    size = message_size(body)
    if size > MAX_MESSAGE_BYTES:
        raise MessageRejectedError(
            f"El correo pesa {size / 1_000_000:.1f} MB y el máximo es {MAX_MESSAGE_BYTES / 1_000_000:.0f} MB. "
            "Reduce los adjuntos.",
            code="message_too_large",
        )
