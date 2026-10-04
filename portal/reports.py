from itertools import groupby

from .models import AcademicCalendar, Result
from .registration import outstanding_courses, registered_courses


def student_report(student):
    """Everything a result page needs: results grouped by session + semester, each
    with its stored GPA, plus the CGPA."""
    results = list(student.results.select_related("course").order_by("session", "semester", "course__code"))
    gpas = {(g.session, g.semester): g for g in student.semester_gpas.all()}
    labels = dict(Result.Semester.choices)

    terms = []
    for (session, semester), rows in groupby(results, key=lambda r: (r.session, r.semester)):
        rows = list(rows)
        g = gpas.get((session, semester))
        terms.append({
            "session": session,
            "semester": labels.get(semester, semester),
            "results": rows,
            "gpa": g.gpa if g else None,
            "units": g.total_units if g else sum(r.course.credit_units for r in rows),
        })
    return {
        "terms": terms,
        "cgpa": student.cgpa,
        "total_units": sum(t["units"] for t in terms),
    }


def academic_standing(report, calendar):
    """Compare the latest semester GPA and the CGPA with the HOD's warning levels.
    level is 'warning' (at or below warning_gpa), 'watch' (at or below watch_gpa) or None."""
    metrics = []
    if report["terms"]:
        last = report["terms"][-1]
        if last["gpa"] is not None:
            metrics.append((f"The GPA for {last['session']} {last['semester']} semester", last["gpa"]))
        metrics.append(("The CGPA", report["cgpa"]))

    level, messages = None, []
    for label, value in metrics:
        if value <= calendar.warning_gpa:
            level = "warning"
            messages.append(f"{label} is {value}, at or below the warning level of {calendar.warning_gpa}.")
        elif value <= calendar.watch_gpa:
            level = level or "watch"
            messages.append(f"{label} is {value}, close to the warning level of {calendar.warning_gpa}.")
    return {"level": level, "messages": messages}


def student_overview(student):
    """Everything the student's page needs: results, standing, registered and outstanding courses."""
    calendar = AcademicCalendar.get_solo()
    report = student_report(student)
    registered = registered_courses(student, calendar)
    return {
        "calendar": calendar,
        "report": report,
        "standing": academic_standing(report, calendar),
        "registered": registered,
        "registered_units": sum(r.course.credit_units for r in registered),
        "outstanding": outstanding_courses(student, calendar),
    }
