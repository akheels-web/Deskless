from django.apps import AppConfig


class TicketsConfig(AppConfig):
    name = 'tickets'

    def ready(self):
        from . import signals  # noqa: F401  register post_save triage
