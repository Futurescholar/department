from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_POST

from .forms import (
    CalendarForm,
    DataUploadForm,
    PortalLoginForm,
    ResultSearchForm,
    ResultsUploadForm,
    StudentSearchForm,
)
from .models import AcademicCalendar, Result, Student
from .registration import registration_options, save_registration
from .reports import student_overview
from .roles import is_hod, is_lecturer, is_student, role_required
from .services import (
    ensure_student_accounts,
    import_course_results,
    import_courses,
    import_files,
    import_guardians,
    import_results,
    import_students,
    read_upload,
)


# ---------------- login, logout, password ----------------

class PortalLoginView(LoginView):
    template_name = "portal/login.html"
    authentication_form = PortalLoginForm
    # No automatic redirect for people who are already logged in: the page tells them who they
    # are logged in as and offers a Log out button, so testers can switch roles easily.


class PasswordChange(PasswordChangeView):
    template_name = "portal/password_change.html"
    success_url = reverse_lazy("home")

    def get_form_class(self):
        # A student on their very first login has no old password to type, so they get
        # the "choose a password" form. Everyone else gets "old password + new password".
        if self.request.user.has_usable_password():
            return PasswordChangeForm
        return SetPasswordForm

    def form_valid(self, form):
        response = super().form_valid(form)
        Student.objects.filter(user=self.request.user).update(must_change_password=False)
        return response


@login_required
def home(request):
    """Send each person to their own starting page."""
    if is_lecturer(request.user):
        return redirect("results_search")
    if is_student(request.user):
        return redirect("my_record")
    return render(request, "portal/no_role.html")


# ---------------- student's own page ----------------

@login_required
def my_record(request):
    student = get_object_or_404(Student, user=request.user)     # only ever THEIR record
    return render(request, "portal/my_record.html", {"student": student, **student_overview(student)})


# ---------------- uploads ----------------

def _read_and_import(request, form, run):
    """Shared by both upload pages: read the file, run the importer, return the errors."""
    try:
        df = read_upload(form.cleaned_data["file"])
    except Exception:
        return None, ["Could not read the file. Check that it is a valid CSV or Excel file."]
    return run(df)


@role_required(is_lecturer)
def upload_results(request):
    errors = []
    form = ResultsUploadForm()
    if request.method == "POST":
        form = ResultsUploadForm(request.POST, request.FILES)
        if form.is_valid():
            course = form.cleaned_data["course"]
            session = form.cleaned_data["session"]
            semester = form.cleaned_data["semester"]
            saved, errors = _read_and_import(
                request, form,
                lambda df: import_course_results(df, course, session, semester))
            if not errors:
                label = dict(Result.Semester.choices)[semester]
                messages.success(
                    request,
                    f"{saved} result(s) saved for {course.code}, {session} {label} semester. "
                    f"GPA and CGPA updated.")
                form = ResultsUploadForm(initial={
                    "session": session, "semester": semester, "course": course})
    return render(request, "portal/upload_results.html", {"form": form, "errors": errors})


DATA_IMPORTERS = {
    "courses": (import_courses, "course(s)"),
    "students": (import_students, "student(s)"),
    "results": (import_results, "result(s)"),
    "guardians": (import_guardians, "new guardian(s)"),
    "files": (import_files, "new file link(s)"),
}


@role_required(is_hod)
def upload_data(request):
    errors = []
    form = DataUploadForm()
    if request.method == "POST":
        form = DataUploadForm(request.POST, request.FILES)
        if form.is_valid():
            kind = form.cleaned_data["kind"]
            importer, label = DATA_IMPORTERS[kind]
            saved, errors = _read_and_import(request, form, importer)
            if not errors:
                text = f"{saved} {label} saved."
                if kind in ("results", "courses"):
                    text += " GPA and CGPA updated."
                if kind == "students":
                    text += " Logins are ready: username = matric number, first password = surname."
                messages.success(request, text)
                form = DataUploadForm(initial={"kind": kind})
    return render(request, "portal/upload_data.html", {"form": form, "errors": errors})


# ---------------- searching (lecturers and HOD) ----------------

