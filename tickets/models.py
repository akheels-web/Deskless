from django.conf import settings
from django.db import models


class Ticket(models.Model):
    # ponytail: agent = User.is_staff, customer = regular user. No role field needed.
    STATUS = [
        ("open", "Open"),
        ("in_progress", "In Progress"),
        ("pending", "Pending"),
        ("escalated", "Escalated"),
        ("closed", "Closed"),
    ]
    OPEN_STATES = ("open", "in_progress", "pending", "escalated")
    PRIORITY = [
        ("low", "Low"),
        ("normal", "Normal"),
        ("high", "High"),
        ("urgent", "Urgent"),
    ]

    subject = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=STATUS, default="open")
    priority = models.CharField(max_length=10, choices=PRIORITY, default="normal")
    resolution = models.TextField(blank=True, help_text="How this ticket was resolved")

    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reported_tickets",
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="assigned_tickets",
        null=True,
        blank=True,
        limit_choices_to={"is_staff": True},
    )
    # F2: link similar tickets. symmetrical = link is mutual (A linked to B ⇒ B linked to A).
    related = models.ManyToManyField("self", blank=True)

    # R3: SLA — resolution deadline computed from priority at creation.
    due_at = models.DateTimeField(null=True, blank=True)
    sla_breached = models.BooleanField(default=False)
    breach_notified = models.BooleanField(default=False)  # avoid re-emailing

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"#{self.pk} {self.subject}"

    @property
    def is_open(self):
        return self.status in self.OPEN_STATES

    @property
    def is_overdue(self):
        from django.utils import timezone
        return bool(self.due_at and self.is_open and timezone.now() > self.due_at)

    def close_with_resolution(self, resolution, cascade=True):
        """Close this ticket, and (F2) cascade-close every linked ticket still open."""
        self.status = "closed"
        self.resolution = resolution
        self.save()
        if cascade:
            for t in self.related.filter(status__in=self.OPEN_STATES):
                t.status = "closed"
                # note which ticket drove the cascade — the "resolution node"
                t.resolution = resolution + f"\n\n(Closed with linked ticket #{self.pk}.)"
                t.save()


class Comment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    body = models.TextField()
    internal = models.BooleanField(default=False, help_text="Visible to agents only")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment on #{self.ticket_id} by {self.author}"


class OrgSettings(models.Model):
    """F3/F6: single-row org branding + integration config. Editable in-app."""

    name = models.CharField(max_length=100, default="Deskless")
    logo = models.ImageField(upload_to="branding/", blank=True, null=True)
    color = models.CharField(max_length=7, default="#1f2937", help_text="Header/rail color")
    accent = models.CharField(max_length=7, default="#2563eb", help_text="Buttons & links")

    # R3: SLA resolution targets in hours, per priority.
    sla_urgent = models.PositiveIntegerField(default=4, help_text="Hours to resolve urgent")
    sla_high = models.PositiveIntegerField(default=8)
    sla_normal = models.PositiveIntegerField(default=24)
    sla_low = models.PositiveIntegerField(default=72)

    class Meta:
        verbose_name = "Organization settings"
        verbose_name_plural = "Organization settings"

    def __str__(self):
        return self.name

    @classmethod
    def load(cls):
        # ponytail: single-row singleton, pk=1 always. Simpler than a settings table.
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def sla_hours(self, priority):
        return getattr(self, f"sla_{priority}", self.sla_normal)


class Webhook(models.Model):
    """F5: fire a JSON POST to an external URL on ticket events. Enables integrations."""

    EVENTS = [
        ("ticket.created", "Ticket created"),
        ("ticket.closed", "Ticket closed"),
    ]
    url = models.URLField()
    event = models.CharField(max_length=20, choices=EVENTS)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.event} → {self.url}"
