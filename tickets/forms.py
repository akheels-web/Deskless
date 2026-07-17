from django import forms
from django.contrib.auth import get_user_model

from .models import Comment, OrgSettings, Ticket

User = get_user_model()


class TicketUpdateForm(forms.ModelForm):
    """Agent-side: change status/priority/assignee/category/tags."""

    class Meta:
        model = Ticket
        fields = ["status", "priority", "assignee", "category", "tags"]
        widgets = {"tags": forms.CheckboxSelectMultiple}


class CommentForm(forms.ModelForm):
    # body optional so an attachment-only reply is allowed (view enforces "body or file")
    body = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 4}))

    class Meta:
        model = Comment
        fields = ["body", "internal"]


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
        fields = ["subject", "body", "priority", "category"]
        widgets = {"body": forms.Textarea(attrs={"rows": 6})}


class CloseTicketForm(forms.Form):
    """F2: close with a resolution note; cascade to linked tickets."""

    resolution = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="What was the fix? Shared with the requester.",
    )
    cascade = forms.BooleanField(
        required=False, initial=True, label="Also close linked tickets",
    )


class LinkTicketForm(forms.Form):
    """F2: link another ticket to this one by its number."""

    ticket_id = forms.IntegerField(label="Link ticket #", min_value=1)


class OrgSettingsForm(forms.ModelForm):
    """F3: in-app branding."""

    class Meta:
        model = OrgSettings
        fields = ["name", "logo", "color", "accent",
                  "sla_urgent", "sla_high", "sla_normal", "sla_low"]
        widgets = {
            "color": forms.TextInput(attrs={"type": "color"}),
            "accent": forms.TextInput(attrs={"type": "color"}),
        }


class NewUserForm(forms.ModelForm):
    """F4: add a team member. is_staff = agent access."""

    password = forms.CharField(widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ["username", "email", "password", "is_staff", "is_superuser"]
        labels = {"is_staff": "Agent (queue access)", "is_superuser": "Admin (full control)"}

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        if commit:
            user.save()
        return user
