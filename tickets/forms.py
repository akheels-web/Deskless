from django import forms

from .models import Comment, Ticket


class TicketUpdateForm(forms.ModelForm):
    """Agent-side: change status/priority/assignee."""

    class Meta:
        model = Ticket
        fields = ["status", "priority", "assignee"]


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ["body", "internal"]
        widgets = {"body": forms.Textarea(attrs={"rows": 4})}


class PublicTicketForm(forms.ModelForm):
    """Phase 3: what a customer fills in. Name/email for anonymous submitters."""

    name = forms.CharField(max_length=150)
    email = forms.EmailField()

    class Meta:
        model = Ticket
        fields = ["subject", "body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 6})}


class AgentTicketForm(forms.ModelForm):
    """Agent logs a ticket for a customer (phone/walk-in). Sets priority directly."""

    email = forms.EmailField(label="Requester email")

    class Meta:
        model = Ticket
        fields = ["subject", "body", "priority"]
        widgets = {"body": forms.Textarea(attrs={"rows": 6})}
