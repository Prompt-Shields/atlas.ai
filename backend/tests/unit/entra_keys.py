"""A local stand-in for Microsoft's v2.0 signing keys: an RSA key, its public JWK,
and id-tokens signed with it."""

from __future__ import annotations

from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk, jwt

from app.services.sso_login import EntraKeySet

KID = "entra-test-kid-1"


def _make_key() -> tuple[str, dict[str, Any]]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    public_jwk = jwk.construct(public_pem, "RS256").to_dict()
    # Shaped like an entry of Microsoft's key document.
    public_jwk.pop("alg", None)
    public_jwk["use"] = "sig"
    return private_pem, public_jwk


PRIVATE_PEM, PUBLIC_JWK = _make_key()
OTHER_PRIVATE_PEM, OTHER_PUBLIC_JWK = _make_key()


def jwk_for(kid: str, public_jwk: dict[str, Any] = PUBLIC_JWK) -> dict[str, Any]:
    return {**public_jwk, "kid": kid}


def id_token(claims: dict[str, Any], *, kid: str = KID, key: str = PRIVATE_PEM) -> str:
    """An id-token signed the way Microsoft signs: RS256, with a key id."""
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def key_set(keys: dict[str, dict[str, Any]] | None = None) -> EntraKeySet:
    served = {KID: jwk_for(KID)} if keys is None else keys

    async def fetch() -> tuple[dict[str, dict[str, Any]], int]:
        return served, 3600

    return EntraKeySet(fetch=fetch)
