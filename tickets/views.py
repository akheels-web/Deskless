from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Avg, Count, F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import (AgentTicketForm, CategoryForm, CloseTicketForm, CommentForm,
                    LinkTicketForm, NewUserForm, OrgSettingsForm, PublicTicketForm,
                    TicketUpdateForm)
from .models import (Article, Attachment, CannedReply, Category, Comment,
                     OrgSettings, Ticket, TicketEvent)
from .signals import fire_webhooks


def _save_attachments(ticket, files, user):
    """G2: persist uploaded files against a ticket."""
    for f in files:
        Attachment.objects.create(ticket=ticket, file=f, uploaded_by=user)


def log_event(ticket, actor, description):
    """H2: append an audit-trail entry."""
    TicketEvent.objects.create(ticket=ticket, actor=actor, description=description)


import re  # noqa: E402
_MENTION_RE = re.compile(r"@([\w.@+-]+)")


def _notify_mentions(ticket, comment, actor):
    """B4: email any staff @mentioned in a comment, and record it."""
    names = set(_MENTION_RE.findall(comment.body))
    if not names:
        return
    mentioned = User.objects.filter(username__in=names, is_staff=True).exclude(pk=actor.pk)
    from .notifications import notify_mention
    for u in mentioned:
        notify_mention(ticket, comment, u)
        log_event(ticket, actor, f"mentioned {u.username}")


User = get_user_model()
admin_only = user_passes_test(lambda u: u.is_superuser)


# ---- F5: landing dashboard ----

