"""Email service — console backend for dev, SMTP for production."""

from __future__ import annotations

from email.message import EmailMessage

import structlog
from aiosmtplib import send as smtp_send

from app.config import get_settings

logger = structlog.get_logger()


async def send_email(to: str, subject: str, body_html: str) -> None:
    """Send an email using configured backend."""
    settings = get_settings()

    if settings.email_backend == "console":
        logger.info(
            "email_sent_console",
            to=to,
            subject=subject,
            body_preview=body_html[:200],
        )
        return

    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body_html, subtype="html")

    use_tls = settings.smtp_port in (465, 587) and settings.is_production
    await smtp_send(
        msg,
        hostname=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_user or None,
        password=settings.smtp_password or None,
        start_tls=use_tls,
    )
    logger.info("email_sent", to=to, subject=subject)


async def send_invite_email(to: str, invite_token: str, tenant_name: str) -> None:
    """Send a tenant invitation email."""
    import html as html_mod
    import urllib.parse

    settings = get_settings()
    base_url = settings.backend_cors_origins.split(",")[0].strip()
    confirm_url = f"{base_url}/invite/confirm?token={urllib.parse.quote(invite_token, safe='')}"
    safe_tenant = html_mod.escape(tenant_name)
    safe_url = html_mod.escape(confirm_url)

    body = f"""
    <h2>You&#39;ve been invited to join {safe_tenant} on AI-GRC</h2>
    <p>Click the link below to accept the invitation and set up your account:</p>
    <p><a href="{safe_url}">{safe_url}</a></p>
    <p>This invitation expires in 72 hours.</p>
    <p>If you didn&#39;t expect this invitation, you can safely ignore this email.</p>
    """

    await send_email(to, f"Invitation to join {safe_tenant} on AI-GRC", body)
