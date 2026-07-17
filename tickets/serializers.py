from rest_framework import serializers

from .models import Comment, Ticket


class CommentSerializer(serializers.ModelSerializer):
    author = serializers.ReadOnlyField(source="author.username")

    class Meta:
        model = Comment
        fields = ["id", "ticket", "author", "body", "internal", "created_at"]
        read_only_fields = ["author", "created_at"]


class TicketSerializer(serializers.ModelSerializer):
    comments = CommentSerializer(many=True, read_only=True)
    reporter = serializers.ReadOnlyField(source="reporter.username")

    class Meta:
        model = Ticket
        fields = ["id", "subject", "body", "status", "priority", "resolution",
                  "reporter", "assignee", "category", "tags", "related",
                  "created_at", "updated_at", "comments"]
        read_only_fields = ["reporter", "created_at", "updated_at"]
