"""The URL Mailko posts its webhooks to.

::

    # urls.py
    path("mailko/", include("mailko.contrib.django.urls")),   # → /mailko/webhook/

    # settings.py
    MAILKO_WEBHOOK_SECRET = env("MAILKO_WEBHOOK_SECRET")         # whsec_…

Nothing is read before the signature checks out, and a body over
``MAILKO_WEBHOOK_MAX_BYTES`` is refused (413) before that. Answers:

* 200 — handled, or already handled before (``"duplicate": true``). An
  authentic event is answered 200 even when nothing in this app cares about
  it, or Mailko would post it again for hours.
* 400 — the signature is missing, wrong or older than the tolerance.
* 404 — ``MAILKO_WEBHOOK_SECRET`` is not set: the URL stays closed.
* 409 — the same event is being handled right now by another delivery;
  Mailko retries it later.
* 500 — a receiver raised; the event is not remembered and Mailko retries it. What to do with each event is yours:
connect to ``signals.email_event`` / ``signals.webhook_received``, or
subclass ``MailkoWebhookView`` and override ``handle_event``.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from mailko.constants import SIGNATURE_HEADER
from mailko.contrib.django import conf, signals
from mailko.errors import SignatureVerificationError
from mailko.webhooks import construct_event

logger = logging.getLogger("mailko")

SEEN_PREFIX = "mailko:webhook-event:"
PROCESSING = "processing"
DONE = "done"
#: How long a delivery holds its event while handling it. A process killed
#: half way leaves the reservation behind for this long, then Mailko's next
#: retry (1, 5, 30 minutes…) is handled.
PROCESSING_SECONDS = 5 * 60


@method_decorator(csrf_exempt, name="dispatch")
class MailkoWebhookView(View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        secrets = conf.webhook_secrets()
        if not secrets:
            # Closed until configured, saying nothing about the route.
            return JsonResponse({"error": "not_found"}, status=404)
        limit = int(conf.get("MAILKO_WEBHOOK_MAX_BYTES"))
        try:
            declared = int(request.headers.get("Content-Length") or 0)
        except ValueError:
            declared = 0
        # Refused before reading what nobody signed yet; without a declared
        # length (a chunked body) no more than one byte past the limit is read.
        if declared > limit:
            return JsonResponse({"error": "payload_too_large"}, status=413)
        body = request.read(limit + 1)
        if len(body) > limit:
            return JsonResponse({"error": "payload_too_large"}, status=413)
        try:
            event = construct_event(
                body,
                request.headers.get(SIGNATURE_HEADER),
                secrets,
                tolerance=int(conf.get("MAILKO_WEBHOOK_TOLERANCE")),
            )
        except SignatureVerificationError as exc:
            logger.warning("A Mailko webhook failed verification: %s", exc.code)
            return JsonResponse({"error": exc.code or "invalid_signature"}, status=400)

        dedup = int(conf.get("MAILKO_WEBHOOK_DEDUP_SECONDS") or 0)
        if not dedup:
            self.handle_event(event)
            return JsonResponse({"received": True})

        seen_key = f"{SEEN_PREFIX}{event['id']}"
        # Reserved before handling (``add`` is atomic in a shared cache): a
        # second delivery of the same event while the first runs (for up to
        # PROCESSING_SECONDS) is told to come back later.
        if not cache.add(seen_key, PROCESSING, PROCESSING_SECONDS):
            # Anything but a running reservation means handled (0.1.0 wrote 1).
            if cache.get(seen_key) != PROCESSING:
                return JsonResponse({"received": True, "duplicate": True})
            return JsonResponse({"error": "in_progress"}, status=409)
        try:
            self.handle_event(event)
        except BaseException:
            # Not remembered: Mailko's retry is handled. A cache that fails
            # here too must not hide the receiver's own error.
            with contextlib.suppress(Exception):
                cache.delete(seen_key)
            raise
        try:
            cache.set(seen_key, DONE, dedup)
        except Exception:
            # Handled already: answering 500 would make Mailko send it again.
            logger.warning("Could not remember Mailko event %s as handled", event["id"], exc_info=True)
        return JsonResponse({"received": True})

    def handle_event(self, event: dict[str, Any]) -> None:
        """Send the signals. Override to handle the event yourself."""
        signals.webhook_received.send(sender=self.__class__, event=event)
        event_type = str(event.get("type") or "")
        if event_type.startswith("email."):
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            email = data.get("object") if isinstance(data.get("object"), dict) else {}
            signals.email_event.send(sender=self.__class__, event=event, type=event_type, email=email)


webhook_view = MailkoWebhookView.as_view()
