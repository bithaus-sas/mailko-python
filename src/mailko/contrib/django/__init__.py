"""Mailko for Django (``pip install "mailko[django]"``).

* ``EmailBackend`` — ``EMAIL_BACKEND = "mailko.contrib.django.EmailBackend"``.
* ``views.webhook_view`` / ``urls`` — the URL for Mailko's webhooks.
* ``signals.email_event`` / ``signals.webhook_received`` — what to connect to.

No models, no migrations: ``INSTALLED_APPS`` needs nothing.
"""

from mailko.contrib.django.backend import EmailBackend

__all__ = ["EmailBackend"]
