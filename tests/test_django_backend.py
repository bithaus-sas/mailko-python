import json
from email.mime.text import MIMEText

import pytest
import responses
from django.core import mail
from django.core.mail import EmailMessage, EmailMultiAlternatives, get_connection
from django.test import override_settings

import mailko

from .conftest import API

ACCEPTED = {"id": "msg_1", "object": "email", "status": "queued", "test_mode": True}


def _sent():
    return json.loads(responses.calls[-1].request.body)


@responses.activate
def test_send_mail_goes_out_through_mailko():
    responses.post(f"{API}/emails", json=ACCEPTED, status=202)

    assert mail.send_mail("Hola", "Texto", None, ["ana@correo.com"], html_message="<p>Hola</p>") == 1

    assert _sent() == {
        "from": "Tienda <pedidos@tienda.co>",
        "to": ["ana@correo.com"],
        "subject": "Hola",
        "html": "<p>Hola</p>",
        "text": "Texto",
    }
    assert responses.calls[0].request.headers["Authorization"] == "Bearer mk_test_default"


@responses.activate
def test_carries_everything_django_has_and_the_mailko_attributes():
    responses.post(f"{API}/emails", json=ACCEPTED, status=202)
    message = EmailMultiAlternatives(
        "Factura",
        "Adjunta",
        "Tienda <pedidos@tienda.co>",
        ["ana@correo.com"],
        cc=["c@correo.com"],
        bcc=["b@correo.com"],
        headers={"Reply-To": "soporte@tienda.co", "X-SES-CONFIGURATION-SET": "x", "List-Id": "<l.tienda.co>"},
    )
    message.attach("factura.pdf", b"%PDF", "application/pdf")
    message.attach(MIMEText("hola", "plain"))
    message.mailko_tags = {"factura": "F-1"}
    message.mailko_metadata = {"tenant": 7}
    message.mailko_stream = "marketing"
    message.mailko_idempotency_key = "factura-F-1"

    assert message.send() == 1

    body = _sent()
    assert body["cc"] == ["c@correo.com"]
    assert body["bcc"] == ["b@correo.com"]
    assert body["reply_to"] == ["soporte@tienda.co"]
    assert body["headers"] == {"List-Id": "<l.tienda.co>"}
    assert [item["filename"] for item in body["attachments"]] == ["factura.pdf", "adjunto"]
    assert body["attachments"][0]["content"] == "JVBERg=="
    assert body["attachments"][1]["content_type"] == "text/plain"
    assert (body["tags"], body["metadata"], body["stream"]) == ({"factura": "F-1"}, {"tenant": 7}, "marketing")
    assert responses.calls[0].request.headers["Idempotency-Key"] == "factura-F-1"
    assert (message.mailko_id, message.mailko_status) == ("msg_1", "queued")


@responses.activate
def test_an_html_body_goes_as_html():
    responses.post(f"{API}/emails", json=ACCEPTED, status=202)
    message = EmailMessage("Hola", "<b>Hola</b>", None, ["ana@correo.com"])
    message.content_subtype = "html"
    message.send()
    assert _sent()["html"] == "<b>Hola</b>"
    assert "text" not in _sent()


@responses.activate
@override_settings(MAILKO_API_KEYS_BY_DOMAIN={"negocios.tienda.co": "mk_test_negocios"})
def test_another_projects_key_for_another_domain():
    responses.post(f"{API}/emails", json=ACCEPTED, status=202)
    mail.send_mail("Factura", "x", "Factura <facturas@negocios.tienda.co>", ["ana@correo.com"])
    mail.send_mail("Código", "x", "pedidos@tienda.co", ["ana@correo.com"])
    keys = [call.request.headers["Authorization"] for call in responses.calls]
    assert keys == ["Bearer mk_test_negocios", "Bearer mk_test_default"]


@responses.activate
def test_a_connection_with_its_own_key():
    responses.post(f"{API}/emails", json=ACCEPTED, status=202)
    connection = get_connection(api_key="mk_test_propia")
    EmailMessage("x", "x", None, ["ana@correo.com"], connection=connection).send()
    assert responses.calls[0].request.headers["Authorization"] == "Bearer mk_test_propia"


@pytest.mark.skipif(not hasattr(mail, "mailers"), reason="MAILERS arrived in Django 6.1")
@responses.activate
def test_a_mailer_of_mailers_with_its_options():
    responses.post(f"{API}/emails", json=ACCEPTED, status=202)
    mailers = {
        "default": {"BACKEND": "mailko.contrib.django.EmailBackend"},
        "negocios": {"BACKEND": "mailko.contrib.django.EmailBackend", "OPTIONS": {"api_key": "mk_test_mailer"}},
    }
    with override_settings(MAILERS=mailers):
        mail.send_mail("x", "x", None, ["ana@correo.com"], using="negocios")
    assert responses.calls[0].request.headers["Authorization"] == "Bearer mk_test_mailer"


