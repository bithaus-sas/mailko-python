import json
import time

import pytest

import mailko
from mailko.webhooks import parse_signature, sign

SECRET = "whsec_uno"
BODY = json.dumps({"id": "evt_1", "type": "email.delivered", "data": {"object": {"id": "msg_1"}}}).encode()


def test_accepts_what_mailko_signed():
    assert mailko.construct_event(BODY, sign(BODY, SECRET), SECRET)["id"] == "evt_1"


def test_matches_mailkos_own_recipe():
    # The documented recipe: HMAC-SHA256(secret, "<t>.<body>") in hex.
    import hashlib
    import hmac

    header = sign(BODY, SECRET, timestamp=1791817339)
    expected = hmac.new(SECRET.encode(), b"1791817339." + BODY, hashlib.sha256).hexdigest()
    assert header == f"t=1791817339,v1={expected}"


def test_any_of_several_secrets_will_do():
    header = sign(BODY, "whsec_dos")
    mailko.verify_signature(BODY, header, [SECRET, "whsec_dos"])
    assert not mailko.is_valid_signature(BODY, header, SECRET)


@pytest.mark.parametrize(
    ("header", "code"),
    [
        (None, "bad_header"),
        ("t=abc,v1=00", "bad_header"),
        ("t=123", "bad_header"),
        (sign(BODY, "whsec_otro"), "mismatch"),
    ],
)
def test_refuses_a_forged_or_broken_header(header, code):
    with pytest.raises(mailko.SignatureVerificationError) as caught:
        mailko.verify_signature(BODY, header, SECRET)
    assert caught.value.code == code


def test_refuses_an_old_one_and_a_changed_body():
    old = sign(BODY, SECRET, timestamp=int(time.time()) - 301)
    assert not mailko.is_valid_signature(BODY, old, SECRET)
    assert mailko.is_valid_signature(BODY, old, SECRET, tolerance=0)
    assert not mailko.is_valid_signature(BODY + b" ", sign(BODY, SECRET), SECRET)


def test_needs_a_secret():
    with pytest.raises(mailko.SignatureVerificationError):
        mailko.verify_signature(BODY, sign(BODY, SECRET), ["", "  "])


def test_an_authentic_body_that_is_no_event_is_refused():
    for body in (b"no json", b"[]", b'{"id": "x"}'):
        with pytest.raises(mailko.SignatureVerificationError):
            mailko.construct_event(body, sign(body, SECRET), SECRET)


def test_parses_several_v1():
    assert parse_signature("t=5, v1=aa ,v1=bb") == (5, ["aa", "bb"])


def test_a_non_ascii_signature_is_refused_not_crashed():
    header = f"t={int(time.time())},v1=café"
    assert not mailko.is_valid_signature(b"{}", header, SECRET)
