"""Sending email (sign-in codes, approvals) through an ordinary mail account.

Configured with ``MPACT_SMTP_*``: any account that allows SMTP sign-in works,
an institutional Google or Microsoft account included (see the README). The
server signs in to that account's own mail server and sends as it, so the
mail is from a real address people know and passes the receiving end's
checks -- which mail sent directly from a cloud server mostly does not.

Port 587 with STARTTLS is the default. (Hetzner, like most cloud providers,
blocks outgoing ports 25 and 465 on new servers; 587 is left open.)

With no server configured, and only then, mail can be written to a local file
instead (``MPACT_MAIL_OUTBOX``), for development and the tests.
"""

import json
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
from pathlib import Path

from . import config


class MailError(Exception):
    """The message could not be sent."""


_OUTBOX_LOCK = threading.Lock()


def configured():
    return bool(config.SMTP_HOST) or bool(config.MAIL_OUTBOX)


def sender():
    """``(display, address)`` the mail is sent as."""
    name, address = parseaddr(config.MAIL_FROM or config.SMTP_USER or '')
    return name or 'MPACT', address


def send(to, subject, body):
    """Send a plain-text message. Raises ``MailError``."""
    name, address = sender()
    if not config.SMTP_HOST:
        if config.MAIL_OUTBOX:
            _to_outbox(to, subject, body)
            return
        raise MailError('email is not set up on this server')
    if not address:
        raise MailError('MPACT_MAIL_FROM (or MPACT_SMTP_USER) must be an address')

    message = EmailMessage()
    message['From'] = formataddr((name, address))
    message['To'] = to
    message['Subject'] = subject
    message['Message-ID'] = make_msgid(domain=address.rsplit('@', 1)[-1])
    if config.MAIL_REPLY_TO:
        message['Reply-To'] = config.MAIL_REPLY_TO
    message.set_content(body)

    security = str(config.SMTP_SECURITY or 'starttls').lower()
    context = ssl.create_default_context()
    try:
        if security == 'ssl':
            client = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=20,
                                      context=context)
        else:
            client = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20)
        with client:
            if security == 'starttls':
                client.starttls(context=context)
            if config.SMTP_USER:
                client.login(config.SMTP_USER, config.SMTP_PASSWORD)
            client.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        # The operator needs the detail; the person signing in does not.
        print(f'mail to {to!r} failed: {type(exc).__name__}: {exc}')
        raise MailError('the email could not be sent right now; try again shortly') from exc


def _to_outbox(to, subject, body):
    path = Path(config.MAIL_OUTBOX)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _OUTBOX_LOCK, open(path, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps({'to': to, 'subject': subject, 'body': body,
                                 'sent': time.time()}) + '\n')
