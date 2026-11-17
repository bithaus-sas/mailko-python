"""Every resource of ``/v1``, one class each, hung from the client.

Methods answer Mailko's JSON as a ``dict``. Lists answer one page
(``{"data": [...], "pagination": {...}}``); ``list_all()`` walks every page.
Writes accept ``idempotency_key=`` (a uuid4 when left out) and the rest of the
body as keyword arguments, named as the API names them.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from mailko.constants import MAX_BATCH
from mailko.errors import MessageRejectedError
from mailko.message import build_email, check_email

if TYPE_CHECKING:  # pragma: no cover
    from mailko.client import Mailko


def _id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Falta el id.")
    return quote(text, safe="")


class Resource:
    path = ""

    def __init__(self, client: Mailko) -> None:
        self._client = client

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._client.request(method, path, **kwargs)


class _List(Resource):
    def list(self, **params: Any) -> dict[str, Any]:
        """One page: ``page``, ``page_size`` (1 to 100) and the resource's filters."""
        return self._request("GET", self.path, params=params)

    def list_all(self, **params: Any) -> Iterator[dict[str, Any]]:
        """Every item, page after page."""
        return self._client.paginate(self.path, params)


class _Retrieve(Resource):
    def retrieve(self, id: str) -> dict[str, Any]:  # noqa: A002
        return self._request("GET", f"{self.path}/{_id(id)}")


class _Create(Resource):
    def create(self, *, idempotency_key: str | None = None, **fields: Any) -> dict[str, Any]:
        return self._request("POST", self.path, body=fields, idempotency_key=idempotency_key)


class _Update(Resource):
    def update(self, id: str, *, idempotency_key: str | None = None, **fields: Any) -> dict[str, Any]:  # noqa: A002
        return self._request("PATCH", f"{self.path}/{_id(id)}", body=fields, idempotency_key=idempotency_key)


class _Delete(Resource):
    def delete(self, id: str, *, idempotency_key: str | None = None) -> None:  # noqa: A002
        self._request("DELETE", f"{self.path}/{_id(id)}", idempotency_key=idempotency_key)


# -- the key and the project ----------------------------------------------------


class Me(Resource):
    def retrieve(self) -> dict[str, Any]:
        """The key itself: its project, scopes and mode."""
        return self._request("GET", "/me")


class Usage(Resource):
    def retrieve(self) -> dict[str, Any]:
        return self._request("GET", "/usage")


# -- sending ----------------------------------------------------------------------


