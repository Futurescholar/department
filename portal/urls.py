from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.PortalLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("password/change/", views.PasswordChange.as_view(), name="password_change"),

    path("me/", views.my_record, name="my_record"),                       # students
    path("register/", views.register_courses, name="register_courses"),   # students

    path("upload/results/", views.upload_results, name="upload_results"),  # lecturers + HOD
    path("results/", views.results_search, name="results_search"),
    path("students/", views.students_list, name="students_list"),
    path("students/<int:pk>/", views.student_detail, name="student_detail"),

    path("upload/data/", views.upload_data, name="upload_data"),           # HOD only
    path("calendar/", views.calendar_settings, name="calendar_settings"),  # HOD only
    path("students/<int:pk>/reset-password/", views.student_reset_password,
         name="student_reset_password"),
]