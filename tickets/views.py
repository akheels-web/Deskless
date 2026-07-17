from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Avg, Count, F, Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import (AgentTicketForm, CloseTicketForm, CommentForm, LinkTicketForm,
                    NewUserForm, OrgSettingsForm, PublicTicketForm, TicketUpdateForm)
from .models import Attachment, CannedReply, Category, OrgSettings, Ticket
from .signals import fire_webhooks


def _save_attachments(ticket, files, user):
    """G2: persist uploaded files against a ticket."""
    for f in files:
        Attachment.objects.create(ticket=ticket, file=f, uploaded_by=user)

User = get_user_model()
admin_only = user_passes_test(lambda u: u.is_superuser)


# ---- F5: landing dashboard ----

@login_required
def dashboard(request):
    qs = Ticket.objects.all()
    by_status = dict(qs.values_list("status").annotate(n=Count("id")))
    mine = qs.filter(assignee=request.user).exclude(status="closed")
    return render(request, "tickets/dashboard.html", {
        "open_count": sum(by_status.get(s, 0) for s in Ticket.OPEN_STATES),
        "escalated_count": by_status.get("escalated", 0),
        "closed_count": by_status.get("closed", 0),
        "unassigned_count": qs.filter(assignee__isnull=True).exclude(status="closed").count(),
        "mine": mine[:8],
        "mine_count": mine.count(),
        "recent": qs.select_related("reporter")[:6],
    })


# ---- Phase 2: agent UI (staff only) ----

def _view_filter(qs, view, user):
    """Map a left-panel view name to a queryset filter."""
    if view == "mine":
        return qs.filter(assignee=user).exclude(status="closed")
    if view == "unassigned":
        return qs.filter(assignee__isnull=True).exclude(status="closed")
    if view == "closed":
        return qs.filter(status="closed")
    if view == "all":
        return qs
    return qs.exclude(status="closed")  # "open" (default): everything not closed


@login_required
def ticket_list(request):
    base = Ticket.objects.select_related("reporter", "assignee", "category")
    view = request.GET.get("view", "open")
    query = request.GET.get("q", "")
    cat = request.GET.get("category", "")

    tickets = _view_filter(base, view, request.user)
    if query:
        tickets = tickets.filter(Q(subject__icontains=query) | Q(body__icontains=query))
    if cat:
        tickets = tickets.filter(category_id=cat)

    # counts for the left panel badges
    counts = {v: _view_filter(base, v, request.user).count()
              for v in ("open", "mine", "unassigned", "closed", "all")}

    page_obj = Paginator(tickets, 25).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    qs = params.urlencode()
    return render(request, "tickets/list.html", {
        "tickets": page_obj,
        "page_obj": page_obj,
        "view": view,
        "counts": counts,
        "query": query,
        "categories": Category.objects.all(),
        "current_category": cat,
        "qs": qs + "&" if qs else "",
    })


@login_required
def ticket_new(request):
    """Agent creates a ticket on a customer's behalf (phone/walk-in)."""
    if request.method == "POST":
        form = AgentTicketForm(request.POST)
        if form.is_valid():
            reporter, _ = User.objects.get_or_create(
                username=form.cleaned_data["email"],
                defaults={"email": form.cleaned_data["email"]},
            )
            ticket = form.save(commit=False)
            ticket.reporter = reporter
            ticket.save()
            _save_attachments(ticket, request.FILES.getlist("files"), request.user)
            return redirect("ticket_detail", pk=ticket.pk)
    else:
        form = AgentTicketForm()
    return render(request, "tickets/ticket_form.html", {"form": form})


