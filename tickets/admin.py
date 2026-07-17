from django.contrib import admin

from .models import (Attachment, CannedReply, Category, Comment, OrgSettings,
                     Tag, Ticket, Webhook)


class CommentInline(admin.TabularInline):
    model = Comment
    extra = 1


class AttachmentInline(admin.TabularInline):
    model = Attachment
    extra = 0


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("id", "subject", "status", "priority", "category", "reporter", "assignee", "updated_at")
    list_filter = ("status", "priority", "category", "tags", "assignee")
    search_fields = ("subject", "body")
    list_editable = ("status", "priority", "category")
    filter_horizontal = ("related", "tags")
    inlines = [CommentInline, AttachmentInline]


admin.site.register(Category)
admin.site.register(Tag)


@admin.register(CannedReply)
class CannedReplyAdmin(admin.ModelAdmin):
    list_display = ("title",)
    search_fields = ("title", "body")


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
