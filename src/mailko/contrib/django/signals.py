"""Signals sent by the webhook view, once per authentic, new event.

``webhook_received(sender, event)`` — every event (``event["type"]``,
``event["data"]["object"]``…).

``email_event(sender, event, type, email)`` — the ``email.*`` ones, with the
email as Mailko's API answers it: ``email["tags"]`` carries back whatever the
sender put (``message.mailko_tags``), ``email["event"]`` what happened
(``recipient``, ``bounce_type``, ``occurred_at``…).

A receiver that raises makes the view answer 500: Mailko delivers the event
again later, and it is not remembered as seen.
"""

from django.dispatch import Signal

webhook_received = Signal()
email_event = Signal()