def _paginate(request, queryset, per_page=50):
    page = Paginator(queryset, per_page).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return page, params.urlencode()


def _words_filter(queryset, text, fields):
    """Every word typed must appear in at least one of the fields, so 'ada okafor'
    finds 'Ada Okafor' and 'okafor ada' alike."""
    for word in text.split():
        condition = Q()
        for field in fields:
            condition |= Q(**{f"{field}__icontains": word})
        queryset = queryset.filter(condition)
    return queryset


@role_required(is_lecturer)
def results_search(request):
    form = ResultSearchForm(request.GET or None)
    queryset = Result.objects.select_related("student", "course").order_by(
        "student__matric_number", "session", "semester", "course__code")

    if form.is_bound and form.is_valid():
        data = form.cleaned_data
        queryset = _words_filter(queryset, data["q"], ["student__matric_number", "student__name"])
        if data["course"]:
            queryset = queryset.filter(course=data["course"])
        if data["session"]:
            queryset = queryset.filter(session=data["session"])
        if data["semester"]:
            queryset = queryset.filter(semester=data["semester"])

    page, querystring = _paginate(request, queryset)
    return render(request, "portal/results_search.html",
                  {"form": form, "page": page, "qs": querystring})


@role_required(is_lecturer)
def students_list(request):
    form = StudentSearchForm(request.GET or None)
    queryset = Student.objects.order_by("matric_number")

    if form.is_bound and form.is_valid():
        data = form.cleaned_data
        queryset = _words_filter(queryset, data["q"], ["matric_number", "name"])
        if data["level"]:
            queryset = queryset.filter(level=data["level"])
        if data["status"]:
            queryset = queryset.filter(status=data["status"])

    page, querystring = _paginate(request, queryset)
    return render(request, "portal/students_list.html",
                  {"form": form, "page": page, "qs": querystring})


@role_required(is_lecturer)
def student_detail(request, pk):
    student = get_object_or_404(Student, pk=pk)
    hod = is_hod(request.user)
    return render(request, "portal/student_detail.html", {
        "student": student,
        **student_overview(student),
        "is_hod": hod,
        "guardians": student.guardians.all() if hod else [],
        "files": student.files.all() if hod else [],
    })


@role_required(is_hod)
@require_POST
def student_reset_password(request, pk):
    """HOD resets a student's password: they log in with their surname again and must
    choose a new password."""
    student = get_object_or_404(Student, pk=pk)
    if not student.surname:
        messages.error(request, "This student has no surname on record. Add one first "
                                "(re-upload the students file with a surname column).")
        return redirect("student_detail", pk=pk)
    if student.user is None:
        ensure_student_accounts([student.matric_number])
        student.refresh_from_db()
    student.user.set_unusable_password()
    student.user.save(update_fields=["password"])
    student.must_change_password = True
    student.save(update_fields=["must_change_password"])
    messages.success(request, f"Password reset. {student.matric_number} can log in with their "
                              f"surname and will be asked to choose a new password.")
    return redirect("student_detail", pk=pk)


# ---------------- course registration (students) ----------------

@role_required(is_student)
def register_courses(request):
    student = get_object_or_404(Student, user=request.user)
    calendar = AcademicCalendar.get_solo()
    errors = []

    if request.method == "POST":
        try:
            selected = {int(value) for value in request.POST.getlist("course")}
        except ValueError:
            selected, errors = set(), ["The selection was not valid. Please try again."]
        if not errors:
            errors, warnings = save_registration(student, calendar, selected)
            if not errors:
                messages.success(request, "Your course registration has been saved.")
                for warning in warnings:
                    messages.warning(request, warning)
                return redirect("register_courses")

    overview = student_overview(student)
    return render(request, "portal/register.html", {
        "student": student,
        "errors": errors,
        "options": registration_options(student, calendar),
        **overview,
    })


# ---------------- academic calendar (HOD) ----------------

@role_required(is_hod)
def calendar_settings(request):
    calendar = AcademicCalendar.get_solo()
    form = CalendarForm(request.POST or None, instance=calendar)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Academic calendar saved.")
        return redirect("calendar_settings")
    return render(request, "portal/calendar.html", {"form": form})

