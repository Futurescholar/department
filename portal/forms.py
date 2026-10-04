from datetime import date

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm

from .models import AcademicCalendar, Course, Result, Student


def session_choices():
    """2027/2028 down to 8 years back. Built when a form is shown so it never goes stale."""
    year = date.today().year
    return [(f"{y}/{y + 1}", f"{y}/{y + 1}") for y in range(year + 1, year - 9, -1)]


def check_upload_file(f):
    if not f.name.lower().endswith((".csv", ".xlsx")):
        raise forms.ValidationError("Upload a .csv or .xlsx file.")
    if f.size > 5 * 1024 * 1024:                       # 5 MB limit
        raise forms.ValidationError("File is larger than 5 MB.")
    return f


# ---------------- login ----------------

class PortalLoginForm(AuthenticationForm):
    """Normal login form, but the username (matric number) is not case-sensitive:
    'csc/2021/001' finds the account 'CSC/2021/001'."""

    def clean(self):
        username = (self.cleaned_data.get("username") or "").strip()
        user = get_user_model().objects.filter(username__iexact=username).first()
        if user:
            self.cleaned_data["username"] = user.username
        return super().clean()


# ---------------- results upload (lecturers) ----------------

class ResultsUploadForm(forms.Form):
    session = forms.ChoiceField(label="Session")
    semester = forms.ChoiceField(label="Semester",
                                 choices=[("", "Select semester")] + Result.Semester.choices)
    course = forms.ModelChoiceField(label="Course", queryset=Course.objects.order_by("code"),
                                    empty_label="Select course")
    file = forms.FileField(label="Results file (.csv, .xlsx)")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["session"].choices = [("", "Select session")] + session_choices()
        # No default is pre-selected on purpose: the lecturer must choose, so results
        # can't be saved to the wrong session by accident.

    def clean_file(self):
        return check_upload_file(self.cleaned_data["file"])


# ---------------- bulk data upload (HOD) ----------------

class DataUploadForm(forms.Form):
    kind = forms.ChoiceField(
        label="What are you uploading?",
        choices=[
            ("courses", "Courses"),
            ("students", "Students (also creates their logins)"),
            ("results", "Results, full format (file has course_code, session and semester columns)"),
            ("guardians", "Guardians"),
            ("files", "Student files (cloud links)"),
        ],
    )
    file = forms.FileField(label="File (.csv, .xlsx)")

    def clean_file(self):
        return check_upload_file(self.cleaned_data["file"])


# ---------------- searching ----------------

class ResultSearchForm(forms.Form):
    q = forms.CharField(label="Matric number or name", required=False)
    course = forms.ModelChoiceField(queryset=Course.objects.order_by("code"), required=False,
                                    empty_label="All courses")
    session = forms.ChoiceField(required=False)
    semester = forms.ChoiceField(required=False,
                                 choices=[("", "Both semesters")] + Result.Semester.choices)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sessions = (Result.objects.order_by("-session")
                    .values_list("session", flat=True).distinct())
        self.fields["session"].choices = [("", "All sessions")] + [(s, s) for s in sessions]


class StudentSearchForm(forms.Form):
    q = forms.CharField(label="Matric number or name", required=False)
    level = forms.ChoiceField(required=False, choices=[("", "All levels")] + Student.LEVELS)
    status = forms.ChoiceField(required=False,
                               choices=[("", "Any status")] + Student.Status.choices)


# ---------------- HOD: academic calendar ----------------

class CalendarForm(forms.ModelForm):
    current_session = forms.ChoiceField(label="Current session")

    class Meta:
        model = AcademicCalendar
        fields = ["current_session", "current_semester", "registration_open",
                  "min_units", "max_units", "warning_gpa", "watch_gpa"]
        labels = {
            "current_semester": "Current semester",
            "registration_open": "Course registration is open",
            "min_units": "Minimum units per semester (0 = no minimum)",
            "max_units": "Maximum units per semester (0 = no maximum)",
            "warning_gpa": "Warning level: GPA or CGPA at or below",
            "watch_gpa": "Watch level: GPA or CGPA at or below",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        sessions = session_choices()
        current = self.instance.current_session
        if current and current not in dict(sessions):
            sessions.append((current, current))
        self.fields["current_session"].choices = sessions

    def clean(self):
        data = super().clean()
        warning, watch = data.get("warning_gpa"), data.get("watch_gpa")
        if warning is not None and watch is not None and watch < warning:
            raise forms.ValidationError("The watch level cannot be lower than the warning level.")
        low, high = data.get("min_units"), data.get("max_units")
        if low and high and low > high:
            raise forms.ValidationError("The minimum units cannot be more than the maximum units.")
        return data
