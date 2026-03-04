"""Email sending tool — send_email.

Authentication: SMTP via Gmail App Password (recommended) or any SMTP server.

Required env vars (at least one set):
  GMAIL_ADDRESS       — sender Gmail address (e.g. you@gmail.com)
  GMAIL_APP_PASSWORD  — 16-char Gmail App Password (not your main password)

Optional env vars (override defaults):
  SMTP_HOST     — SMTP server hostname   (default: smtp.gmail.com)
  SMTP_PORT     — SMTP port              (default: 587, STARTTLS)
  SMTP_FROM     — Override sender address (default: GMAIL_ADDRESS)

How to create a Gmail App Password:
  1. Enable 2FA on your Google account.
  2. Go to https://myaccount.google.com/apppasswords
  3. Create a new App Password → copy the 16-char code.
  4. Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD as Colab secrets / env vars.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List

from ouroboros.tools.registry import ToolContext, ToolEntry

log = logging.getLogger(__name__)


def _send_email(
    ctx: ToolContext,
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    html: bool = False,
) -> str:
    """Send an email via SMTP (Gmail App Password by default).

    Args:
        to:      Recipient address (or comma-separated list).
        subject: Email subject line.
        body:    Email body — plain text (or HTML if html=True).
        cc:      Optional CC address(es), comma-separated.
        html:    If True, send body as text/html; otherwise text/plain.

    Returns:
        "OK: email sent to <to>" on success, or an error string.
    """
    gmail_addr = os.environ.get("GMAIL_ADDRESS", "").strip()
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()

    if not gmail_addr:
        return (
            "⚠️ GMAIL_ADDRESS env var not set. "
            "Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD to enable email sending."
        )
    if not app_password:
        return (
            "⚠️ GMAIL_APP_PASSWORD env var not set. "
            "Create a Gmail App Password at https://myaccount.google.com/apppasswords "
            "and set GMAIL_APP_PASSWORD."
        )

    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip()
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_from = os.environ.get("SMTP_FROM", gmail_addr).strip()

    # Build message
    msg = MIMEMultipart("alternative")
    msg["From"] = smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc

    content_type = "html" if html else "plain"
    msg.attach(MIMEText(body, content_type, "utf-8"))

    # Collect all recipients
    recipients: List[str] = [addr.strip() for addr in to.split(",") if addr.strip()]
    if cc:
        recipients += [addr.strip() for addr in cc.split(",") if addr.strip()]

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(gmail_addr, app_password)
            server.sendmail(smtp_from, recipients, msg.as_string())
        log.info("Email sent to %s via %s:%d", to, smtp_host, smtp_port)
        return f"OK: email sent to {to}"
    except smtplib.SMTPAuthenticationError:
        return (
            "⚠️ SMTP authentication failed. "
            "Check that GMAIL_ADDRESS and GMAIL_APP_PASSWORD are correct, "
            "and that you're using an App Password (not your main Gmail password). "
            "See: https://myaccount.google.com/apppasswords"
        )
    except smtplib.SMTPRecipientsRefused as e:
        return f"⚠️ Recipient refused: {e}"
    except smtplib.SMTPException as e:
        log.warning("SMTP error sending email", exc_info=True)
        return f"⚠️ SMTP error: {e}"
    except OSError as e:
        log.warning("Network error sending email", exc_info=True)
        return f"⚠️ Network error: {e}"


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            "send_email",
            {
                "name": "send_email",
                "description": (
                    "Send an email on behalf of the owner. "
                    "Uses Gmail SMTP with App Password (GMAIL_ADDRESS + GMAIL_APP_PASSWORD env vars). "
                    "Supports plain text and HTML. "
                    "Requires GMAIL_ADDRESS and GMAIL_APP_PASSWORD to be set."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {
                            "type": "string",
                            "description": "Recipient email address (or comma-separated list).",
                        },
                        "subject": {
                            "type": "string",
                            "description": "Email subject line.",
                        },
                        "body": {
                            "type": "string",
                            "description": "Email body — plain text (or HTML if html=True).",
                        },
                        "cc": {
                            "type": "string",
                            "description": "Optional CC address(es), comma-separated.",
                        },
                        "html": {
                            "type": "boolean",
                            "description": "If true, send body as HTML. Default: false (plain text).",
                        },
                    },
                    "required": ["to", "subject", "body"],
                },
            },
            _send_email,
            timeout_sec=45,
        ),
    ]
