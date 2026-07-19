"""R3: agent-facing email notifications (new ticket, assignment, SLA breach).

ponytail: all fail_silently — a dead SMTP must never break ticket flow.
Recipients are staff emails; customer-facing mail stays in views.notify_reporter.
"""
from django.contrib.auth import get_user_model
from django.core.mail import send_mail

User = get_user_model()


def _staff_emails():
    return list(User.objects.filter(is_staff=True, is_active=True)
                .exclude(email="").values_list("email", flat=True))


def _send(subject, body, recipients):
    """Send via the org's DB-configured SMTP if set, else Django's env/console backend.
    ponytail: opens a short-lived connection per send — fine at helpdesk volume.
    Batch through one connection only if you start blasting thousands.
    """
    if not recipients:
        return
    from .models import OrgSettings
    org = OrgSettings.load()
    if org.email_host:
        from django.core.mail import EmailMessage, get_connection
        conn = get_connection(
            backend="django.core.mail.backends.smtp.EmailBackend",
            host=org.email_host, port=org.email_port,
            username=org.email_user, password=org.email_password,
            use_tls=org.email_use_tls, fail_silently=True,
        )
        from_email = org.email_from or org.email_user or None
        EmailMessage(subject, body, from_email, recipients, connection=conn).send(fail_silently=True)
    else:
        send_mail(subject, body, None, recipients, fail_silently=True)


def notify_new_ticket(ticket):
    """Tell staff a new ticket landed (only if unassigned — assigned ones notify the assignee)."""
    if ticket.assignee_id:
        return
    _send(
        f"[New · DSK-{ticket.pk:04d}] {ticket.subject}",
        f"A new {ticket.get_priority_display()} ticket is unassigned.\n\n{ticket.body}",
        _staff_emails(),
    )


def notify_assignment(ticket):
    """Tell the assignee a ticket is now theirs."""
    if not (ticket.assignee_id and ticket.assignee.email):
        return
    _send(
        f"[Assigned · DSK-{ticket.pk:04d}] {ticket.subject}",
        f"You've been assigned this {ticket.get_priority_display()} ticket.\n\n{ticket.body}",
        [ticket.assignee.email],
    )


def notify_breach(ticket):
    """Alert on SLA breach — assignee if set, else all staff."""
    if ticket.assignee_id and ticket.assignee.email:
        recipients = [ticket.assignee.email]
    else:
        recipients = _staff_emails()
    _send(
        f"[SLA BREACH · DSK-{ticket.pk:04d}] {ticket.subject}",
        f"This {ticket.get_priority_display()} ticket has breached its "
        f"resolution deadline ({ticket.due_at:%b %d, %H:%M}) and is still "
        f"{ticket.get_status_display()}.",
        recipients,
    )


def send_csat_request(ticket):
    """H3: email the requester a rate link + their status page."""
    if not (ticket.reporter.email and ticket.csat_token):
        return
    from django.urls import reverse
    rate = _absolute(reverse("rate_ticket", args=[ticket.csat_token]))
    status = _absolute(reverse("ticket_status", args=[ticket.csat_token]))
    _send(
        f"[Resolved · DSK-{ticket.pk:04d}] How did we do?",
        f"Your request \"{ticket.subject}\" has been resolved.\n\n"
        f"Rate our support (1-5): {rate}\n\n"
        f"View or reopen this ticket: {status}",
        [ticket.reporter.email],
    )


def _absolute(path):
    """Absolute URL from the org's configured site_url, else the Sites domain."""
    from .models import OrgSettings
    base = OrgSettings.load().site_url.rstrip("/")
    if base:
        return f"{base}{path}"
    try:
        from django.contrib.sites.models import Site
        return f"https://{Site.objects.get_current().domain}{path}"
    except Exception:
        return path


def notify_mention(ticket, comment, user):
    """B4: tell an agent they were @mentioned in a ticket note."""
    if not user.email:
        return
    _send(
        f"[Mention · DSK-{ticket.pk:04d}] {ticket.subject}",
        f"{comment.author.username} mentioned you in an internal note:\n\n{comment.body}",
        [user.email],
    )


def send_track_link(email, link):
    """P4: email a customer a signed link to view their tickets."""
    _send(
        "Your support tickets",
        f"Here's a secure link to view your support tickets:\n\n{link}\n\n"
        f"The link works for 7 days. If you didn't request it, you can ignore this email.",
        [email],
    )
