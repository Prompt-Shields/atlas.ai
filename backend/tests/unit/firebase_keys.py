"""A local stand-in for Google's securetoken keys: an RSA key, its self-signed
certificate, and tokens shaped like Firebase ID tokens signed with it."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from jose import jwt

from app.auth.firebase_token import ISSUER_PREFIX, FirebaseTokenVerifier

KID = "test-kid-1"
PROJECT = "promptly-guide-test"


def _make_key() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "securetoken.test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return private_pem, cert.public_bytes(serialization.Encoding.PEM).decode()


PRIVATE_PEM, CERT_PEM = _make_key()
OTHER_PRIVATE_PEM, _ = _make_key()


def token(
    *,
    project: str = PROJECT,
    subject: str = "firebase-uid-1",
    firebase_tenant: str | None = None,
    kid: str = KID,
    key: str = PRIVATE_PEM,
    overrides: dict[str, Any] | None = None,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER_PREFIX + project,
        "aud": project,
        "sub": subject,
        "iat": now - 10,
        "exp": now + 3600,
        "auth_time": now - 10,
        "email": "kari@example.com",
        "firebase": {"sign_in_provider": "saml.entra"},
    }
    if firebase_tenant is not None:
        claims["firebase"]["tenant"] = firebase_tenant
    claims.update(overrides or {})
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def verifier(certs: dict[str, str] | None = None) -> FirebaseTokenVerifier:
    served = {KID: CERT_PEM} if certs is None else certs

    async def fetch() -> tuple[dict[str, str], int]:
        return served, 3600

    return FirebaseTokenVerifier(fetch=fetch)
