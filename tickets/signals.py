"""Auto-triage new tickets: set priority from content.

Runs on EVERY ticket creation (web form, email, API) because they all hit
Ticket's post_save — one place, not three.

ponytail: keyword heuristic by default. Set OPENAI_API_KEY (or wire your own
LLM in classify_llm) only when keywords measurably miss. The knob is the
URGENT_WORDS list — tune per client, that's the calibration the model can't see.
"""
import json
import os
import urllib.request

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Ticket, Webhook


def fire_webhooks(event, ticket):
    """F6: POST ticket JSON to every active webhook for this event.
    ponytail: synchronous best-effort. Swap to a task queue only if a slow
    endpoint starts blocking saves — then move this into celery/rq.
    """
    payload = json.dumps({
        "event": event,
        "ticket": {
            "id": ticket.pk,
            "subject": ticket.subject,
            "status": ticket.status,
            "priority": ticket.priority,
            "resolution": ticket.resolution,
            "reporter": ticket.reporter.email or ticket.reporter.username,
        },
    }).encode()
    for hook in Webhook.objects.filter(event=event, active=True):
        req = urllib.request.Request(
            hook.url, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass  # never let a dead endpoint break ticket flow

URGENT_WORDS = ("down", "outage", "urgent", "asap", "broken", "cannot access",
                "security", "breach", "data loss", "production")
HIGH_WORDS = ("error", "failed", "not working", "crash", "slow", "blocked")


def classify_keywords(text):
    t = text.lower()
    if any(w in t for w in URGENT_WORDS):
        return "urgent"
    if any(w in t for w in HIGH_WORDS):
        return "high"
    return "normal"


def classify_llm(text):
    """Optional: LLM classification. Returns a valid priority or None."""
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return None
    try:  # ponytail: lazy import — no dep required unless you actually use this
        from openai import OpenAI
        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content":
                       "Classify this support ticket priority as exactly one of "
                       "low/normal/high/urgent. Reply with only the word.\n\n" + text[:2000]}],
            max_tokens=1, temperature=0,
        )
        word = resp.choices[0].message.content.strip().lower()
        return word if word in dict(Ticket.PRIORITY) else None
    except Exception:
        return None  # never let triage block ticket creation


@receiver(post_save, sender=Ticket)
def on_ticket_created(sender, instance, created, **kwargs):
    if not created:
        return

    # AI triage: only override the default priority, respect explicit choices.
    if instance.priority == "normal":
        text = f"{instance.subject}\n{instance.body}"
        priority = classify_llm(text) or classify_keywords(text)
        if priority != instance.priority:
            instance.priority = priority

    # R3: set the SLA resolution deadline from the (possibly triaged) priority.
    from django.utils import timezone
    from datetime import timedelta
    from .models import OrgSettings
    org = OrgSettings.load()
    due = instance.created_at + timedelta(hours=org.sla_hours(instance.priority))

    Ticket.objects.filter(pk=instance.pk).update(priority=instance.priority, due_at=due)
    instance.due_at = due

    fire_webhooks("ticket.created", instance)
    from .notifications import notify_new_ticket
    notify_new_ticket(instance)


# R4: users who sign in via SSO become agents (staff) automatically.
try:
    from allauth.account.signals import user_signed_up

    @receiver(user_signed_up)
    def sso_user_is_agent(request, user, **kwargs):
        # ponytail: any SSO sign-up = agent. Tighten to an email-domain allowlist
        # here if you need to restrict who can self-provision.
        if not user.is_staff:
            user.is_staff = True
            user.save(update_fields=["is_staff"])
except Exception:
    pass  # allauth not installed / migrations not run yet
