from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from .models import Student


class FirstLoginBackend(ModelBackend):
    """A student who has not chosen a password yet can log in with their SURNAME
    (capital letters don't matter). The middleware then forces them to set a real
    password. Once a password is set this backend steps aside and Django's normal
    login takes over."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None
        User = get_user_model()
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            return None
        if user.has_usable_password() or not self.user_can_authenticate(user):
            return None          # has a real password already: normal login handles it
        student = Student.objects.filter(user=user, must_change_password=True).first()
        if student and student.surname and password.strip().lower() == student.surname.strip().lower():
            return user
        return None

