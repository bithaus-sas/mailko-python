import base64

import pytest

import mailko
from mailko.message import build_email, check_email, message_size


def _email(**overrides):
    return build_email(**{"from_": "a@tienda.co", "to": "b@correo.com", "subject": "x", "text": "x", **overrides})


def test_builds_the_api_shape_leaving_out_what_is_empty():
    body = build_email(
        from_=" Tienda <a@tienda.co> ",
        to="Ana <ana@correo.com>",
        cc=["c@correo.com", ""],
        subject="Línea\r\nrota",
        text="Hola",
        headers={"List-Unsubscribe": "<mailto:x>\n"},
    )
    assert body == {
        "from": "Tienda <a@tienda.co>",
        "to": ["Ana <ana@correo.com>"],
        "cc": ["c@correo.com"],
        "subject": "Línea rota",
        "text": "Hola",
        "headers": {"List-Unsubscribe": "<mailto:x>"},
    }


@pytest.mark.parametrize(
    "item",
    [
        mailko.Attachment("f.csv", b"a,b"),
        ("f.csv", b"a,b"),
        ("f.csv", "a,b", "text/csv; charset=utf-8"),
        {"filename": "f.csv", "content": b"a,b"},
        {"filename": "f.csv", "content": base64.b64encode(b"a,b").decode(), "content_type": "text/csv"},
    ],
)
def test_attachments_in_every_shape(item):
    [attachment] = _email(attachments=[item])["attachments"]
    assert attachment == {"filename": "f.csv", "content": "YSxi", "content_type": "text/csv"}


def test_an_odd_type_becomes_octet_stream():
    [attachment] = _email(attachments=[("x", b"1", "no es un tipo")])["attachments"]
    assert attachment["content_type"] == "application/octet-stream"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"to": []}, "to_required"),
        ({"from_": ""}, "from_required"),
        ({"text": None}, "no_content"),
        ({"to": [f"u{n}@correo.com" for n in range(51)]}, "too_many_recipients"),
        ({"attachments": [("a", b"1")] * 21}, "too_many_attachments"),
        ({"headers": {"X-SES-CONFIGURATION-SET": "x"}}, "invalid_header"),
        ({"tags": {"con espacio": "x"}}, "invalid_tag"),
        ({"tags": {f"t{n}": "x" for n in range(11)}}, "too_many_tags"),
        ({"attachments": [("big", b"0" * 7_600_000)]}, "message_too_large"),
    ],
)
def test_refuses_what_mailko_would(overrides, code):
    with pytest.raises(mailko.MessageRejectedError) as caught:
        check_email(_email(**overrides))
    assert caught.value.code == code


def test_repeated_recipients_count_once():
    check_email(_email(to=[f"u{n}@correo.com" for n in range(50)], cc=["U0@correo.com"]))


def test_measures_size_as_mailko_does():
    content = b"x" * 1000
    body = _email(subject="Hola", attachments=[("a", content)])
    # Mailko: subject + text + len(raw bytes) * 4 // 3.
    assert message_size(body) == len("Hola") + len("x") + len(content) * 4 // 3
    assert message_size(_email(attachments=[("a", b"ab")])) == len("x") + len("x") + 2 * 4 // 3


def test_a_template_is_content_enough():
    check_email(_email(text=None, template={"id": "bienvenida"}))


def test_a_template_or_content_never_both_and_a_subject_without_template():
    with pytest.raises(mailko.MessageRejectedError) as caught:
        check_email(_email(template={"id": "bienvenida"}))
    assert caught.value.code == "template_and_content"
    with pytest.raises(mailko.MessageRejectedError) as caught:
        check_email(_email(subject=" "))
    assert caught.value.code == "subject_required"
    check_email(_email(subject=None, text=None, template={"id": "bienvenida"}))
