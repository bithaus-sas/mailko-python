import json

import pytest
import requests
import responses

import mailko
from mailko.constants import API_VERSION

from .conftest import API

ACCEPTED = {"id": "msg_1", "object": "email", "status": "queued", "test_mode": True}


def _client(**kwargs):
    return mailko.Mailko("mk_test_abc", base_url="https://api.mailko.test", **kwargs)


def _error(code, message="No.", **extra):
    return {"error": {"type": "x", "code": code, "message": message, "request_id": "req_1", **extra}}


def test_refuses_a_key_that_is_not_mailkos():
    for key in ("", "  ", "sk_live_123", None):
        with pytest.raises(ValueError):
            mailko.Mailko(key)


def test_modes_and_base_url():
    assert not _client().livemode
    assert mailko.Mailko("mk_live_x").livemode
    assert mailko.Mailko("mkp_live_x").is_partner
    assert mailko.Mailko("mk_live_x", base_url="https://h.co/v1/").base_url == "https://h.co"
    assert mailko.Mailko("mk_live_x", base_url="http://localhost:8000").base_url == "http://localhost:8000"


@pytest.mark.parametrize("url", ["http://api.mailko.co", "ftp://api.mailko.co", "api.mailko.co", ""])
def test_the_key_never_travels_in_clear(url):
    if url == "":
        assert mailko.Mailko("mk_live_x", base_url=url).base_url == "https://api.mailko.co"
        return
    with pytest.raises(ValueError):
        mailko.Mailko("mk_live_x", base_url=url)


@responses.activate
def test_a_redirect_is_never_followed():
    responses.post(f"{API}/emails", status=301, headers={"Location": "https://otro.co/v1/emails"})
    responses.get("https://otro.co/v1/emails", json={"data": []})
    with pytest.raises(mailko.APIConnectionError) as caught:
        _client().emails.send(from_="a@tienda.co", to="b@correo.com", subject="x", text="x")
    assert caught.value.code == "redirected"
    assert len(responses.calls) == 1


@responses.activate
def test_a_bad_certificate_is_not_retried():
    responses.post(f"{API}/emails", body=requests.exceptions.SSLError("bad cert"))
    with pytest.raises(mailko.APIConnectionError) as caught:
        _client().emails.send(from_="a@tienda.co", to="b@correo.com", subject="x", text="x")
    assert caught.value.code == "tls_error"
    assert len(responses.calls) == 1


@responses.activate
def test_query_values_as_mailko_reads_them():
    import datetime

    responses.get(f"{API}/emails", json={"data": [], "pagination": {"has_more": False}})
    _client().emails.list(status=["bounced", "delivered"], created_after=datetime.date(2026, 11, 1), test=False)
    url = responses.calls[0].request.url
    assert "status=bounced%2Cdelivered" in url
    assert "created_after=2026-11-01" in url
    assert "test=false" in url


@responses.activate
def test_a_batch_error_says_what_went():
    error = _error("insufficient_balance", index=2, accepted=[{"id": "msg_1"}, {"id": "msg_2"}])
    responses.post(f"{API}/emails/batch", json=error, status=402)
    good = mailko.build_email(from_="a@tienda.co", to="b@correo.com", subject="x", text="x")
    with pytest.raises(mailko.PaymentRequiredError) as caught:
        _client().emails.batch([good] * 3)
    assert caught.value.extra == {"index": 2, "accepted": [{"id": "msg_1"}, {"id": "msg_2"}]}


@responses.activate
def test_send_posts_the_api_shape_with_its_headers():
    responses.post(f"{API}/emails", json=ACCEPTED, status=202)

    answer = _client().emails.send(
        from_="Tienda <pedidos@tienda.co>",
        to="ana@correo.com",
        subject="Hola",
        html="<p>Hola</p>",
        attachments=[mailko.Attachment("a.pdf", b"%PDF")],
        tags={"pedido": 1234},
        idempotency_key="pedido-1234",
    )

    assert answer == ACCEPTED
    call = responses.calls[0].request
    assert call.headers["Authorization"] == "Bearer mk_test_abc"
    assert call.headers["Idempotency-Key"] == "pedido-1234"
    assert call.headers["Mailko-Version"] == API_VERSION
    assert call.headers["User-Agent"].startswith("mailko-python/")
    body = json.loads(call.body)
    assert body == {
        "from": "Tienda <pedidos@tienda.co>",
        "to": ["ana@correo.com"],
        "subject": "Hola",
        "html": "<p>Hola</p>",
        "attachments": [{"filename": "a.pdf", "content": "JVBERg==", "content_type": "application/pdf"}],
        "tags": {"pedido": "1234"},
    }


@responses.activate
def test_every_write_gets_a_key_and_reads_none():
    responses.post(f"{API}/domains", json={"id": "d"}, status=201)
    responses.get(f"{API}/domains/d", json={"id": "d"})
    client = _client()
    client.domains.create("tienda.co")
    client.domains.retrieve("d")
    assert responses.calls[0].request.headers["Idempotency-Key"]
    assert "Idempotency-Key" not in responses.calls[1].request.headers


