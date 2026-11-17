"""The URL Mailko posts its webhooks to.

::

    # urls.py
    path("mailko/", include("mailko.contrib.django.urls")),   # → /mailko/webhook/

    # settings.py
    MAILKO_WEBHOOK_SECRET = env("MAILKO_WEBHOOK_SECRET")         # whsec_…

Nothing is read before the signature checks out. An authentic event is
answered 200 even when nothing in this app cares about it — or Mailko would
post it again for hours — and an id already handled is not handled again
(Mailko delivers at least once). What to do with each event is yours:
connect to ``signals.email_event`` / ``signals.webhook_received``, or
subclass ``MailkoWebhookView`` and override ``handle_event``.
"""

from __future__ import annotations

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


@method_decorator(csrf_exempt, name="dispatch")
class MailkoWebhookView(View):
    http_method_names = ["post"]

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        secrets = conf.webhook_secrets()
        if not secrets:
            # Closed until configured, saying nothing about the route.
            return JsonResponse({"error": "not_found"}, status=404)
        try:
            event = construct_event(
                request.body,
                request.headers.get(SIGNATURE_HEADER),
                secrets,
                tolerance=int(conf.get("MAILKO_WEBHOOK_TOLERANCE")),
            )
        except SignatureVerificationError as exc:
            logger.warning("A Mailko webhook failed verification: %s", exc.code)
            return JsonResponse({"error": exc.code or "invalid_signature"}, status=400)

        dedup = int(conf.get("MAILKO_WEBHOOK_DEDUP_SECONDS") or 0)
        seen_key = f"{SEEN_PREFIX}{event['id']}"
        if dedup and cache.get(seen_key):
            return JsonResponse({"received": True, "duplicate": True})
        self.handle_event(event)
        # Only once handled: an event that failed half way is taken again.
        if dedup:
            cache.add(seen_key, 1, dedup)
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
