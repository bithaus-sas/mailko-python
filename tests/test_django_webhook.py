import json

import pytest
from django.test import Client, override_settings

from mailko.contrib.django import signals
from mailko.webhooks import sign

SECRET = "whsec_tienda"
URL = "/mailko/webhook/"
EVENT = {
    "id": "evt_1",
    "type": "email.bounced",
    "data": {"object": {"id": "msg_1", "tags": {"pedido": "1234"}, "event": {"bounce_type": "permanent"}}},
}


def _post(event=EVENT, secret=SECRET, client=None, header=None):
    body = json.dumps(event).encode()
    client = client or Client()
    return client.post(
        URL,
        data=body,
        content_type="application/json",
        headers={"Mailko-Signature": header if header is not None else sign(body, secret)},
    )


@pytest.fixture
def received():
    seen = []

    def on_email(sender, event, type, email, **kwargs):  # noqa: A002
        seen.append(("email", type, email))

    def on_any(sender, event, **kwargs):
        seen.append(("any", event["type"], None))

    signals.email_event.connect(on_email, weak=False)
    signals.webhook_received.connect(on_any, weak=False)
    yield seen
    signals.email_event.disconnect(on_email)
    signals.webhook_received.disconnect(on_any)


def test_closed_until_configured(received):
    assert _post().status_code == 404
    assert not received


@override_settings(MAILKO_WEBHOOK_SECRET=SECRET)
def test_an_authentic_event_reaches_the_signals_once(received):
    response = _post()
    assert response.status_code == 200
    assert received == [("any", "email.bounced", None), ("email", "email.bounced", EVENT["data"]["object"])]

    again = _post()
    assert again.status_code == 200
    assert again.json()["duplicate"] is True
    assert len(received) == 2


@override_settings(MAILKO_WEBHOOK_SECRET=SECRET)
def test_refuses_a_forged_signature_without_reading_it(received):
    assert _post(secret="whsec_otro").status_code == 400
    assert _post(header="").status_code == 400
    assert not received


@override_settings(MAILKO_WEBHOOK_SECRET=f"whsec_otro, {SECRET}")
def test_several_projects_post_to_one_url(received):
    assert _post().status_code == 200


@override_settings(MAILKO_WEBHOOK_SECRET=SECRET)
def test_other_events_reach_only_the_general_signal(received):
    _post({"id": "evt_2", "type": "domain.verified", "data": {"object": {}}})
    assert received == [("any", "domain.verified", None)]


@override_settings(MAILKO_WEBHOOK_SECRET=SECRET)
def test_a_failing_receiver_lets_mailko_retry():
    def broken(sender, **kwargs):
        raise RuntimeError("database down")

    signals.email_event.connect(broken, weak=False)
    try:
        client = Client(raise_request_exception=False)
        assert _post(client=client).status_code == 500
    finally:
        signals.email_event.disconnect(broken)
    # Not remembered as seen: the retry is handled.
    assert _post().json() == {"received": True}


@override_settings(MAILKO_WEBHOOK_SECRET=SECRET)
def test_only_post():
    assert Client().get(URL).status_code == 405


@override_settings(MAILKO_WEBHOOK_SECRET=SECRET, MAILKO_WEBHOOK_DEDUP_SECONDS=0)
def test_dedup_can_be_turned_off(received):
    _post()
    _post()
    assert len(received) == 4
