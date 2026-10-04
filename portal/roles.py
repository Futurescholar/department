from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import Student
# Three roles:
#   HOD       group "HOD"        -> everything, including bulk data uploads and emailing parents
#   Lecturer  group "Lecturers"  -> upload results, search and view students' results
#   Student   has a Student record linked to their login -> sees only their own record


def is_hod(user):
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name="HOD").exists())


def is_lecturer(user):
    # HODs can do everything lecturers can.
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name__in=["Lecturers", "HOD"]).exists())


def is_student(user):
    return user.is_authenticated and Student.objects.filter(user=user).exists()


def role_required(check):
    """Decorator: log in first, then give a 403 'not allowed' page if the role check fails."""
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not check(request.user):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return wrapper
    return decorator