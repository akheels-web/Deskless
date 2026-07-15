"""Auto-triage new tickets: set priority from content.

Runs on EVERY ticket creation (web form, email, API) because they all hit
Ticket's post_save — one place, not three.

ponytail: keyword heuristic by default. Set OPENAI_API_KEY (or wire your own
LLM in classify_llm) only when keywords measurably miss. The knob is the
URGENT_WORDS list — tune per client, that's the calibration the model can't see.
"""
import os

from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Ticket

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
def triage(sender, instance, created, **kwargs):
    if not created or instance.priority != "normal":
        return  # respect an explicitly-set priority; only triage the default
    text = f"{instance.subject}\n{instance.body}"
    priority = classify_llm(text) or classify_keywords(text)
    if priority != instance.priority:
        # update_fields avoids re-triggering full save logic elsewhere
        Ticket.objects.filter(pk=instance.pk).update(priority=priority)
        instance.priority = priority
