from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from .models import Course, Guardian, Result, SemesterGPA, Student, StudentFile
from .services import ensure_student_accounts

FAST_HASHER = ["django.contrib.auth.hashers.MD5PasswordHasher"]   # makes the tests quicker
PASSWORD = "Passw0rd!x"
FULL_HEAD = "matric_number,course_code,score,session,semester\n"
STUDENT_HEAD = "matric_number,name,surname,email,level\n"


@override_settings(PASSWORD_HASHERS=FAST_HASHER)
class PortalTestCase(TestCase):
    """Common set-up: an HOD, a lecturer, two students (with logins) and one course."""

    def setUp(self):
        self.hod = self.staff_user("hod", "HOD")
        self.lecturer = self.staff_user("lect", "Lecturers")
        self.student = Student.objects.create(
            matric_number="CSC/2021/001", name="Ada Okafor", surname="Okafor",
            email="ada@x.com", level=200)
        self.student2 = Student.objects.create(
            matric_number="CSC/2021/002", name="Bola Ade", surname="Ade",
            email="bola@x.com", level=200)
        self.csc201 = Course.objects.create(code="CSC201", title="Data Structures", credit_units=3)
        ensure_student_accounts(["CSC/2021/001", "CSC/2021/002"])
        self.student.refresh_from_db()      # reload so the objects know about their new logins
        self.student2.refresh_from_db()

    def staff_user(self, username, group):
        user = User.objects.create_user(username, password=PASSWORD)
        user.groups.add(Group.objects.get(name=group))
        return user

    def login(self, user):
        self.assertTrue(self.client.login(username=user.username, password=PASSWORD))

    def login_student(self, student):
        """Give the student a real password and log in (skips the first-login step)."""
        student.user.set_password(PASSWORD)
        student.user.save()
        Student.objects.filter(pk=student.pk).update(must_change_password=False)
        self.assertTrue(self.client.login(username=student.matric_number, password=PASSWORD))

    # ---- upload helpers ----
    def post_results(self, text, course=None, session="2024/2025", semester="HARMATTAN", name="r.csv"):
        f = SimpleUploadedFile(name, text.encode(), content_type="text/csv")
        return self.client.post(reverse("upload_results"), {
            "file": f, "course": (course or self.csc201).pk,
            "session": session, "semester": semester})

    def post_data(self, text, kind, name="d.csv"):
        f = SimpleUploadedFile(name, text.encode(), content_type="text/csv")
        return self.client.post(reverse("upload_data"), {"file": f, "kind": kind})


# ======================================================================
#  Logins and passwords
# ======================================================================
class AccountTests(PortalTestCase):

    def test_students_upload_creates_logins(self):
        self.login(self.hod)
        self.post_data(STUDENT_HEAD + "CSC/2021/003,Chidi Eze,Eze,chidi@x.com,300\n", "students")
        s = Student.objects.get(matric_number="CSC/2021/003")
        self.assertEqual(s.user.username, "CSC/2021/003")
        self.assertFalse(s.user.has_usable_password())     # no password yet: surname is used at first login
        self.assertTrue(s.must_change_password)

    def test_first_login_with_surname_then_forced_to_choose_password(self):
        resp = self.client.post(reverse("login"), {"username": "CSC/2021/001", "password": "okafor"})
        self.assertEqual(resp.status_code, 302)             # surname works, in any capitals
        # every page now redirects to "choose a password"
        self.assertRedirects(self.client.get(reverse("my_record")), reverse("password_change"))

        resp = self.client.post(reverse("password_change"),
                                {"new_password1": "Brand-new-pass-77", "new_password2": "Brand-new-pass-77"})
        self.assertEqual(resp.status_code, 302)
        self.student.refresh_from_db()
        self.assertFalse(self.student.must_change_password)
        self.assertEqual(self.client.get(reverse("my_record")).status_code, 200)

        # log out, then: new password works, surname no longer does
        self.client.post(reverse("logout"))
        self.client.post(reverse("login"), {"username": "CSC/2021/001", "password": "okafor"})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.client.post(reverse("login"), {"username": "CSC/2021/001", "password": "Brand-new-pass-77"})
        self.assertIn("_auth_user_id", self.client.session)

    def test_username_is_not_case_sensitive(self):
        self.client.post(reverse("login"), {"username": "csc/2021/001", "password": "Okafor"})
        self.assertIn("_auth_user_id", self.client.session)

    def test_wrong_password_rejected(self):
        self.client.post(reverse("login"), {"username": "CSC/2021/001", "password": "wrong"})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_reupload_does_not_reset_a_chosen_password(self):
        self.login_student(self.student)                    # student has chosen their own password
        self.client.logout()
        self.login(self.hod)
        self.post_data(STUDENT_HEAD + "CSC/2021/001,Ada Okafor,Newname,ada@x.com,200\n", "students")
        self.client.logout()
        self.client.post(reverse("login"), {"username": "CSC/2021/001", "password": "Newname"})
        self.assertNotIn("_auth_user_id", self.client.session)    # new surname is NOT a password now
        self.client.post(reverse("login"), {"username": "CSC/2021/001", "password": PASSWORD})
        self.assertIn("_auth_user_id", self.client.session)       # chosen password still works

    def test_hod_can_reset_a_students_password(self):
        self.login_student(self.student)
        self.client.logout()
        self.login(self.hod)
        resp = self.client.post(reverse("student_reset_password", args=[self.student.pk]))
        self.assertEqual(resp.status_code, 302)
        self.student.refresh_from_db()
        self.assertTrue(self.student.must_change_password)
        self.assertFalse(self.student.user.has_usable_password())
        self.client.logout()
        self.client.post(reverse("login"), {"username": "CSC/2021/001", "password": "Okafor"})
        self.assertIn("_auth_user_id", self.client.session)

    def test_management_command_creates_missing_accounts(self):
        s = Student.objects.create(matric_number="CSC/2021/009", name="Dayo Bello", surname="Bello",
                                   email="dayo@x.com", level=100)
        self.assertIsNone(s.user)
        call_command("create_student_accounts")
        s.refresh_from_db()
        self.assertEqual(s.user.username, "CSC/2021/009")


