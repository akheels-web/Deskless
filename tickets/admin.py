from django.contrib import admin

from .models import Comment, OrgSettings, Ticket, Webhook


class CommentInline(admin.TabularInline):
    model = Comment
    extra = 1


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("id", "subject", "status", "priority", "reporter", "assignee", "updated_at")
    list_filter = ("status", "priority", "assignee")
    search_fields = ("subject", "body")
    list_editable = ("status", "priority", "assignee")
    filter_horizontal = ("related",)
    inlines = [CommentInline]


@admin.register(OrgSettings)
class OrgSettingsAdmin(admin.ModelAdmin):
    list_display = ("name", "color", "accent")


@admin.register(Webhook)
class WebhookAdmin(admin.ModelAdmin):
    list_display = ("event", "url", "active")
    list_filter = ("event", "active")


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "author", "internal", "created_at")
    list_filter = ("internal",)