@login_required
def dashboard(request):
    from django.utils import timezone
    now = timezone.now()
    qs = Ticket.objects.all()
    open_qs = qs.exclude(status="closed")
    by_status = dict(qs.values_list("status").annotate(n=Count("id")))
    mine = open_qs.filter(assignee=request.user)
    overdue = open_qs.filter(due_at__lt=now)
    # unresolved by priority, for a small breakdown strip
    by_prio = dict(open_qs.values_list("priority").annotate(n=Count("id")))
    prio_rows = [(lbl, by_prio.get(v, 0)) for v, lbl in Ticket.PRIORITY]
    return render(request, "tickets/dashboard.html", {
        "open_count": sum(by_status.get(s, 0) for s in Ticket.OPEN_STATES),
        "escalated_count": by_status.get("escalated", 0),
        "closed_count": by_status.get("closed", 0),
        "unassigned_count": open_qs.filter(assignee__isnull=True).count(),
        "overdue_count": overdue.count(),
        "mine": mine.order_by("due_at")[:8],
        "mine_count": mine.count(),
        "due_soon": mine.filter(due_at__gte=now).order_by("due_at")[:5],
        "overdue_list": overdue.select_related("reporter", "assignee").order_by("due_at")[:5],
        "recent": qs.select_related("reporter")[:6],
        "prio_rows": prio_rows,
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
                _notify_mentions(ticket, c, request.user)  # B4
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
            error = _apply_ticket_update(request, ticket)
            if error:
                # re-render with the error and the attempted values
                return render(request, "tickets/detail.html", _detail_ctx(
                    request, ticket, comments, transition_error=error,
                    uform=TicketUpdateForm(request.POST, instance=ticket)))
            return redirect("ticket_detail", pk=pk)
    return render(request, "tickets/detail.html", _detail_ctx(request, ticket, comments))


def _detail_ctx(request, ticket, comments, transition_error=None, uform=None):
    return {
        "ticket": ticket, "comments": comments,
        "cform": CommentForm(), "uform": uform or TicketUpdateForm(instance=ticket),
        "link_form": LinkTicketForm(),
        "related": ticket.related.all(),
        "transition_error": transition_error,
        "escalate_targets": User.objects.filter(is_staff=True, is_active=True).exclude(pk=ticket.assignee_id),
        "attachments": ticket.attachments.all(),
        "canned": CannedReply.objects.all(),
        "events": ticket.events.select_related("actor"),
    }


def _apply_ticket_update(request, ticket):
    """Validate + apply a properties change. Returns an error string, or None on success.
    Enforces: closed/pending/escalate require a note; escalate also requires a person.
    """
    new_status = request.POST.get("status", ticket.status)
    note = request.POST.get("transition_note", "").strip()

    # T1: note required when moving INTO a note-required state
    if new_status != ticket.status and new_status in Ticket.NOTE_REQUIRED:
        if not note:
            label = dict(Ticket.STATUS)[new_status]
            return f"A note is required to move this ticket to “{label}”."
        if new_status == "escalated" and not request.POST.get("escalate_to"):
            return "Choose who to escalate this ticket to."

    prev = {"status_val": ticket.status, "status": ticket.get_status_display(),
            "priority": ticket.get_priority_display(),
            "assignee": ticket.assignee_id, "group": ticket.group_id,
            "ever_assigned": ticket.events.filter(description__startswith="assigned to").exists()}

    form = TicketUpdateForm(request.POST, instance=ticket)
    if not form.is_valid():
        return "Please correct the highlighted fields."
    ticket = form.save(commit=False)

    entering = new_status != prev["status_val"]

    # T2: SLA pause/resume around pending
    if entering and new_status == "pending":
        ticket.pause_sla()
    elif entering and prev["status_val"] == "pending":
        ticket.resume_sla()

    # #15 closing: capture resolution + cascade + CSAT
    if entering and new_status == "closed":
        ticket.save()
        ticket.close_with_resolution(note, cascade=bool(request.POST.get("cascade")))
        log_event(ticket, request.user, f"closed the ticket — {note}")
        fire_webhooks("ticket.closed", ticket)
        from .notifications import send_csat_request
        send_csat_request(ticket)
    else:
        # escalate → reassign to chosen person
        if entering and new_status == "escalated":
            target_id = request.POST.get("escalate_to")
            ticket.assignee_id = target_id
        ticket.save()
        form.save_m2m()
        if entering:
            lbl = ticket.get_status_display()
            if new_status in Ticket.NOTE_REQUIRED:
                log_event(ticket, request.user, f"moved to {lbl} — {note}")
            else:
                log_event(ticket, request.user, f"changed status to {lbl}")
            _post_comment_note(ticket, request.user, note)  # keep note in the thread

    # priority / group / assignee change logging (unchanged states)
    if ticket.get_priority_display() != prev["priority"]:
        log_event(ticket, request.user, f"set priority to {ticket.get_priority_display()}")
    if ticket.group_id != prev["group"]:
        log_event(ticket, request.user,
                  f"routed to group {ticket.group.name if ticket.group_id else 'none'}")
    if ticket.assignee_id != prev["assignee"]:
        who = ticket.assignee.username if ticket.assignee_id else "nobody"
        first = "" if prev["ever_assigned"] else " (first assignee)"
        log_event(ticket, request.user, f"assigned to {who}{first}")
        if ticket.assignee_id:
            from .notifications import notify_assignment
            notify_assignment(ticket)
    return None


def _post_comment_note(ticket, author, note):
    """Persist a transition note as an internal comment so it's visible in the thread."""
    if note:
        Comment.objects.create(ticket=ticket, author=author, body=note, internal=True)


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
    form = NewUserForm()
    if request.method == "POST":
        if "remove_user" in request.POST:  # #9 removal
            _remove_user(request, request.POST["remove_user"])
            return redirect("team")
        form = NewUserForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("team")
    return render(request, "tickets/team.html", {
        "form": form,
        # #10 Team = active staff only (customers have their own tab)
        "users": User.objects.filter(is_staff=True, is_active=True).order_by("-is_superuser", "username"),
    })


def _remove_user(request, user_id):
    """Deactivate a user (soft delete — keeps their ticket history intact).
    ponytail: deactivate not delete, because reporter/author are PROTECT FKs —
    a hard delete would fail or orphan tickets. Reactivate in Django admin.
    """
    u = User.objects.filter(pk=user_id).first()
    if u and u != request.user:  # can't remove yourself
        u.is_active = False
        u.save(update_fields=["is_active"])


# ---- C3: customer management (admins only) ----

@admin_only
def customers(request):
    from .forms import BulkCustomerForm, NewCustomerForm
    form = NewCustomerForm()
    bulk_form = BulkCustomerForm()
    added = None
    if request.method == "POST":
        if "remove_user" in request.POST:  # #11 removal
            _remove_user(request, request.POST["remove_user"])
            return redirect("customers")
        if "add_one" in request.POST:
            form = NewCustomerForm(request.POST)
            if form.is_valid():
                form.save()
                return redirect("customers")
        elif "add_bulk" in request.POST:
            bulk_form = BulkCustomerForm(request.POST)
            if bulk_form.is_valid():
                added = _bulk_add(_rows_from_text(bulk_form.cleaned_data["csv"]))
        elif "add_excel" in request.POST and request.FILES.get("xlsx"):  # #11 Excel
            added = _bulk_add(_rows_from_excel(request.FILES["xlsx"]))
    people = User.objects.filter(is_staff=False, is_active=True).order_by("-date_joined")[:100]
    return render(request, "tickets/customers.html", {
        "form": form, "bulk_form": bulk_form, "customers": people, "added": added,
    })


def _rows_from_text(text):
    import csv as csvmod
    import io
    return list(csvmod.reader(io.StringIO(text)))


def _rows_from_excel(f):
    """First col = email, second col = name. Skips a header row if present."""
    import openpyxl
    wb = openpyxl.load_workbook(f, read_only=True)
    rows = []
    for r in wb.active.iter_rows(values_only=True):
        if r and r[0]:
            rows.append([str(r[0]), str(r[1]) if len(r) > 1 and r[1] else ""])
    return rows


def _bulk_add(rows):
    """Create customers from [email, name] rows. Returns count created."""
    created = 0
    for row in rows:
        if not row:
            continue
        email = str(row[0]).strip().lower()
        name = str(row[1]).strip() if len(row) > 1 else ""
        if "@" not in email or User.objects.filter(username=email).exists():
            continue  # skip invalid/dupes/header silently — report count only
        User.objects.create_user(username=email, email=email, first_name=name)
        created += 1
    return created


# ---- C4: knowledge-base authoring (admins only) ----

@admin_only
def kb_edit(request, pk=None):
    from .forms import ArticleForm
    article = get_object_or_404(Article, pk=pk) if pk else None
    if request.method == "POST":
        form = ArticleForm(request.POST, instance=article)
        if form.is_valid():
            form.save()
            return redirect("kb_manage")
    else:
        form = ArticleForm(instance=article)
    return render(request, "tickets/kb_edit.html", {"form": form, "article": article})


@admin_only
def kb_manage(request):
    return render(request, "tickets/kb_manage.html", {
        "articles": Article.objects.select_related("category").all(),
    })


# ---- F3: org settings + logo (admins only) ----

@admin_only
def org_settings(request):
    org = OrgSettings.load()
    form = OrgSettingsForm(instance=org)
    cat_form = CategoryForm()
    if request.method == "POST":
        if "add_category" in request.POST:  # B2: create a category
            cat_form = CategoryForm(request.POST)
            if cat_form.is_valid():
                cat_form.save()
                return redirect("org_settings")
        elif "delete_category" in request.POST:
            Category.objects.filter(pk=request.POST["delete_category"]).delete()
            return redirect("org_settings")
        else:
            form = OrgSettingsForm(request.POST, request.FILES, instance=org)
            if form.is_valid():
                form.save()
                return redirect("org_settings")
    return render(request, "tickets/settings.html", {
        "form": form, "org": org,
        "cat_form": cat_form, "categories": Category.objects.all(),
    })


# ---- T3: groups & triggers console (admins only) ----

@admin_only
def groups(request):
    from .forms import GroupForm
    from .models import Group
    form = GroupForm()
    if request.method == "POST":
        if "delete_group" in request.POST:
            Group.objects.filter(pk=request.POST["delete_group"]).delete()
            return redirect("groups")
        form = GroupForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("groups")
    return render(request, "tickets/groups.html", {
        "form": form,
        "groups": Group.objects.prefetch_related("members").all(),
    })


@admin_only
def triggers(request):
    from .forms import TriggerForm
    from .models import Trigger
    form = TriggerForm()
    if request.method == "POST":
        if "delete_trigger" in request.POST:
            Trigger.objects.filter(pk=request.POST["delete_trigger"]).delete()
            return redirect("triggers")
        form = TriggerForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("triggers")
    return render(request, "tickets/triggers.html", {
        "form": form,
        "triggers": Trigger.objects.select_related("set_group").all(),
    })


@admin_only
def sla_policies(request):
    from .forms import SLAPolicyForm
    from .models import SLAPolicy
    form = SLAPolicyForm()
    if request.method == "POST":
        if "delete_policy" in request.POST:
            SLAPolicy.objects.filter(pk=request.POST["delete_policy"]).delete()
            return redirect("sla_policies")
        form = SLAPolicyForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("sla_policies")
    return render(request, "tickets/sla_policies.html", {
        "form": form,
        "policies": SLAPolicy.objects.select_related("group", "category").all(),
    })


@admin_only
def canned_replies(request):
    from .forms import CannedReplyForm
    form = CannedReplyForm()
    if request.method == "POST":
        if "delete_canned" in request.POST:
            CannedReply.objects.filter(pk=request.POST["delete_canned"]).delete()
            return redirect("canned_replies")
        form = CannedReplyForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("canned_replies")
    return render(request, "tickets/canned.html", {
        "form": form, "replies": CannedReply.objects.all(),
    })


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


# ---- U2: public status page + U3: reopen ----

def ticket_status(request, token):
    """Public per-ticket status page, keyed by the ticket's signed token."""
    ticket = get_object_or_404(Ticket, csat_token=token)
    if request.method == "POST" and "reopen" in request.POST:
        if ticket.can_reopen():
            ticket.status = "open"
            ticket.closed_at = None
            ticket.save(update_fields=["status", "closed_at"])
            reason = request.POST.get("reason", "").strip()
            log_event(ticket, None, f"reopened by requester{': ' + reason if reason else ''}")
            if reason:
                Comment.objects.create(ticket=ticket, author=ticket.reporter, body=reason)
        return redirect("ticket_status", token=token)
    # only public (non-internal) comments are visible to the customer
    public_comments = ticket.comments.filter(internal=False).select_related("author")
    return render(request, "tickets/status.html", {
        "ticket": ticket, "public_comments": public_comments,
    })


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
    """Public help-center landing. Staff go straight to their console (option B),
    unless they explicitly asked to preview the portal (?preview=1)."""
    if request.user.is_authenticated and request.user.is_staff and not request.GET.get("preview"):
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