class Emails(_List, _Retrieve):
    """``send`` · ``batch`` · ``list`` (``status``, ``recipient``, ``tag``,
    ``created_after``, ``created_before``) · ``retrieve``."""

    path = "/emails"

    def send(
        self,
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
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Send one email. Answers ``{"id": "msg_…", "status": "queued", ...}``
        (``status`` is ``suppressed`` when every recipient is on the project's
        suppression list: nothing left).

        ``from_`` is the sender (``"Tienda <pedidos@tienda.co>"``, of a
        verified domain). Attachments: ``mailko.Attachment(name, bytes)``,
        ``(name, bytes, type)`` or the API's dict with base64 content."""
        body = build_email(
            from_=from_,
            to=to,
            subject=subject,
            html=html,
            text=text,
            cc=cc,
            bcc=bcc,
            reply_to=reply_to,
            template=template,
            headers=headers,
            attachments=attachments,
            tags=tags,
            metadata=metadata,
            stream=stream,
        )
        check_email(body)
        return self.send_raw(body, idempotency_key=idempotency_key)

    def send_raw(self, body: Mapping[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Send a body already in the API's shape (``"from"``, base64 files)."""
        return self._request("POST", self.path, body=body, idempotency_key=idempotency_key)

    def batch(self, emails: Iterable[Mapping[str, Any]], *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Up to 100 emails in one call: ``emails`` are bodies in the API's
        shape (``build_email(...)`` makes one). Answers ``{"data": [...]}``.

        Mailko checks every email before taking any, then takes them in order:
        a limit or the balance running out half way stops the rest, and the
        error says which went in ``exc.extra["accepted"]`` (and where it
        stopped in ``exc.extra["index"]``).
        Retrying with the same ``idempotency_key`` is safe: what already went
        is not sent again."""
        items = [dict(item) for item in emails]
        if not items:
            raise MessageRejectedError("El lote está vacío.", code="empty_batch")
        if len(items) > MAX_BATCH:
            raise MessageRejectedError(
                f"Un lote puede tener hasta {MAX_BATCH} correos; este tiene {len(items)}.", code="too_many_emails"
            )
        for item in items:
            check_email(item)
        return self._request("POST", f"{self.path}/batch", body={"emails": items}, idempotency_key=idempotency_key)


class Domains(_List, _Retrieve, _Delete):
    path = "/domains"

    def create(self, name: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Add a sending domain; the answer lists the DNS records to publish."""
        return self._request("POST", self.path, body={"name": name}, idempotency_key=idempotency_key)

    def verify(self, id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:  # noqa: A002
        """Check its DNS now instead of waiting for Mailko's next round."""
        return self._request("POST", f"{self.path}/{_id(id)}/verify", idempotency_key=idempotency_key)


class Suppressions(_List, _Retrieve, _Delete):
    """Addresses the project never sends to (filters: ``reason``, ``email``)."""

    path = "/suppressions"

    def create(self, email: str, *, detail: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        body = {"email": email}
        if detail:
            body["detail"] = detail
        return self._request("POST", self.path, body=body, idempotency_key=idempotency_key)


class Templates(_List, _Retrieve):
    path = "/templates"


# -- webhooks ---------------------------------------------------------------------


class WebhookEndpoints(_List, _Retrieve, _Create, _Update, _Delete):
    """``create(url=..., topics=[...])`` answers the signing secret
    (``whsec_…``) once: keep it."""

    path = "/webhook-endpoints"

    def rotate_secret(self, id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:  # noqa: A002
        return self._request("POST", f"{self.path}/{_id(id)}/rotate-secret", idempotency_key=idempotency_key)

    def test(self, id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:  # noqa: A002
        """Send a test event to the endpoint."""
        return self._request("POST", f"{self.path}/{_id(id)}/test", idempotency_key=idempotency_key)

    def deliveries(self, id: str, **params: Any) -> dict[str, Any]:  # noqa: A002
        return self._request("GET", f"{self.path}/{_id(id)}/deliveries", params=params)

    def redeliver(self, id: str, delivery_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:  # noqa: A002
        return self._request(
            "POST",
            f"{self.path}/{_id(id)}/deliveries/{_id(delivery_id)}/redeliver",
            idempotency_key=idempotency_key,
        )


class WebhookEvents(Resource):
    def list(self) -> dict[str, Any]:
        """The event types a webhook can subscribe to."""
        return self._request("GET", "/webhook-events")


# -- marketing (the key needs the «marketing» scope) --------------------------------


class Contacts(_List, _Retrieve, _Create, _Update, _Delete):
    path = "/contacts"


class Lists(_List, _Retrieve, _Create):
    path = "/lists"

    def add_contacts(
        self,
        id: str,  # noqa: A002
        contacts: Iterable[str],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"{self.path}/{_id(id)}/contacts",
            body={"contacts": list(contacts)},
            idempotency_key=idempotency_key,
        )

    def remove_contacts(
        self,
        id: str,  # noqa: A002
        contacts: Iterable[str],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "DELETE",
            f"{self.path}/{_id(id)}/contacts",
            body={"contacts": list(contacts)},
            idempotency_key=idempotency_key,
        )


class Segments(_List, _Retrieve):
    path = "/segments"


class Campaigns(_List, _Retrieve, _Create, _Update):
    path = "/campaigns"

    def _action(self, id: str, action: str, body: Mapping[str, Any] | None, key: str | None) -> dict[str, Any]:  # noqa: A002
        return self._request("POST", f"{self.path}/{_id(id)}/{action}", body=body, idempotency_key=key)

    def review(self, id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:  # noqa: A002
        return self._action(id, "review", None, idempotency_key)

    def schedule(self, id: str, *, send_at: str, confirm: bool, idempotency_key: str | None = None) -> dict[str, Any]:  # noqa: A002
        """Schedule it for ``send_at`` (ISO 8601). ``confirm=True`` is required:
        it says a person meant it."""
        return self._action(id, "schedule", {"send_at": send_at, "confirm": confirm}, idempotency_key)

    def send(self, id: str, *, confirm: bool, idempotency_key: str | None = None) -> dict[str, Any]:  # noqa: A002
        """Send it now. ``confirm=True`` is required."""
        return self._action(id, "send", {"confirm": confirm}, idempotency_key)

    def pause(self, id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:  # noqa: A002
        return self._action(id, "pause", None, idempotency_key)

    def cancel(self, id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:  # noqa: A002
        return self._action(id, "cancel", None, idempotency_key)

    def stats(self, id: str) -> dict[str, Any]:  # noqa: A002
        return self._request("GET", f"{self.path}/{_id(id)}/stats")


class Imports(_List, _Retrieve, _Create):
    path = "/imports"

    def confirm(self, id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:  # noqa: A002
        return self._request("POST", f"{self.path}/{_id(id)}/confirm", idempotency_key=idempotency_key)


# -- «Conectar Mailko» (a partner key, mkp_…) ---------------------------------------


class Partner(Resource):
    def create_link_request(
        self,
        *,
        name: str,
        external_id: str,
        return_url: str,
        scopes: Iterable[str],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """A one-use link where a Mailko customer consents to connect a project.
        Mailko does not deduplicate this one by ``Idempotency-Key``: a retry
        may create a second link (the first simply goes unused)."""
        body = {"name": name, "external_id": external_id, "return_url": return_url, "scopes": list(scopes)}
        return self._request("POST", "/partner/link-requests", body=body, idempotency_key=idempotency_key)

    def exchange(self, code: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Trade the code of the return for the connection and its key (once)."""
        return self._request(
            "POST", "/partner/link-requests/exchange", body={"code": code}, idempotency_key=idempotency_key
        )

    def retrieve_connection(self, id: str) -> dict[str, Any]:  # noqa: A002
        return self._request("GET", f"/partner/connections/{_id(id)}")

    def revoke_connection(self, id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:  # noqa: A002
        return self._request("POST", f"/partner/connections/{_id(id)}/revoke", idempotency_key=idempotency_key)
