from django.urls import path

from . import views

urlpatterns = [
    path("", views.ticket_list, name="ticket_list"),
    path("new/", views.ticket_new, name="ticket_new"),
    path("t/<int:pk>/", views.ticket_detail, name="ticket_detail"),
    path("reports/", views.reports, name="reports"),
    path("submit/", views.submit_ticket, name="submit_ticket"),
]
