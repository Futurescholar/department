from django.apps import AppConfig
from django.db.models.signals import post_migrate


def create_role_groups(sender, **kwargs):
    """After every 'migrate', make sure the two staff groups exist."""
    from django.contrib.auth.models import Group
    for name in ("Lecturers", "HOD"):
        Group.objects.get_or_create(name=name)


class PortalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "portal"

    def ready(self):
        post_migrate.connect(create_role_groups, sender=self)

