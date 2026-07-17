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
    if not recipients:
        return
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