@login_required
def ticket_detail(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    comments = ticket.comments.select_related("author")
    if request.method == "POST":
        if "add_comment" in request.POST:
            cform = CommentForm(request.POST)
            files = request.FILES.getlist("files")
            if cform.is_valid() and (cform.cleaned_data["body"] or files):
                c = cform.save(commit=False)
                c.ticket = ticket
                c.author = request.user
                c.save()
                _save_attachments(ticket, files, request.user)
                notify_reporter(ticket, c)
            return redirect("ticket_detail", pk=pk)
        elif "link" in request.POST:  # F2: link another ticket
            lform = LinkTicketForm(request.POST)
            if lform.is_valid():
                other = Ticket.objects.filter(pk=lform.cleaned_data["ticket_id"]).first()
                if other and other.pk != ticket.pk:
                    ticket.related.add(other)
            return redirect("ticket_detail", pk=pk)
        elif "unlink" in request.POST:
            ticket.related.remove(request.POST["unlink"])
            return redirect("ticket_detail", pk=pk)
        elif "close" in request.POST:  # F2: close with resolution + cascade
            close_form = CloseTicketForm(request.POST)
            if close_form.is_valid():
                ticket.close_with_resolution(
                    close_form.cleaned_data["resolution"],
                    cascade=close_form.cleaned_data["cascade"],
                )
                fire_webhooks("ticket.closed", ticket)
            return redirect("ticket_detail", pk=pk)
        else:  # properties update
            prev_assignee = ticket.assignee_id
            uform = TicketUpdateForm(request.POST, instance=ticket)
            if uform.is_valid():
                ticket = uform.save()
                if ticket.assignee_id and ticket.assignee_id != prev_assignee:
                    from .notifications import notify_assignment
                    notify_assignment(ticket)  # R3: email newly-assigned agent
            return redirect("ticket_detail", pk=pk)
    return render(request, "tickets/detail.html", {
        "ticket": ticket, "comments": comments,
        "cform": CommentForm(), "uform": TicketUpdateForm(instance=ticket),
        "close_form": CloseTicketForm(), "link_form": LinkTicketForm(),
        "related": ticket.related.all(),
        "attachments": ticket.attachments.all(),
        "canned": CannedReply.objects.all(),
    })


@login_required
def reports(request):
    qs = Ticket.objects.all()
    by_status = dict(qs.values_list("status").annotate(n=Count("id")))
    by_priority = dict(qs.values_list("priority").annotate(n=Count("id")))
    # avg resolution time for closed tickets
    avg_delta = qs.filter(status="closed").aggregate(
        avg=Avg(F("updated_at") - F("created_at")))["avg"]
    by_agent = list(qs.filter(assignee__isnull=False)
                    .values("assignee__username")
                    .annotate(n=Count("id")).order_by("-n"))
    status_rows = [(lbl, by_status.get(v, 0)) for v, lbl in Ticket.STATUS]
    priority_rows = [(lbl, by_priority.get(v, 0)) for v, lbl in Ticket.PRIORITY]
    # bar scaling maxes (avoid div-by-zero → min 1)
    return render(request, "tickets/reports.html", {
        "total": qs.count(),
        "by_status": status_rows,
        "by_priority": priority_rows,
        "open_count": sum(by_status.get(s, 0) for s in Ticket.OPEN_STATES),
        "avg_resolution": _fmt_duration(avg_delta),
        "by_agent": by_agent,
        "status_max": max([n for _, n in status_rows] + [1]),
        "priority_max": max([n for _, n in priority_rows] + [1]),
        "agent_max": max([r["n"] for r in by_agent] + [1]),
    })


def _fmt_duration(delta):
    """Human 'time to resolve' — avg_delta is a timedelta or None."""
    if not delta:
        return None
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"{round(hours * 60)}m"
    if hours < 48:
        return f"{round(hours)}h"
    return f"{round(hours / 24)}d"


# ---- Phase 3: public intake ----

def submit_ticket(request):
    if request.method == "POST":
        form = PublicTicketForm(request.POST)
        if form.is_valid():
            reporter, _ = User.objects.get_or_create(
                username=form.cleaned_data["email"],
                defaults={"email": form.cleaned_data["email"],
                          "first_name": form.cleaned_data["name"]},
            )
            ticket = form.save(commit=False)
            ticket.reporter = reporter
            ticket.save()
            _save_attachments(ticket, request.FILES.getlist("files"), reporter)
            return render(request, "tickets/submitted.html", {"ticket": ticket})
    else:
        form = PublicTicketForm()
    return render(request, "tickets/submit.html", {"form": form})


def notify_reporter(ticket, comment):
    """Email the reporter when an agent leaves a public reply."""
    if comment.internal or not ticket.reporter.email:
        return
    if comment.author_id == ticket.reporter_id:
        return  # don't notify the reporter about their own message
    send_mail(
        subject=f"[Ticket #{ticket.pk}] {ticket.subject}",
        message=comment.body,
        from_email=None,  # uses DEFAULT_FROM_EMAIL
        recipient_list=[ticket.reporter.email],
        fail_silently=True,  # ponytail: don't 500 the agent if SMTP is down
    )


# ---- F4: team / role management (admins only) ----

@admin_only
def team(request):
    if request.method == "POST":
        form = NewUserForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("team")
    else:
        form = NewUserForm()
    return render(request, "tickets/team.html", {
        "form": form,
        "users": User.objects.order_by("-is_superuser", "-is_staff", "username"),
    })


# ---- F3: org settings + logo (admins only) ----

@admin_only
def org_settings(request):
    org = OrgSettings.load()
    if request.method == "POST":
        form = OrgSettingsForm(request.POST, request.FILES, instance=org)
        if form.is_valid():
            form.save()
            return redirect("org_settings")
    else:
        form = OrgSettingsForm(instance=org)
    return render(request, "tickets/settings.html", {"form": form, "org": org})
