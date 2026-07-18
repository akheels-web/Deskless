from django.urls import path

from . import views

urlpatterns = [
    # Public customer portal
    path("", views.portal_home, name="portal_home"),
    path("kb/", views.kb_list, name="kb_list"),
    path("kb/<slug:slug>/", views.kb_detail, name="kb_detail"),
    path("submit/", views.submit_ticket, name="submit_ticket"),
    path("track/", views.track_tickets, name="track_tickets"),
    path("track/<str:token>/", views.track_view, name="track_view"),
    path("rate/<str:token>/", views.rate_ticket, name="rate_ticket"),

    # Agent console
    path("dashboard/", views.dashboard, name="dashboard"),
    path("queue/", views.ticket_list, name="ticket_list"),
    path("new/", views.ticket_new, name="ticket_new"),
    path("t/<int:pk>/", views.ticket_detail, name="ticket_detail"),
    path("bulk/", views.ticket_bulk, name="ticket_bulk"),
    path("reports/", views.reports, name="reports"),
    path("team/", views.team, name="team"),
    path("settings/", views.org_settings, name="org_settings"),
]
