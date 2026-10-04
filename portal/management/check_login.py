from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.core.management.base import BaseCommand

from portal.models import Student
from portal.roles import is_hod, is_lecturer


class Command(BaseCommand):
    help = ("Find out why a login does not work.  Example:  "
            "python manage.py check_login CSC/2021/001 --password okafor")

    def add_arguments(self, parser):
        parser.add_argument("username", help="a matric number or a username")
        parser.add_argument("--password", help="also try to log in with this password")

    def good(self, text):
        self.stdout.write(self.style.SUCCESS("  OK       " + text))

    def problem(self, text):
        self.stdout.write(self.style.ERROR("  PROBLEM  " + text))

    def note(self, text):
        self.stdout.write("  info     " + text)

    def handle(self, *args, **options):
        User = get_user_model()
        name = options["username"].strip()
        self.stdout.write(f"Checking the login '{name}'")

        # ---- settings.py ----
        if "portal.auth_backends.FirstLoginBackend" in settings.AUTHENTICATION_BACKENDS:
            self.good("settings.py has the first-login backend (a student's surname can work as the first password)")
        else:
            self.problem("settings.py has no FirstLoginBackend, so a surname can never work as a first password. "
                         "Add AUTHENTICATION_BACKENDS from config/settings_changes.py.")
        if "portal.middleware.ForcePasswordChangeMiddleware" in settings.MIDDLEWARE:
            self.good("settings.py has the force-password-change middleware")
        else:
            self.problem("settings.py is missing portal.middleware.ForcePasswordChangeMiddleware in MIDDLEWARE.")

        # ---- the account and the student record ----
        user = User.objects.filter(username__iexact=name).first()
        student = Student.objects.filter(matric_number__iexact=name).first()
        if student is None and user is not None:
            student = Student.objects.filter(user=user).first()

        if user is None:
            if student is None:
                self.problem(f"There is no user and no student called '{name}'. Check the spelling, or upload the "
                             "students file (HOD > Upload data > Students).")
            else:
                self.problem(f"The student {student.matric_number} exists but has no login account (no login account "
                             "was created for them). Re-upload the students file (with a surname column) or run: "
                             "python manage.py create_student_accounts")
        else:
            self.good(f"Login account '{user.username}' exists")
            if not user.is_active:
                self.problem("The account is switched off (is_active is false). Tick Active in the admin.")

            # ---- role ----
            if is_hod(user):
                self.good("Role: HOD (can do everything)")
            elif is_lecturer(user):
                self.good("Role: Lecturer (upload results, search)")
            elif student is not None and student.user_id == user.id:
                self.good(f"Role: Student ({student.matric_number})")
            else:
                self.problem("This account has no role: it is not in the group HOD or Lecturers and is not linked to a "
                             "student record, so the portal shows 'No access yet'. A group called 'Student' does "
                             "nothing; student logins come from the Students upload.")

            # ---- student details ----
            if student is not None and student.user_id == user.id:
                if not student.surname:
                    self.problem("The student has no surname on record, so the first login cannot work. "
                                 "Re-upload the students file with a surname.")
                if user.has_usable_password():
                    self.note("The student has chosen their own password, so the surname no longer works.")
                else:
                    state = "must choose a new password at first login" if student.must_change_password \
                        else "has no password set"
                    self.note(f"No password yet: they log in with their surname ({student.surname or '?'}) and {state}.")
            elif student is not None and student.user_id is None:
                self.problem(f"The student {student.matric_number} exists but is not linked to this account.")

        # ---- try the password ----
        if options.get("password"):
            who = user.username if user else name
            if authenticate(username=who, password=options["password"]):
                self.good("Login with that password works")
            else:
                self.problem("Login with that password does NOT work. If it is a student's first login the password is "
                             "their surname; if they already chose a password, use that one.")
