from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Avg, Count, F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import (AgentTicketForm, CloseTicketForm, CommentForm, LinkTicketForm,
                    NewUserForm, OrgSettingsForm, PublicTicketForm, TicketUpdateForm)
from .models import (Article, Attachment, CannedReply, Category, OrgSettings,
                     Ticket, TicketEvent)
from .signals import fire_webhooks


def _save_attachments(ticket, files, user):
    """G2: persist uploaded files against a ticket."""
    for f in files:
        Attachment.objects.create(ticket=ticket, file=f, uploaded_by=user)


def log_event(ticket, actor, description):
    """H2: append an audit-trail entry."""
    TicketEvent.objects.create(ticket=ticket, actor=actor, description=description)


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
                log_event(ticket, request.user, "closed the ticket")
                fire_webhooks("ticket.closed", ticket)
                from .notifications import send_csat_request
                send_csat_request(ticket)  # H3: ask the requester to rate
            return redirect("ticket_detail", pk=pk)
        else:  # properties update
            prev = {"status": ticket.get_status_display(),
                    "priority": ticket.get_priority_display(),
                    "assignee": ticket.assignee_id}
            uform = TicketUpdateForm(request.POST, instance=ticket)
            if uform.is_valid():
                ticket = uform.save()
                # H2: record what actually changed
                if ticket.get_status_display() != prev["status"]:
                    log_event(ticket, request.user, f"changed status to {ticket.get_status_display()}")
                if ticket.get_priority_display() != prev["priority"]:
                    log_event(ticket, request.user, f"set priority to {ticket.get_priority_display()}")
                if ticket.assignee_id != prev["assignee"]:
                    who = ticket.assignee.username if ticket.assignee_id else "nobody"
                    log_event(ticket, request.user, f"assigned to {who}")
                    if ticket.assignee_id:
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
        "events": ticket.events.select_related("actor"),
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
    # H3: CSAT — average rating + response count over rated tickets
    csat = qs.filter(csat_rating__isnull=False).aggregate(
        avg=Avg("csat_rating"), n=Count("id"))
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
        "csat_avg": round(csat["avg"], 1) if csat["avg"] else None,
        "csat_n": csat["n"],
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


# ---- H4: bulk actions ----

@login_required
def ticket_bulk(request):
    """Apply an action to many selected tickets from the queue."""
    ids = request.POST.getlist("ids")
    action = request.POST.get("action")
    tickets = Ticket.objects.filter(pk__in=ids)
    if action == "close":
        for t in tickets.exclude(status="closed"):
            t.close_with_resolution("Closed in bulk by an agent.", cascade=False)
            log_event(t, request.user, "closed the ticket (bulk)")
    elif action == "assign_me":
        for t in tickets:
            t.assignee = request.user
            t.save(update_fields=["assignee"])
            log_event(t, request.user, f"assigned to {request.user.username} (bulk)")
    elif action and action.startswith("status:"):
        new = action.split(":", 1)[1]
        if new in dict(Ticket.STATUS):
            for t in tickets:
                t.status = new
                t.save(update_fields=["status"])
                log_event(t, request.user, f"changed status to {t.get_status_display()} (bulk)")
    return redirect(request.POST.get("next") or "ticket_list")


# ---- H3: CSAT public rating ----

def rate_ticket(request, token):
    ticket = get_object_or_404(Ticket, csat_token=token)
    if request.method == "POST":
        try:
            rating = int(request.POST.get("rating", 0))
        except ValueError:
            rating = 0
        if 1 <= rating <= 5:
            ticket.csat_rating = rating
            ticket.save(update_fields=["csat_rating"])
            return render(request, "tickets/rated.html", {"ticket": ticket, "rating": rating})
    return render(request, "tickets/rate.html", {"ticket": ticket})


# ---- H5: knowledge base ----

def kb_list(request):
    articles = Article.objects.filter(published=True).select_related("category")
    query = request.GET.get("q", "")
    if query:
        articles = articles.filter(Q(title__icontains=query) | Q(body__icontains=query))
    return render(request, "tickets/kb_list.html", {"articles": articles, "query": query})


def kb_detail(request, slug):
    article = get_object_or_404(Article, slug=slug, published=True)
    return render(request, "tickets/kb_detail.html", {"article": article})


# ---- Customer portal ----

def portal_home(request):
    """Public help-center landing. Staff go straight to their console (option B)."""
    if request.user.is_authenticated and request.user.is_staff:
        return redirect("dashboard")
    return render(request, "tickets/portal_home.html", {
        "articles": Article.objects.filter(published=True).select_related("category")[:5],
    })


# ---- P4: magic-link ticket tracking ----
# ponytail: sign the email into a link with django's signer — no token model,
# no password. Link expires via max_age at verification time.
from django.core import signing  # noqa: E402

TRACK_SALT = "deskless.track"


def track_tickets(request):
    """Customer enters email → we email them a signed link to their tickets."""
    sent = False
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        if email:
            # Only email a link if they actually have tickets — avoids confirming
            # nothing, and prevents using us as an open relay to arbitrary addresses.
            if Ticket.objects.filter(reporter__email__iexact=email).exists():
                token = signing.dumps(email, salt=TRACK_SALT)
                link = request.build_absolute_uri(
                    reverse("track_view", args=[token]))
                from .notifications import send_track_link
                send_track_link(email, link)
            sent = True  # always show the same confirmation
    return render(request, "tickets/track.html", {"sent": sent})


def track_view(request, token):
    """Show the tickets for the email encoded in a signed, time-limited link."""
    try:
        email = signing.loads(token, salt=TRACK_SALT, max_age=60 * 60 * 24 * 7)  # 7 days
    except signing.BadSignature:
        return render(request, "tickets/track.html", {"expired": True})
    tickets = (Ticket.objects.filter(reporter__email__iexact=email)
               .order_by("-updated_at"))
    return render(request, "tickets/track_list.html", {"tickets": tickets, "email": email})
