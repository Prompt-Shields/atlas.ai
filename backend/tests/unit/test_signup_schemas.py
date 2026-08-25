import pytest
from pydantic import ValidationError

from app.schemas.signup import SignupRequest


def test_valid_signup():
    r = SignupRequest(
        email="a@b.com", password="Str0ng!Passw0rd", tenant_name="Acme", tenant_slug="acme-co"
    )
    assert r.tenant_slug == "acme-co"


@pytest.mark.parametrize("bad", ["", "ab", "UPPER", "has space", "x" * 101])
def test_invalid_slug(bad):
    with pytest.raises(ValidationError):
        SignupRequest(
            email="a@b.com", password="Str0ng!Passw0rd", tenant_name="Acme", tenant_slug=bad
        )


def test_short_password():
    with pytest.raises(ValidationError):
        SignupRequest(email="a@b.com", password="short", tenant_name="Acme", tenant_slug="acme")


def test_bad_email():
    with pytest.raises(ValidationError):
        SignupRequest(
            email="notanemail", password="Str0ng!Passw0rd", tenant_name="Acme", tenant_slug="acme"
        )