@responses.activate
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (500, "internal_error"),
        (503, "provider_unavailable"),
        (409, "idempotency_key_in_progress"),
        (429, "rate_limited"),
    ],
)
def test_retries_what_is_safe_with_the_same_key(status, code):
    responses.post(f"{API}/emails", json=_error(code), status=status)
    responses.post(f"{API}/emails", json=ACCEPTED, status=202)

    assert _client().emails.send(from_="a@tienda.co", to="b@correo.com", subject="x", text="x") == ACCEPTED
    keys = {call.request.headers["Idempotency-Key"] for call in responses.calls}
    assert len(responses.calls) == 2
    assert len(keys) == 1


@responses.activate
def test_retries_a_lost_connection_then_says_so():
    responses.post(f"{API}/emails", body=requests.exceptions.ConnectionError("down"))
    with pytest.raises(mailko.APIConnectionError):
        _client(max_retries=2).emails.send(from_="a@tienda.co", to="b@correo.com", subject="x", text="x")
    assert len(responses.calls) == 3


@responses.activate
def test_honours_retry_after_up_to_its_cap(no_sleep):
    responses.post(f"{API}/emails", json=_error("rate_limited"), status=429, headers={"Retry-After": "3"})
    responses.post(f"{API}/emails", json=ACCEPTED, status=202)
    _client().emails.send(from_="a@tienda.co", to="b@correo.com", subject="x", text="x")
    assert no_sleep == [3.0]

    responses.post(f"{API}/emails", json=_error("limit_exceeded"), status=429, headers={"Retry-After": "3600"})
    with pytest.raises(mailko.RateLimitError) as caught:
        _client().emails.send(from_="a@tienda.co", to="b@correo.com", subject="x", text="x")
    assert caught.value.retry_after == 3600


@responses.activate
def test_never_retries_the_months_quota_nor_another_4xx():
    responses.post(f"{API}/emails", json=_error("monthly_quota_exceeded"), status=429)
    with pytest.raises(mailko.RateLimitError):
        _client().emails.send(from_="a@tienda.co", to="b@correo.com", subject="x", text="x")
    assert len(responses.calls) == 1

    responses.post(f"{API}/domains", json=_error("domain_taken", "Ya existe."), status=400)
    with pytest.raises(mailko.BadRequestError):
        _client().domains.create("tienda.co")
    assert len(responses.calls) == 2


@responses.activate
@pytest.mark.parametrize(
    ("status", "code", "cls"),
    [
        (400, "validation_error", mailko.BadRequestError),
        (401, "api_key_invalid", mailko.AuthenticationError),
        (402, "insufficient_balance", mailko.PaymentRequiredError),
        (403, "domain_not_verified", mailko.PermissionDeniedError),
        (404, "not_found", mailko.NotFoundError),
        (409, "confirmation_required", mailko.ConflictError),
        (413, "message_too_large", mailko.BadRequestError),
        (422, "idempotency_key_reused", mailko.IdempotencyError),
        (500, "internal_error", mailko.ServerError),
    ],
)
def test_maps_each_refusal_to_its_class(status, code, cls):
    responses.get(f"{API}/me", json=_error(code, "Explicación.", fields={"to": ["Mal."]}), status=status)
    with pytest.raises(cls) as caught:
        _client(max_retries=0).me.retrieve()
    error = caught.value
    assert (error.status, error.code, error.request_id, error.message) == (status, code, "req_1", "Explicación.")
    assert error.fields == {"to": ["Mal."]}
    assert "req_1" in str(error)
    assert isinstance(error, mailko.MailkoError)


@responses.activate
def test_a_refusal_without_json_still_reads():
    responses.get(f"{API}/me", body="<html>bad gateway</html>", status=502, headers={"X-Request-Id": "req_9"})
    with pytest.raises(mailko.ServerError) as caught:
        _client(max_retries=0).me.retrieve()
    assert caught.value.request_id == "req_9"
    assert "502" in str(caught.value)


@responses.activate
def test_delete_answers_none():
    responses.delete(f"{API}/suppressions/s%2F1", status=204)
    assert _client().suppressions.delete("s/1") is None
    assert responses.calls[0].request.url.endswith("/suppressions/s%2F1")


@responses.activate
def test_list_all_walks_every_page():
    responses.get(f"{API}/emails", json={"data": [{"id": 1}, {"id": 2}], "pagination": {"has_more": True}})
    responses.get(f"{API}/emails", json={"data": [{"id": 3}], "pagination": {"has_more": False}})

    ids = [item["id"] for item in _client().emails.list_all(status="bounced", page_size=2)]

    assert ids == [1, 2, 3]
    assert "page=2" in responses.calls[1].request.url
    assert "status=bounced" in responses.calls[1].request.url


@responses.activate
def test_campaign_actions_carry_their_confirmation():
    responses.post(f"{API}/campaigns/c1/send", json={"status": "sending"}, status=202)
    _client().campaigns.send("c1", confirm=True)
    assert json.loads(responses.calls[0].request.body) == {"confirm": True}


@responses.activate
def test_batch_checks_every_email_before_calling():
    good = mailko.build_email(from_="a@tienda.co", to="b@correo.com", subject="x", text="x")
    with pytest.raises(mailko.MessageRejectedError):
        _client().emails.batch([good, {**good, "to": []}])
    with pytest.raises(mailko.MessageRejectedError):
        _client().emails.batch([good] * 101)
    assert not responses.calls

    responses.post(f"{API}/emails/batch", json={"data": [ACCEPTED]}, status=202)
    _client().emails.batch([good])
    assert json.loads(responses.calls[0].request.body) == {"emails": [good]}
