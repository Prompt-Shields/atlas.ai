"""The invite email: what goes in the subject and what goes in the body."""

from __future__ import annotations

from email.message import EmailMessage
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import email_service

pytestmark = [pytest.mark.unit]


def _settings(backend: str = "console") -> SimpleNamespace:
    return SimpleNamespace(
        email_backend=backend,
        backend_cors_origins="https://app.example.test, https://other.example.test",
        smtp_from="noreply@example.test",
        smtp_host="localhost",
        smtp_port=1025,
        smtp_user="",
        smtp_password="",
        is_production=False,
    )


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """Record what send_invite_email hands to send_email, without sending."""
    calls: list[tuple[str, str, str]] = []

    async def fake_send_email(to: str, subject: str, body_html: str) -> None:
        calls.append((to, subject, body_html))

    monkeypatch.setattr(email_service, "get_settings", lambda: _settings())
    monkeypatch.setattr(email_service, "send_email", fake_send_email)
    return calls


async def test_body_escapes_tenant_name(captured: list) -> None:
    await email_service.send_invite_email("a@example.test", "tok", "<b>Acme</b> & Co")

    _, _, body = captured[0]
    assert "&lt;b&gt;Acme&lt;/b&gt; &amp; Co" in body
    assert "<b>Acme</b>" not in body


async def test_body_escapes_url(captured: list, monkeypatch: pytest.MonkeyPatch) -> None:
    # The base URL comes from configuration, so an "&" or a quote in it must
    # not close the href early or start an entity.
    settings = _settings()
    settings.backend_cors_origins = 'https://app.example.test/?a=1&b="2"'
    monkeypatch.setattr(email_service, "get_settings", lambda: settings)

    await email_service.send_invite_email("a@example.test", "tok", "Acme")

    _, _, body = captured[0]
    escaped = "https://app.example.test/?a=1&amp;b=&quot;2&quot;/invite/confirm?token=tok"
    assert f'<a href="{escaped}">{escaped}</a>' in body


async def test_token_is_url_quoted(captured: list) -> None:
    await email_service.send_invite_email("a@example.test", 'a/b&c=d"e f', "Acme")

    _, _, body = captured[0]
    assert "token=a%2Fb%26c%3Dd%22e%20f" in body
    assert 'a/b&c=d"e f' not in body


async def test_subject_has_raw_tenant_name(captured: list) -> None:
    await email_service.send_invite_email("a@example.test", "tok", "Acme & Co <UK>")

    _, subject, _ = captured[0]
    assert subject == "Invitation to join Acme & Co <UK> on AI-GRC"


@pytest.mark.parametrize("brk", ["\n", "\r\n", "\r", "\x0b", "\x85", " "])
async def test_line_break_in_name_cannot_inject_header(captured: list, brk: str) -> None:
    name = f"Acme{brk}Bcc: victim@example.test"
    await email_service.send_invite_email("a@example.test", "tok", name)

    _, subject, _ = captured[0]
    assert subject == "Invitation to join Acme Bcc: victim@example.test on AI-GRC"
    assert len(subject.splitlines()) == 1

    # The subject is one that EmailMessage accepts, and it adds no header.
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["To"] = "a@example.test"
    assert msg.get_all("Bcc") is None
    assert msg["Subject"] == subject


async def test_console_backend_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    smtp = AsyncMock()
    monkeypatch.setattr(email_service, "get_settings", lambda: _settings("console"))
    monkeypatch.setattr(email_service, "smtp_send", smtp)

    await email_service.send_invite_email("a@example.test", "tok", "Acme & Co")

    smtp.assert_not_awaited()


async def test_smtp_backend_sends_raw_subject_once(monkeypatch: pytest.MonkeyPatch) -> None:
    smtp = AsyncMock()
    monkeypatch.setattr(email_service, "get_settings", lambda: _settings("smtp"))
    monkeypatch.setattr(email_service, "smtp_send", smtp)

    await email_service.send_invite_email("a@example.test", "tok", "Acme & Co\nBcc: x@y")

    smtp.assert_awaited_once()
    msg = smtp.await_args.args[0]
    assert msg["Subject"] == "Invitation to join Acme & Co Bcc: x@y on AI-GRC"
    assert msg.get_all("Bcc") is None
    assert "Acme &amp; Co" in msg.get_content()
