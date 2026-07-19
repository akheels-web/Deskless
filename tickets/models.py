import secrets

from django.conf import settings
from django.db import models


def _new_token():
    return secrets.token_urlsafe(16)


class Category(models.Model):
    """A bucket/department a ticket belongs to (Billing, IT, Sales…)."""

    name = models.CharField(max_length=80, unique=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Group(models.Model):
    """C1: a team of agents (Networking, Support…). Tickets can be assigned to a group."""

    name = models.CharField(max_length=80, unique=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="ticket_groups",
        limit_choices_to={"is_staff": True})

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Tag(models.Model):
    """Freeform label for cross-cutting themes (vip, bug, refund…)."""

    name = models.CharField(max_length=40, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Ticket(models.Model):
    # ponytail: agent = User.is_staff, customer = regular user. No role field needed.
    STATUS = [
        ("open", "Open"),
        ("in_progress", "In Progress"),
        ("pending", "Pending"),
        ("escalated", "Escalate"),
        ("closed", "Closed"),
    ]
    OPEN_STATES = ("open", "in_progress", "pending", "escalated")
    # statuses that require a note (and, for escalate, a person) on transition
    NOTE_REQUIRED = ("pending", "escalated", "closed")
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

    # G1: routing & labels
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets")
    group = models.ForeignKey(  # C1: owning team
        "Group", on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets")
    tags = models.ManyToManyField(Tag, blank=True, related_name="tickets")

    # R3: SLA — resolution deadline computed from priority at creation.
    due_at = models.DateTimeField(null=True, blank=True)
    sla_breached = models.BooleanField(default=False)
    breach_notified = models.BooleanField(default=False)  # avoid re-emailing
    paused_at = models.DateTimeField(null=True, blank=True)  # T2: set while pending

    # H3: CSAT — set when the requester rates a closed ticket.
    csat_rating = models.PositiveSmallIntegerField(null=True, blank=True)  # 1..5
    csat_token = models.CharField(max_length=32, blank=True, db_index=True)
    closed_at = models.DateTimeField(null=True, blank=True)  # U3: reopen-window anchor

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
    def is_paused(self):
        return self.paused_at is not None

    @property
    def is_overdue(self):
        from django.utils import timezone
        # T2: a paused (pending) ticket never counts as overdue
        return bool(self.due_at and self.is_open and not self.is_paused
                    and timezone.now() > self.due_at)

    def pause_sla(self):
        """T2: stop the SLA clock (entering pending)."""
        from django.utils import timezone
        if not self.paused_at:
            self.paused_at = timezone.now()

    def resume_sla(self):
        """T2: push due_at forward by the paused duration, then clear the pause."""
        from django.utils import timezone
        if self.paused_at and self.due_at:
            self.due_at = self.due_at + (timezone.now() - self.paused_at)
        self.paused_at = None

    def close_with_resolution(self, resolution, cascade=True):
        """Close this ticket, and (F2) cascade-close every linked ticket still open."""
        from django.utils import timezone
        now = timezone.now()
        self.status = "closed"
        self.resolution = resolution
        self.csat_token = self.csat_token or _new_token()  # H3: enable rating + status link
        self.closed_at = now
        self.save()
        if cascade:
            for t in self.related.filter(status__in=self.OPEN_STATES):
                t.status = "closed"
                # note which ticket drove the cascade — the "resolution node"
                t.resolution = resolution + f"\n\n(Closed with linked ticket #{self.pk}.)"
                t.csat_token = t.csat_token or _new_token()
                t.closed_at = now
                t.save()

    def can_reopen(self):
        """U3: customer may reopen a closed ticket within the org's reopen window."""
        from django.utils import timezone
        from datetime import timedelta
        if self.status != "closed" or not self.closed_at:
            return False
        days = OrgSettings.load().reopen_days
        return days > 0 and timezone.now() <= self.closed_at + timedelta(days=days)


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


class Attachment(models.Model):
    """G2: a file on a ticket (screenshot, log, doc)."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="attachments/%Y/%m/")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def filename(self):
        import os
        return os.path.basename(self.file.name)

    def __str__(self):
        return self.filename


class CannedReply(models.Model):
    """G3: a saved answer agents can drop into a reply."""

    title = models.CharField(max_length=100)
    body = models.TextField()

    class Meta:
        ordering = ["title"]
        verbose_name_plural = "canned replies"

    def __str__(self):
        return self.title


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

    # H1: business hours. SLA clock only ticks inside this window (local time).
    business_hours_enabled = models.BooleanField(
        default=False, help_text="Count SLA only during business hours")
    business_start = models.PositiveSmallIntegerField(default=9, help_text="Work day start hour (0-23)")
    business_end = models.PositiveSmallIntegerField(default=17, help_text="Work day end hour (0-23)")
    business_days = models.CharField(
        default="0,1,2,3,4", max_length=13,
        help_text="Working weekdays, comma-separated (Mon=0 … Sun=6)")
    holidays = models.TextField(
        blank=True, help_text="Holiday dates (YYYY-MM-DD), one per line — excluded from SLA")

    # U3: how many days a customer may reopen a closed ticket (0 = never)
    reopen_days = models.PositiveIntegerField(
        default=3, help_text="Days a customer can reopen a closed ticket (0 disables)")

    # Email: outbound SMTP (DB overrides env). Blank host → env/console fallback.
    site_url = models.URLField(
        blank=True, help_text="Public base URL, e.g. https://deskless.example.com — used in email links")
    email_host = models.CharField(max_length=200, blank=True, help_text="SMTP server")
    email_port = models.PositiveIntegerField(default=587)
    email_user = models.CharField(max_length=200, blank=True)
    email_password = models.CharField(max_length=200, blank=True)
    email_use_tls = models.BooleanField(default=True)
    email_from = models.EmailField(blank=True, help_text="From address (defaults to SMTP user)")

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

    def sla_hours(self, priority, group=None, category=None):
        """U1: most-specific SLAPolicy wins (group+category > group > category),
        else the global per-priority default.
        """
        policies = SLAPolicy.objects.filter(priority=priority)
        best, best_score = None, -1
        for p in policies:
            if p.group_id and group is not None and p.group_id != getattr(group, "id", group):
                continue
            if p.category_id and category is not None and p.category_id != getattr(category, "id", category):
                continue
            if p.group_id and group is None:
                continue
            if p.category_id and category is None:
                continue
            score = (2 if p.group_id else 0) + (1 if p.category_id else 0)
            if score > best_score:
                best, best_score = p, score
        if best:
            return best.hours
        return getattr(self, f"sla_{priority}", self.sla_normal)

    def _work_days(self):
        return {int(d) for d in self.business_days.split(",") if d.strip().isdigit()}

    def _holiday_set(self):
        """Parse holidays textarea → set of 'YYYY-MM-DD' strings."""
        return {line.strip() for line in self.holidays.splitlines() if line.strip()}

    def sla_deadline(self, start, priority, group=None, category=None):
        """Deadline = start + SLA hours, counting only business time if enabled."""
        hours = self.sla_hours(priority, group=group, category=category)
        if not self.business_hours_enabled:
            from datetime import timedelta
            return start + timedelta(hours=hours)
        return self._add_business_hours(start, hours)

    def _add_business_hours(self, start, hours):
        """Advance `start` by `hours` of business time (whole-hour steps).
        ponytail: hour-granularity walk — simple and correct for SLA buckets
        (4/8/24/72h). Go minute-granular only if sub-hour SLAs appear.
        """
        from datetime import timedelta
        work_days = self._work_days() or {0, 1, 2, 3, 4}
        holidays = self._holiday_set()
        remaining = hours
        t = start
        guard = 0
        while remaining > 0:
            guard += 1
            if guard > 100000:  # safety: never spin forever on misconfig
                break
            is_workday = t.weekday() in work_days and t.strftime("%Y-%m-%d") not in holidays
            if is_workday and self.business_start <= t.hour < self.business_end:
                remaining -= 1
            t += timedelta(hours=1)
        return t


class SLAPolicy(models.Model):
    """U1: per-group / per-category resolution target, overriding the global default.
    Leave group/category blank to match any. Most-specific match wins (see sla_hours).
    """

    group = models.ForeignKey(
        "Group", on_delete=models.CASCADE, null=True, blank=True, related_name="sla_policies")
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, null=True, blank=True, related_name="sla_policies")
    priority = models.CharField(max_length=10, choices=Ticket.PRIORITY)
    hours = models.PositiveIntegerField(help_text="Hours to resolve")

    class Meta:
        verbose_name_plural = "SLA policies"
        ordering = ["group__name", "category__name", "priority"]

    def __str__(self):
        scope = self.group.name if self.group_id else (self.category.name if self.category_id else "Any")
        return f"{scope} · {self.get_priority_display()} → {self.hours}h"


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


class Trigger(models.Model):
    """C2: on new-ticket, if the keyword appears in subject/body, apply routing.
    ponytail: simple keyword-contains rule. Add regex/AND-OR conditions only if
    a client outgrows plain keywords.
    """

    name = models.CharField(max_length=100)
    keyword = models.CharField(max_length=100, help_text="Case-insensitive text to match in subject or body")
    set_group = models.ForeignKey(
        Group, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Assign matching tickets to this group")
    set_priority = models.CharField(
        max_length=10, blank=True, choices=Ticket.PRIORITY,
        help_text="Optionally force a priority")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} (~{self.keyword})"

    def matches(self, ticket):
        text = f"{ticket.subject}\n{ticket.body}".lower()
        return self.keyword.lower() in text


class TicketEvent(models.Model):
    """H2: audit trail — one row per change to a ticket."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.ticket_id}: {self.description}"


class Article(models.Model):
    """H5: knowledge-base article. Published ones are public."""

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)
    body = models.TextField(help_text="Plain text or basic HTML")
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="articles")
    published = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title
