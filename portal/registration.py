from django.db import transaction

from .models import Course, CourseRegistration, PASS_MARK, Result


def passed_course_ids(student):
    """Ids of every course the student has passed (score at or above the pass mark)."""
    return set(Result.objects.filter(student=student, score__gte=PASS_MARK)
               .values_list("course_id", flat=True))


def outstanding_courses(student, calendar):
    """Courses the student has failed and not passed since: the ones still to be retaken."""
    passed = passed_course_ids(student)
    failed = (Result.objects.filter(student=student, score__lt=PASS_MARK)
              .exclude(course_id__in=passed).select_related("course")
              .order_by("course__code", "session"))
    latest = {}
    for r in failed:
        latest[r.course_id] = r             # sorted by session, so the last one is the latest attempt
    return [{
        "course": r.course,
        "score": r.score,
        "session": r.session,
        "offered_now": r.course.semester == calendar.current_semester,
    } for r in latest.values()]


def registered_courses(student, calendar):
    return list(CourseRegistration.objects
                .filter(student=student, session=calendar.current_session,
                        semester=calendar.current_semester)
                .select_related("course").order_by("course__code"))


def registration_options(student, calendar):
    """Work out what this student may register for in the current semester.
    eligible = can be ticked; locked = prerequisites not yet passed.
    A course shows up if it is offered this semester, at or below the student's level, and not
    already passed. Retakes of failed courses are included and marked."""
    passed = passed_course_ids(student)
    failed = set(Result.objects.filter(student=student, score__lt=PASS_MARK)
                 .values_list("course_id", flat=True)) - passed
    registered_ids = {r.course_id for r in registered_courses(student, calendar)}

    candidates = (Course.objects
                  .filter(semester=calendar.current_semester, level__lte=student.level)
                  .prefetch_related("prerequisites").order_by("level", "code"))
    eligible, locked = [], []
    for course in candidates:
        if course.id in registered_ids:                       # already chosen: always stays listed
            eligible.append({"course": course, "retake": course.id in failed, "registered": True})
            continue
        if course.id in passed:
            continue                                          # already passed: not offered again
        missing = [p.code for p in course.prerequisites.all() if p.id not in passed]
        if missing:
            locked.append({"course": course, "missing": missing})
        else:
            eligible.append({"course": course, "retake": course.id in failed, "registered": False})
    return {"eligible": eligible, "locked": locked}


def save_registration(student, calendar, selected_ids):
    """Make the student's registration for this semester exactly the selected courses.
    Returns (errors, warnings). Nothing is saved if there is any error."""
    if not calendar.registration_open:
        return ["Course registration is closed."], []

    eligible = {e["course"].id: e["course"] for e in registration_options(student, calendar)["eligible"]}
    chosen = set(selected_ids)
    errors, warnings = [], []

    if chosen - set(eligible):
        errors.append("One or more of the selected courses are not available to you this semester.")
    total = sum(eligible[c].credit_units for c in chosen if c in eligible)
    if calendar.max_units and total > calendar.max_units:
        errors.append(f"You selected {total} units, but the maximum is {calendar.max_units}.")
    if errors:
        return errors, warnings

    with transaction.atomic():
        current = CourseRegistration.objects.filter(
            student=student, session=calendar.current_session, semester=calendar.current_semester)
        current.exclude(course_id__in=chosen).delete()                    # dropped courses
        already = set(current.values_list("course_id", flat=True))
        CourseRegistration.objects.bulk_create([                         # newly ticked courses
            CourseRegistration(student=student, course_id=cid, session=calendar.current_session,
                               semester=calendar.current_semester)
            for cid in chosen - already])

    if calendar.min_units and total < calendar.min_units:
        warnings.append(f"You have {total} units; the minimum is {calendar.min_units}.")
    return [], warnings
