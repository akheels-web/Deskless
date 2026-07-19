from django import forms
from django.contrib.auth import get_user_model

from .models import Category, Comment, Group, OrgSettings, Ticket, Trigger

User = get_user_model()


class CategoryForm(forms.ModelForm):
    """B2: create a category from the settings page."""

    class Meta:
        model = Category
        fields = ["name"]
        widgets = {"name": forms.TextInput(attrs={"placeholder": "e.g. Billing"})}


class GroupForm(forms.ModelForm):
    """T3: create/edit a group + members, in-console."""

    class Meta:
        model = Group
        fields = ["name", "members"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Networking"}),
            "members": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["members"].queryset = User.objects.filter(is_staff=True, is_active=True)


class TriggerForm(forms.ModelForm):
    """T3: create/edit an auto-routing rule, in-console."""

    class Meta:
        model = Trigger
        fields = ["name", "keyword", "set_group", "set_priority", "active"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. VPN issues"}),
            "keyword": forms.TextInput(attrs={"placeholder": "vpn"}),
        }


class CannedReplyForm(forms.ModelForm):
    """T5: manage saved replies in-console."""

    class Meta:
        from .models import CannedReply
        model = CannedReply
        fields = ["title", "body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 4})}


class SLAPolicyForm(forms.ModelForm):
    """U1: per-group/category SLA target, in-console."""

    class Meta:
        from .models import SLAPolicy
        model = SLAPolicy
        fields = ["group", "category", "priority", "hours"]


class TicketUpdateForm(forms.ModelForm):
    """Agent-side: change status/priority/assignee/category/tags."""

    class Meta:
        model = Ticket
        fields = ["status", "priority", "assignee", "group", "category", "tags"]
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


WEEKDAYS = [("0", "Monday"), ("1", "Tuesday"), ("2", "Wednesday"),
            ("3", "Thursday"), ("4", "Friday"), ("5", "Saturday"), ("6", "Sunday")]


class OrgSettingsForm(forms.ModelForm):
    """F3: in-app branding. B3: weekday checkboxes + holidays."""

    business_days = forms.MultipleChoiceField(
        choices=WEEKDAYS, widget=forms.CheckboxSelectMultiple, required=False,
        label="Working days")

    class Meta:
        model = OrgSettings
        fields = ["name", "logo", "color", "accent",
                  "sla_urgent", "sla_high", "sla_normal", "sla_low",
                  "business_hours_enabled", "business_start", "business_end",
                  "business_days", "holidays", "reopen_days",
                  "site_url", "email_host", "email_port", "email_user",
                  "email_password", "email_use_tls", "email_from"]
        widgets = {
            "color": forms.TextInput(attrs={"type": "color"}),
            "accent": forms.TextInput(attrs={"type": "color"}),
            "holidays": forms.Textarea(attrs={"rows": 4, "placeholder": "2026-12-25\n2026-01-01"}),
            "email_password": forms.PasswordInput(render_value=True),
            "site_url": forms.TextInput(attrs={"placeholder": "https://deskless.example.com"}),
            "email_host": forms.TextInput(attrs={"placeholder": "smtp.gmail.com"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # model stores "0,1,2,3,4" → checkbox list needs ["0","1",...]
        if self.instance and self.instance.business_days:
            self.initial["business_days"] = self.instance.business_days.split(",")

    def clean_business_days(self):
        # checkbox list → comma string for the CharField
        return ",".join(self.cleaned_data["business_days"])


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


class NewCustomerForm(forms.Form):
    """C3: admin adds one customer (a non-staff user)."""

    name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField()

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(username=email).exists():
            raise forms.ValidationError("A user with that email already exists.")
        return email

    def save(self):
        d = self.cleaned_data
        return User.objects.create_user(
            username=d["email"], email=d["email"], first_name=d.get("name", ""))


class BulkCustomerForm(forms.Form):
    """C3: paste CSV — 'email,name' per line — to create many customers."""

    csv = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "jane@acme.com,Jane Doe\nbob@acme.com,Bob"}),
        help_text="One per line: email,name")


class ArticleForm(forms.ModelForm):
    """C4: author a knowledge-base article from the console."""

    class Meta:
        from .models import Article
        model = Article
        fields = ["title", "category", "body", "published"]  # #1 slug auto-generated
        widgets = {"body": forms.Textarea(attrs={"rows": 12})}

    def save(self, commit=True):
        from django.utils.text import slugify
        from .models import Article
        article = super().save(commit=False)
        if not article.slug:
            base = slugify(article.title)[:200] or "article"
            slug, n = base, 2
            # ensure uniqueness without clobbering this article's own slug
            while Article.objects.filter(slug=slug).exclude(pk=article.pk).exists():
                slug = f"{base}-{n}"; n += 1
            article.slug = slug
        if commit:
            article.save()
        return article
