from django.contrib import admin

from .models import (
    AcademicCalendar,
    Course,
    CourseRegistration,
    Guardian,
    Result,
    SemesterGPA,
    Student,
    StudentFile,
)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("matric_number", "name", "level", "status", "cgpa")
    search_fields = ("matric_number", "name", "surname", "email")
    list_filter = ("level", "status")


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("code", "title", "level", "semester", "credit_units")
    search_fields = ("code", "title")
    list_filter = ("level", "semester")
    filter_horizontal = ("prerequisites",)


@admin.register(Result)
class ResultAdmin(admin.ModelAdmin):
    list_display = ("student", "course", "session", "semester", "score", "grade_point")
    search_fields = ("student__matric_number", "student__name", "course__code")
    list_filter = ("session", "semester")


@admin.register(CourseRegistration)
class CourseRegistrationAdmin(admin.ModelAdmin):
    list_display = ("student", "course", "session", "semester", "registered_at")
    search_fields = ("student__matric_number", "course__code")
    list_filter = ("session", "semester")


admin.site.register(AcademicCalendar)
admin.site.register(SemesterGPA)
admin.site.register(Guardian)
admin.site.register(StudentFile)
