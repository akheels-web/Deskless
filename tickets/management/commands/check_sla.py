"""R3: flag SLA-breached tickets and email once per breach.

Run on a schedule (cron / Task Scheduler), e.g. every 15 minutes:
    python manage.py check_sla
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from tickets.models import Ticket
from tickets.notifications import notify_breach


class Command(BaseCommand):
    help = "Mark overdue open tickets as SLA-breached and notify."

    def handle(self, *args, **opts):
        now = timezone.now()
        overdue = Ticket.objects.filter(
            due_at__lt=now,
            status__in=Ticket.OPEN_STATES,
            breach_notified=False,
            paused_at__isnull=True,  # T2: paused (pending) tickets don't breach
        )
        n = 0
        for ticket in overdue:
            ticket.sla_breached = True
            ticket.breach_notified = True
            ticket.save(update_fields=["sla_breached", "breach_notified"])
            notify_breach(ticket)
            n += 1
        self.stdout.write(f"{n} ticket(s) newly breached and notified.")
