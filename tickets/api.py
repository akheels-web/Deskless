from rest_framework import viewsets

from .models import Comment, Ticket
from .serializers import CommentSerializer, TicketSerializer


class TicketViewSet(viewsets.ModelViewSet):
    queryset = Ticket.objects.all()
    serializer_class = TicketSerializer
    # ponytail: no django-filter dep — add filterset_fields + django-filter when
    # an API client actually needs server-side filtering.

    def perform_create(self, serializer):
        serializer.save(reporter=self.request.user)


class CommentViewSet(viewsets.ModelViewSet):
    queryset = Comment.objects.all()
    serializer_class = CommentSerializer

    def perform_create(self, serializer):
        comment = serializer.save(author=self.request.user)
        # reuse the same reply notification as the web UI
        from .views import notify_reporter
        notify_reporter(comment.ticket, comment)
