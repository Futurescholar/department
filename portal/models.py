from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def grade_point_for(score):
    """Convert a score (0-100) to a grade point on a 5-point scale.
    Change the cut-offs to match your school's grading system."""
    score = Decimal(score)
    if score >= 70: return Decimal("5.00")   # A
    if score >= 60: return Decimal("4.00")   # B
    if score >= 50: return Decimal("3.00")   # C
    if score >= 45: return Decimal("2.00")   # D
    if score >= 40: return Decimal("1.00")   # E
    return Decimal("0.00")                   # F


PASS_MARK = Decimal("40")
# A score of 40 or more is a pass (anything below is an F). Change it together with
# grade_point_for() if your school's pass mark is different.


class Semester(models.TextChoices):
    """The two semesters of a session. Shared by courses, results, GPAs and registrations."""
    HARMATTAN = "HARMATTAN", "Harmattan"   # first semester
    RAIN = "RAIN", "Rain"                  # second semester


# ---------------- TABLE 1: Students ----------------
class Student(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        TERMINATED = "TERMINATED", "Terminated"

    LEVELS = [(l, str(l)) for l in (100, 200, 300, 400, 500)]     # the department has 100 to 500 level

    matric_number = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    level = models.PositiveSmallIntegerField(choices=LEVELS)
    cgpa = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)

    surname = models.CharField(max_length=100, blank=True, default="")
    # The student's surname. It is the student's first-login password.

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="student_profile",
    )
    # The student's login account (username = matric number). One account per student.

    must_change_password = models.BooleanField(default=True)
    # True until the student sets their own password at first login.

    def __str__(self):
        return f"{self.matric_number} - {self.name}"


# ---------------- TABLE 2: Courses ----------------
class Course(models.Model):
    code = models.CharField(max_length=15, unique=True)
    title = models.CharField(max_length=200)
    credit_units = models.PositiveSmallIntegerField()

    level = models.PositiveSmallIntegerField(choices=Student.LEVELS, default=100)
    # The level this course belongs to (100 to 500).

    semester = models.CharField(max_length=10, choices=Semester.choices, default=Semester.HARMATTAN)
    # The semester this course is offered in.

    prerequisites = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="required_for")
    # Courses a student must have PASSED before registering for this one.

    def __str__(self):
        return f"{self.code} - {self.title}"


# ---------------- TABLE 3: Results ----------------
class Result(models.Model):
    Semester = Semester      # the shared Harmattan/Rain choices defined at the top of this file

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="results")
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="results")
    score = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    grade_point = models.DecimalField(max_digits=3, decimal_places=2)
    session = models.CharField(max_length=9)                      # e.g. "2024/2025"
    semester = models.CharField(max_length=10, choices=Semester.choices)    # Harmattan or Rain

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "course", "session"],
                name="one_result_per_course_session",
            )
        ]

    def save(self, *args, **kwargs):
        self.grade_point = grade_point_for(self.score)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student.matric_number} {self.course.code} {self.session} {self.get_semester_display()}"


# ---------------- TABLE 4: Semester GPA ----------------
class SemesterGPA(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="semester_gpas")
    session = models.CharField(max_length=9)
    semester = models.CharField(max_length=10, choices=Result.Semester.choices)
    total_units = models.PositiveSmallIntegerField()
    gpa = models.DecimalField(max_digits=3, decimal_places=2)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "session", "semester"],
                name="one_gpa_per_student_per_semester",
            )
        ]

    def __str__(self):
        return f"{self.student.matric_number} {self.session} {self.get_semester_display()}: {self.gpa}"


# ---------------- TABLE 5: Guardians ----------------
class Guardian(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="guardians")
    email = models.EmailField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "email"],
                name="one_guardian_email_per_student",
            )
            # The same guardian email can't be added twice for the same student,
            # so uploading the same file again never creates duplicates.
        ]

    def __str__(self):
        return f"{self.email} (guardian of {self.student.matric_number})"


# ---------------- TABLE 6: Files ----------------
class StudentFile(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="files")
    file_type = models.CharField(max_length=50)      # e.g. "transcript", "warning_letter"
    cloud_url = models.URLField(max_length=500)      # link to the file in cloud storage

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "file_type", "cloud_url"],
                name="one_file_link_per_student_and_type",
            )
            # The same link can't be saved twice for the same student and file type.
        ]

    def __str__(self):
        return f"{self.file_type} for {self.student.matric_number}"


# ---------------- TABLE 7: Course registrations ----------------
class CourseRegistration(models.Model):
    """A student's chosen courses for one session and semester."""
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="registrations")
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="registrations")
    session = models.CharField(max_length=9)
    semester = models.CharField(max_length=10, choices=Semester.choices)
    registered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "course", "session"],
                name="one_registration_per_course_session",
            )
        ]

    def __str__(self):
        return f"{self.student.matric_number} registered {self.course.code} ({self.session})"


# ---------------- TABLE 8: Academic calendar (one row of settings) ----------------
class AcademicCalendar(models.Model):
    """The HOD's settings: which session and semester it is now, whether course registration
    is open, unit limits and the GPA levels that trigger a warning. There is only ever one row."""
    current_session = models.CharField(max_length=9)                       # e.g. "2026/2027"
    current_semester = models.CharField(max_length=10, choices=Semester.choices,
                                        default=Semester.HARMATTAN)
    registration_open = models.BooleanField(default=False)
    min_units = models.PositiveSmallIntegerField(default=0)      # 0 = no minimum
    max_units = models.PositiveSmallIntegerField(default=0)      # 0 = no maximum
    warning_gpa = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("2.00"))
    watch_gpa = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("2.25"))
    # GPA or CGPA at or below warning_gpa shows a red warning; at or below watch_gpa an amber one.

    class Meta:
        verbose_name_plural = "academic calendar"

    @classmethod
    def get_solo(cls):
        """Return the single settings row, creating a sensible first guess if none exists yet."""
        calendar = cls.objects.first()
        if calendar is None:
            today = date.today()
            start = today.year if today.month >= 9 else today.year - 1
            first_half = today.month >= 9 or today.month <= 2
            calendar = cls.objects.create(
                current_session=f"{start}/{start + 1}",
                current_semester=Semester.HARMATTAN if first_half else Semester.RAIN,
            )
        return calendar

    def __str__(self):
        return f"{self.current_session} {self.get_current_semester_display()} semester"
