from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Avg, Count, F, Q
from django.shortcuts import get_object_or_404, redirect, render

from .forms import AgentTicketForm, CommentForm, PublicTicketForm, TicketUpdateForm
from .models import Ticket

User = get_user_model()


# ---- Phase 2: agent UI (staff only) ----

@login_required
def ticket_list(request):
    tickets = Ticket.objects.select_related("reporter", "assignee")
    status = request.GET.get("status")
    mine = request.GET.get("mine")
    query = request.GET.get("q", "")
    if status:
        tickets = tickets.filter(status=status)
    if mine:
        tickets = tickets.filter(assignee=request.user)
    if query:
        tickets = tickets.filter(Q(subject__icontains=query) | Q(body__icontains=query))

    page_obj = Paginator(tickets, 25).get_page(request.GET.get("page"))
    # querystring (minus page) so pager links keep the active filters
    params = request.GET.copy()
    params.pop("page", None)
    qs = params.urlencode()
    return render(request, "tickets/list.html", {
        "tickets": page_obj,
        "page_obj": page_obj,
        "status_choices": Ticket.STATUS,
        "current_status": status,
        "mine": mine,
        "query": query,
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
            if cform.is_valid():
                c = cform.save(commit=False)
                c.ticket = ticket
                c.author = request.user
                c.save()
                notify_reporter(ticket, c)
                return redirect("ticket_detail", pk=pk)
            uform = TicketUpdateForm(instance=ticket)
        else:
            uform = TicketUpdateForm(request.POST, instance=ticket)
            if uform.is_valid():
                uform.save()
                return redirect("ticket_detail", pk=pk)
            cform = CommentForm()
    else:
        cform = CommentForm()
        uform = TicketUpdateForm(instance=ticket)
    return render(request, "tickets/detail.html", {
        "ticket": ticket, "comments": comments, "cform": cform, "uform": uform,
    })


@login_required
def reports(request):
    qs = Ticket.objects.all()
    by_status = dict(qs.values_list("status").annotate(n=Count("id")))
    by_priority = dict(qs.values_list("priority").annotate(n=Count("id")))
    # avg resolution time for tickets that reached resolved/closed
    resolved = qs.filter(status__in=["resolved", "closed"])
    avg_delta = resolved.aggregate(
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
        "open_count": by_status.get("open", 0) + by_status.get("pending", 0),
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