# ======================================================================
#  Who may see what
# ======================================================================
class RoleTests(PortalTestCase):

    def test_anonymous_visitors_are_sent_to_login(self):
        for name in ("home", "my_record", "upload_results", "upload_data", "results_search", "students_list"):
            resp = self.client.get(reverse(name))
            self.assertEqual(resp.status_code, 302, name)
            self.assertIn(reverse("login"), resp["Location"])

    def test_student_sees_own_record_only(self):
        other = Course.objects.create(code="ZZZ999", title="Other Course", credit_units=2)
        Result.objects.create(student=self.student2, course=other, score=60, session="2024/2025",
                              semester="HARMATTAN")
        Result.objects.create(student=self.student, course=self.csc201, score=72, session="2024/2025",
                              semester="HARMATTAN")
        self.login_student(self.student)
        resp = self.client.get(reverse("my_record"))
        self.assertContains(resp, "CSC201")
        self.assertNotContains(resp, "ZZZ999")
        self.assertNotContains(resp, "CSC/2021/002")

    def test_student_is_blocked_from_staff_pages(self):
        self.login_student(self.student)
        for name, args in (("upload_results", []), ("upload_data", []), ("results_search", []),
                           ("students_list", []), ("student_detail", [self.student2.pk])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403, name)

    def test_lecturer_can_upload_results_and_search_but_not_bulk_upload(self):
        self.login(self.lecturer)
        for name in ("upload_results", "results_search", "students_list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("upload_data")).status_code, 403)
        resp = self.client.post(reverse("student_reset_password", args=[self.student.pk]))
        self.assertEqual(resp.status_code, 403)

    def test_hod_can_do_everything(self):
        self.login(self.hod)
        for name in ("upload_results", "upload_data", "results_search", "students_list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_lecturer_does_not_see_guardians_but_hod_does(self):
        Guardian.objects.create(student=self.student, email="mum@secret.com")
        self.login(self.lecturer)
        self.assertNotContains(self.client.get(reverse("student_detail", args=[self.student.pk])),
                               "mum@secret.com")
        self.client.logout()
        self.login(self.hod)
        self.assertContains(self.client.get(reverse("student_detail", args=[self.student.pk])),
                            "mum@secret.com")

    def test_home_redirects_by_role(self):
        self.login(self.lecturer)
        self.assertRedirects(self.client.get(reverse("home")), reverse("results_search"))
        self.client.logout()
        self.login_student(self.student)
        self.assertRedirects(self.client.get(reverse("home")), reverse("my_record"))


# ======================================================================
#  Lecturer results upload (session / semester / course chosen on the page)
# ======================================================================
class ResultUploadTests(PortalTestCase):

    def setUp(self):
        super().setUp()
        self.login(self.lecturer)

    def test_matric_and_score(self):
        self.post_results("matric_number,score\nCSC/2021/001,72\nCSC/2021/002,45\n")
        r = Result.objects.get(student=self.student)
        self.assertEqual(r.grade_point, Decimal("5.00"))
        self.assertEqual((r.course, r.session, r.semester), (self.csc201, "2024/2025", "HARMATTAN"))
        self.assertEqual(Result.objects.get(student=self.student2).grade_point, Decimal("2.00"))
        self.student.refresh_from_db()
        self.assertEqual(self.student.cgpa, Decimal("5.00"))

    def test_name_and_score(self):
        self.post_results("name,score\nAda Okafor,72\n")
        self.assertEqual(Result.objects.get().student, self.student)

    def test_name_order_and_capitals_do_not_matter(self):
        self.post_results('name,score\n"OKAFOR, ada",72\n')
        self.assertEqual(Result.objects.get().student, self.student)

    def test_matric_name_and_score(self):
        self.post_results("matric_number,name,score\nCSC/2021/001,Ada Okafor,72\n")
        self.assertEqual(Result.objects.count(), 1)

    def test_name_that_does_not_match_the_matric_is_rejected(self):
        resp = self.post_results("matric_number,name,score\nCSC/2021/001,Bola Ade,72\n")
        self.assertEqual(Result.objects.count(), 0)
        self.assertContains(resp, "does not match")

    def test_ambiguous_name_is_rejected_not_guessed(self):
        Student.objects.create(matric_number="CSC/2021/003", name="Ada Okafor", surname="Okafor",
                               email="ada2@x.com", level=200)
        resp = self.post_results("name,score\nAda Okafor,72\n")
        self.assertEqual(Result.objects.count(), 0)
        self.assertContains(resp, "matches 2 students")

    def test_unknown_name_and_matric(self):
        resp = self.post_results("name,score\nNobody Here,72\n")
        self.assertContains(resp, "no student named")
        resp = self.post_results("matric_number,score\nNOPE/1,72\n")
        self.assertContains(resp, "unknown matric number")
        self.assertEqual(Result.objects.count(), 0)

    def test_file_needs_matric_or_name_and_a_score(self):
        self.assertContains(self.post_results("score\n72\n"), "needs a &#x27;matric_number&#x27; column")
        self.assertContains(self.post_results("matric_number\nCSC/2021/001\n"), "Missing column: score")
        self.assertEqual(Result.objects.count(), 0)

    def test_one_bad_row_saves_nothing(self):
        resp = self.post_results("matric_number,score\nCSC/2021/001,72\nCSC/2021/002,150\n")
        self.assertEqual(Result.objects.count(), 0)
        self.assertContains(resp, "must be a number from 0 to 100")

    def test_same_student_twice_in_one_file_is_rejected(self):
        resp = self.post_results("matric_number,score\nCSC/2021/001,50\nCSC/2021/001,60\n")
        self.assertEqual(Result.objects.count(), 0)
        self.assertContains(resp, "more than once")

    def test_blank_rows_are_skipped(self):
        self.post_results("matric_number,score\nCSC/2021/001,72\n,\n,\n")
        self.assertEqual(Result.objects.count(), 1)

    def test_common_heading_variants_are_accepted(self):
        self.post_results("Matric No,Total\nCSC/2021/001,72\n")
        self.assertEqual(Result.objects.count(), 1)

    def test_session_and_semester_must_be_chosen_from_the_lists(self):
        self.post_results("matric_number,score\nCSC/2021/001,72\n", session="1999/2000")
        self.post_results("matric_number,score\nCSC/2021/001,72\n", semester="SUMMER")
        self.post_results("matric_number,score\nCSC/2021/001,72\n", session="")
        self.assertEqual(Result.objects.count(), 0)

    def test_reupload_replaces_the_score_and_gpa_follows(self):
        self.post_results("matric_number,score\nCSC/2021/001,50\n")
        self.post_results("matric_number,score\nCSC/2021/001,80\n")
        self.assertEqual(Result.objects.count(), 1)
        self.assertEqual(Result.objects.get().score, Decimal("80.00"))
        self.student.refresh_from_db()
        self.assertEqual(self.student.cgpa, Decimal("5.00"))

    def test_gpa_per_semester_and_cgpa_across_uploads(self):
        mth = Course.objects.create(code="MTH201", title="Calculus", credit_units=2)
        csc202 = Course.objects.create(code="CSC202", title="Algorithms", credit_units=3)
        self.post_results("matric_number,score\nCSC/2021/001,72\n")                          # 3 units x 5
        self.post_results("matric_number,score\nCSC/2021/001,55\n", course=mth)              # 2 units x 3
        self.post_results("matric_number,score\nCSC/2021/001,65\n", course=csc202, semester="RAIN")  # 3 x 4
        harmattan = SemesterGPA.objects.get(student=self.student, semester="HARMATTAN")
        rain = SemesterGPA.objects.get(student=self.student, semester="RAIN")
        self.assertEqual(harmattan.gpa, Decimal("4.20"))     # 21 / 5
        self.assertEqual(rain.gpa, Decimal("4.00"))          # 12 / 3
        self.student.refresh_from_db()
        self.assertEqual(self.student.cgpa, Decimal("4.13"))  # 33 / 8 = 4.125

    def test_wrong_file_type_rejected(self):
        resp = self.post_results("hello", name="notes.txt")
        self.assertContains(resp, "Upload a .csv or .xlsx file")

    def test_real_excel_file_works(self):
        wb = Workbook()
        ws = wb.active
        ws.append(["Matric No", "Name", "Score"])
        ws.append(["CSC/2021/001", "Ada Okafor", 72])
        buffer = BytesIO()
        wb.save(buffer)
        f = SimpleUploadedFile("scores.xlsx", buffer.getvalue())
        self.client.post(reverse("upload_results"), {
            "file": f, "course": self.csc201.pk, "session": "2024/2025", "semester": "RAIN"})
        r = Result.objects.get()
        self.assertEqual((r.score, r.semester), (Decimal("72"), "RAIN"))


# ======================================================================
#  HOD bulk uploads
# ======================================================================
class DataUploadTests(PortalTestCase):

    def setUp(self):
        super().setUp()
        self.login(self.hod)

    def test_courses_upload_creates_and_updates(self):
        self.post_data("code,title,credit_units\nmth201,Calculus,2\nCSC202,Algorithms,3\n", "courses")
        self.assertEqual(Course.objects.count(), 3)
        self.assertEqual(Course.objects.get(code="MTH201").credit_units, 2)
        self.post_data("code,title,credit_units\nMTH201,Calculus,4\n", "courses")
        self.assertEqual(Course.objects.count(), 3)
        self.assertEqual(Course.objects.get(code="MTH201").credit_units, 4)

    def test_bad_course_row_saves_nothing(self):
        resp = self.post_data("code,title,credit_units\nMTH201,Calculus,2\nPHY101,Physics,abc\n", "courses")
        self.assertEqual(Course.objects.count(), 1)
        self.assertContains(resp, "credit_units")

    def test_changing_course_units_recalculates_cgpa(self):
        self.post_data("code,title,credit_units\nMTH201,Calculus,2\n", "courses")
        self.post_data(FULL_HEAD + "CSC/2021/001,CSC201,72,2024/2025,Harmattan\n"
                       "CSC/2021/001,MTH201,55,2024/2025,Harmattan\n", "results")
        self.student.refresh_from_db()
        self.assertEqual(self.student.cgpa, Decimal("4.20"))        # 21 / 5
        self.post_data("code,title,credit_units\nMTH201,Calculus,4\n", "courses")
        self.student.refresh_from_db()
        self.assertEqual(self.student.cgpa, Decimal("3.86"))        # 27 / 7

    def test_students_upload_needs_a_surname(self):
        resp = self.post_data("matric_number,name,email,level\nCSC/2021/003,Chidi Eze,chidi@x.com,300\n",
                              "students")
        self.assertContains(resp, "Missing column")
        resp = self.post_data(STUDENT_HEAD + "CSC/2021/003,Chidi Eze,,chidi@x.com,300\n", "students")
        self.assertContains(resp, "surname is empty")
        self.assertFalse(Student.objects.filter(matric_number="CSC/2021/003").exists())

    def test_students_upload_can_terminate_and_keeps_cgpa(self):
        self.post_data(FULL_HEAD + "CSC/2021/001,CSC201,72,2024/2025,Harmattan\n", "results")
        self.post_data(STUDENT_HEAD.strip() + ",status\nCSC/2021/001,Ada Okafor,Okafor,ada@x.com,200,Terminated\n",
                       "students")
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, "TERMINATED")
        self.assertEqual(self.student.cgpa, Decimal("5.00"))

    def test_students_upload_rejects_email_used_by_another_student(self):
        resp = self.post_data(STUDENT_HEAD + "CSC/2021/003,Chidi Eze,Eze,ada@x.com,300\n", "students")
        self.assertContains(resp, "already used")
        self.assertEqual(Student.objects.count(), 2)

    def test_students_upload_rejects_bad_level_and_email(self):
        resp = self.post_data(STUDENT_HEAD + "CSC/2021/003,Chidi Eze,Eze,not-an-email,350\n", "students")
        self.assertContains(resp, "valid email")
        self.assertContains(resp, "level")

    def test_guardians_upload_does_not_duplicate(self):
        text = "matric_number,guardian_email\nCSC/2021/001,mum@x.com\n"
        self.post_data(text, "guardians")
        self.post_data(text, "guardians")
        self.assertEqual(Guardian.objects.count(), 1)

    def test_guardians_upload_unknown_student(self):
        resp = self.post_data("matric_number,guardian_email\nNOPE/1,mum@x.com\n", "guardians")
        self.assertEqual(Guardian.objects.count(), 0)
        self.assertContains(resp, "unknown matric number")

    def test_files_upload_and_bad_link(self):
        good = "matric_number,file_type,cloud_url\nCSC/2021/001,transcript,https://example.com/a.pdf\n"
        self.post_data(good, "files")
        self.post_data(good, "files")
        self.assertEqual(StudentFile.objects.count(), 1)
        resp = self.post_data("matric_number,file_type,cloud_url\nCSC/2021/001,transcript,not-a-url\n", "files")
        self.assertContains(resp, "valid web link")

    def test_full_format_results_validation(self):
        resp = self.post_data(FULL_HEAD + "CSC/2021/001,CSC201,72,2024/2025,Summer\n", "results")
        self.assertContains(resp, "must be Harmattan or Rain")
        resp = self.post_data(FULL_HEAD + "CSC/2021/001,CSC201,50,2024/2025,Rain\n"
                              "CSC/2021/001,CSC201,60,2024/2025,Rain\n", "results")
        self.assertContains(resp, "duplicate")
        self.assertEqual(Result.objects.count(), 0)


# ======================================================================
#  Searching
# ======================================================================
class SearchTests(PortalTestCase):

    def setUp(self):
        super().setUp()
        self.mth = Course.objects.create(code="MTH201", title="Calculus", credit_units=2)
        Result.objects.create(student=self.student, course=self.csc201, score=72, session="2024/2025",
                              semester="HARMATTAN")
        Result.objects.create(student=self.student2, course=self.csc201, score=45, session="2024/2025",
                              semester="HARMATTAN")
        Result.objects.create(student=self.student2, course=self.mth, score=65, session="2025/2026",
                              semester="RAIN")
        self.login(self.lecturer)

    def test_empty_search_lists_everybody(self):
        resp = self.client.get(reverse("results_search"))
        self.assertContains(resp, "3 result(s) found")

    def test_search_by_matric_and_by_name(self):
        resp = self.client.get(reverse("results_search"), {"q": "CSC/2021/001"})
        self.assertContains(resp, "1 result(s) found")
        resp = self.client.get(reverse("results_search"), {"q": "bola"})
        self.assertContains(resp, "2 result(s) found")
        resp = self.client.get(reverse("results_search"), {"q": "okafor ada"})   # word order doesn't matter
        self.assertContains(resp, "1 result(s) found")

    def test_filters_for_course_session_and_semester(self):
        resp = self.client.get(reverse("results_search"), {"course": self.mth.pk})
        self.assertContains(resp, "1 result(s) found")
        resp = self.client.get(reverse("results_search"), {"session": "2024/2025"})
        self.assertContains(resp, "2 result(s) found")
        resp = self.client.get(reverse("results_search"), {"semester": "RAIN"})
        self.assertContains(resp, "1 result(s) found")

    def test_students_list_search(self):
        resp = self.client.get(reverse("students_list"), {"q": "ada okafor"})
        self.assertContains(resp, "1 student(s) found")
        resp = self.client.get(reverse("students_list"))
        self.assertContains(resp, "2 student(s) found")

    def test_student_detail_shows_results_gpa_and_cgpa(self):
        from .services import recalculate_gpa_and_cgpa
        recalculate_gpa_and_cgpa({self.student2.id})
        resp = self.client.get(reverse("student_detail", args=[self.student2.pk]))
        self.assertContains(resp, "MTH201")
        self.assertContains(resp, "Semester GPA")
        self.assertContains(resp, "2025/2026")