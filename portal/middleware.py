from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

from .models import Student


class ForcePasswordChangeMiddleware:
    """Until a student has chosen their own password, every page sends them to the
    'choose a password' page (except that page itself, logout and static files)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated:
            allowed = (reverse("password_change"), reverse("logout"), settings.STATIC_URL)
            if not request.path.startswith(allowed):
                if Student.objects.filter(user=user, must_change_password=True).exists():
                    return redirect("password_change")
        return self.get_response(request)