@responses.activate
def test_suppressed_is_not_sent():
    responses.post(f"{API}/emails", json={**ACCEPTED, "status": "suppressed"}, status=202)
    with pytest.raises(mailko.SuppressedError):
        mail.send_mail("x", "x", None, ["rebote@correo.com"])
    assert mail.send_mail("x", "x", None, ["rebote@correo.com"], fail_silently=True) == 0


@responses.activate
def test_a_refusal_raises_unless_silent():
    responses.post(
        f"{API}/emails",
        json={"error": {"code": "domain_not_verified", "message": "El dominio no está verificado."}},
        status=403,
    )
    with pytest.raises(mailko.PermissionDeniedError):
        mail.send_mail("x", "x", None, ["ana@correo.com"])
    assert mail.send_mail("x", "x", None, ["ana@correo.com"], fail_silently=True) == 0


@responses.activate
def test_a_reused_key_with_another_email_is_never_read_as_sent():
    responses.post(
        f"{API}/emails", json={"error": {"code": "idempotency_key_reused", "message": "Ya se usó."}}, status=422
    )
    message = EmailMessage("Envío", "Tu pedido salió", None, ["ana@correo.com"])
    message.mailko_idempotency_key = "pedido-1"
    with pytest.raises(mailko.IdempotencyError):
        message.send()


@responses.activate
def test_a_suppressed_message_does_not_stop_the_rest():
    responses.post(f"{API}/emails", json={**ACCEPTED, "status": "suppressed"}, status=202)
    responses.post(f"{API}/emails", json=ACCEPTED, status=202)
    messages = [EmailMessage("x", "x", None, ["rebote@correo.com"]), EmailMessage("x", "x", None, ["b@correo.com"])]
    with pytest.raises(mailko.SuppressedError):
        get_connection().send_messages(messages)
    assert len(responses.calls) == 2
    assert messages[1].mailko_status == "queued"


@responses.activate
def test_an_html_body_keeps_its_plain_alternative_and_drops_others():
    responses.post(f"{API}/emails", json=ACCEPTED, status=202)
    message = EmailMultiAlternatives("Hola", "<b>Hola</b>", None, ["ana@correo.com"])
    message.content_subtype = "html"
    message.attach_alternative("Hola", "text/plain")
    message.attach_alternative("BEGIN:VCALENDAR", "text/calendar")
    message.send()
    assert (_sent()["html"], _sent()["text"]) == ("<b>Hola</b>", "Hola")


@responses.activate
def test_a_template_leaves_the_django_body_out():
    responses.post(f"{API}/emails", json=ACCEPTED, status=202)
    message = EmailMessage("", "cuerpo de relleno", None, ["ana@correo.com"])
    message.mailko_template = {"id": "bienvenida", "variables": {"nombre": "Ana"}}
    message.send()
    assert _sent()["template"] == {"id": "bienvenida", "variables": {"nombre": "Ana"}}
    assert "text" not in _sent()


def test_needs_a_subject_without_a_template():
    with pytest.raises(mailko.MessageRejectedError) as caught:
        EmailMessage("", "x", None, ["ana@correo.com"]).send()
    assert caught.value.code == "subject_required"


def test_refuses_before_calling():
    with responses.RequestsMock() as rsps:
        with pytest.raises(mailko.MessageRejectedError) as caught:
            EmailMessage("x", "x", None, [], bcc=["oculto@correo.com"]).send()
        assert caught.value.code == "to_required"
        with pytest.raises(mailko.MessageRejectedError):
            EmailMessage("x", "x", None, [f"u{n}@correo.com" for n in range(51)]).send()
        assert EmailMessage("x", "x", None, []).send() == 0
        assert not rsps.calls


@override_settings(MAILKO_API_KEY="")
def test_without_a_key_it_says_so():
    with pytest.raises(mailko.MessageRejectedError) as caught:
        mail.send_mail("x", "x", None, ["ana@correo.com"])
    assert caught.value.code == "no_key"
    assert mail.send_mail("x", "x", None, ["ana@correo.com"], fail_silently=True) == 0


@responses.activate
def test_one_failure_does_not_hide_the_others_when_silent():
    responses.post(f"{API}/emails", json={"error": {"code": "x", "message": "No."}}, status=400)
    responses.post(f"{API}/emails", json=ACCEPTED, status=202)
    messages = [EmailMessage("x", "x", None, ["a@correo.com"]), EmailMessage("x", "x", None, ["b@correo.com"])]
    assert get_connection(fail_silently=True).send_messages(messages) == 1
