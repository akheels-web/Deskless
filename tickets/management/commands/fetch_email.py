"""Poll an IMAP inbox and turn unread messages into tickets/comments.

Run manually or via cron/Task Scheduler:
    python manage.py fetch_email

ponytail: no push/IDLE, no threading library — a polling cron job is enough
until a client needs sub-minute email response. Then reach for imapclient IDLE.
"""
import email
import imaplib
import re
from email.header import decode_header

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from tickets.models import Comment, Ticket

User = get_user_model()

# Matches "[Ticket #12]" in a reply subject → append as comment instead of new ticket.
TICKET_RE = re.compile(r"\[Ticket #(\d+)\]")


def decode(value):
    if not value:
        return ""
    parts = decode_header(value)
    return "".join(
        p.decode(enc or "utf-8", "replace") if isinstance(p, bytes) else p
        for p, enc in parts
    )


def body_text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition")):
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", "replace")
        return ""
    payload = msg.get_payload(decode=True)
    return payload.decode(msg.get_content_charset() or "utf-8", "replace") if payload else ""


class Command(BaseCommand):
    help = "Fetch unread emails and create tickets or comments."

    def handle(self, *args, **opts):
        if not settings.IMAP_HOST:
            self.stderr.write("IMAP_HOST not configured in settings; skipping.")
            return

        M = imaplib.IMAP4_SSL(settings.IMAP_HOST)
        M.login(settings.IMAP_USER, settings.IMAP_PASSWORD)
        M.select("INBOX")
        _, data = M.search(None, "UNSEEN")
        ids = data[0].split()
        self.stdout.write(f"{len(ids)} new message(s)")

        for num in ids:
            _, msg_data = M.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            subject = decode(msg["Subject"]).strip() or "(no subject)"
            from_addr = email.utils.parseaddr(msg.get("From"))[1].lower()
            body = body_text(msg).strip()

            reporter, _ = User.objects.get_or_create(
                username=from_addr,
                defaults={"email": from_addr},
            )

            m = TICKET_RE.search(subject)
            if m and Ticket.objects.filter(pk=m.group(1)).exists():
                ticket = Ticket.objects.get(pk=m.group(1))
                Comment.objects.create(ticket=ticket, author=reporter, body=body)
                if ticket.status in ("resolved", "closed"):
                    ticket.status = "open"  # customer replied → reopen
                    ticket.save()
                self.stdout.write(f"  → comment on #{ticket.pk}")
            else:
                ticket = Ticket.objects.create(
                    subject=subject, body=body, reporter=reporter,
                )
                self.stdout.write(f"  → new ticket #{ticket.pk}")

            M.store(num, "+FLAGS", "\\Seen")

        M.close()
        M.logout()
