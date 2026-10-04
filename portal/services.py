import re
from collections import defaultdict
# defaultdict = a dictionary that creates a starting value the first time a new
# key is used. Handy for running totals.

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
# InvalidOperation: the error raised when text can't become a Decimal (e.g. "abc").
# ROUND_HALF_UP: normal school rounding (4.125 -> 4.13).

import pandas as pd
# pandas reads Excel/CSV into a "DataFrame" (a table in memory).

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
# Ready-made checkers for email addresses and web links.

from django.db import transaction
# A transaction = "all or nothing". Either every change is saved, or none.

from .models import Course, Guardian, Result, SemesterGPA, Student, StudentFile, grade_point_for
# Our own tables and the grade helper from models.py.


REQUIRED_COLUMNS = {"matric_number", "course_code", "score", "session", "semester"}
# Column headings the lecturer's file MUST have.

VALID_SEMESTERS = Result.Semester.values
# ["HARMATTAN", "RAIN"], taken straight from models.py so there is one source of truth.


def read_upload(uploaded_file):
    """Turn an uploaded CSV/Excel file into a pandas table."""
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        df = pd.read_csv(uploaded_file, dtype=str)    # dtype=str keeps everything as text
    else:
        df = pd.read_excel(uploaded_file, dtype=str)  # (stops "007" turning into 7)

    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    # "Matric Number " -> "matric_number"

    return df.fillna("")
    # Empty cells arrive as NaN; replace them with empty text.


def _ratio(points, units):
    """points / units rounded to 2 decimals. Returns 0.00 when there are no units."""
    if not units:                                      # avoid dividing by zero
        return Decimal("0.00")
    return (points / units).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def recalculate_gpa_and_cgpa(student_ids):
    """For each student:
       - GPA per semester  = sum(grade point x units in that semester) / units in that semester
       - CGPA (so far)     = sum(grade point x units in ALL semesters) / units in ALL semesters
    Both are rebuilt from ALL of the student's results, so they are always current."""

    results = Result.objects.filter(student_id__in=student_ids).select_related("course")
    # Every result these students have (not just the ones just uploaded), because
    # CGPA must include every semester since admission. select_related fetches
    # each course in the same query so credit_units is available cheaply.

    per_semester = defaultdict(lambda: [Decimal("0"), 0])
    # Running totals per (student, session, semester): [points, units]

    per_student = defaultdict(lambda: [Decimal("0"), 0])
    # Running totals per student across all semesters: [points, units]

    for r in results:
        units = r.course.credit_units               # e.g. 3
        points = r.grade_point * units              # e.g. 5.00 x 3 = 15.00

        term = per_semester[(r.student_id, r.session, r.semester)]
        term[0] += points
        term[1] += units

        overall = per_student[r.student_id]
        overall[0] += points
        overall[1] += units

    for (student_id, session, semester), (points, units) in per_semester.items():
        SemesterGPA.objects.update_or_create(
            student_id=student_id, session=session, semester=semester,
            defaults={"total_units": units, "gpa": _ratio(points, units)},
        )
        # Update the semester's GPA row if it exists, otherwise create it.

    for student_id in student_ids:
        points, units = per_student.get(student_id, (Decimal("0"), 0))
        Student.objects.filter(id=student_id).update(cgpa=_ratio(points, units))
        # Save the up-to-date CGPA on the student.


def import_results(df):
    """Validate every row, then save. Returns (number_saved, list_of_errors)."""

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        return 0, [f"Missing column(s): {', '.join(sorted(missing))}"]

    students = {
        s.matric_number: s
        for s in Student.objects.filter(matric_number__in=df["matric_number"].str.strip())
    }
    # One query fetches every student in the file: {"CSC/2021/001": <Student>, ...}

    courses = {
        c.code: c
        for c in Course.objects.filter(code__in=df["course_code"].str.strip().str.upper())
    }
    # Same for courses, keyed by uppercase course code.

    errors = []       # every problem found
    objs = []         # Result rows ready to save
    seen = set()      # (matric, course, session) combinations already met in this file

    for i, row in df.iterrows():
        line = i + 2
        # The row number the lecturer sees in Excel (+1 for Excel counting from 1,
        # +1 because row 1 is the heading row).

        matric = row["matric_number"].strip()
        code = row["course_code"].strip().upper()
        session = row["session"].strip()
        semester = row["semester"].strip().upper()     # "Harmattan" -> "HARMATTAN"

        student = students.get(matric)
        course = courses.get(code)

        if not student:
            errors.append(f"Row {line}: unknown matric number '{matric}'")
        if not course:
            errors.append(f"Row {line}: unknown course code '{code}'")

        try:
            score = Decimal(row["score"].strip())
            if not (0 <= score <= 100):
                raise InvalidOperation
        except InvalidOperation:
            errors.append(f"Row {line}: score '{row['score']}' must be a number from 0 to 100")
            score = None

        if not session:
            errors.append(f"Row {line}: session is empty")

        if semester not in VALID_SEMESTERS:
            errors.append(f"Row {line}: semester '{row['semester']}' must be Harmattan or Rain")

        key = (matric, code, session)
        if key in seen:
            errors.append(f"Row {line}: duplicate of an earlier row in this file")
        seen.add(key)

        if student and course and score is not None and session and semester in VALID_SEMESTERS:
            objs.append(
                Result(
                    student=student,
                    course=course,
                    score=score,
                    grade_point=grade_point_for(score),
                    session=session,
                    semester=semester,
                )
            )

    if errors:
        return 0, errors
    # Any error at all? Save NOTHING and return every error so the whole file
    # can be fixed in one go.

    with transaction.atomic():
        Result.objects.bulk_create(
            objs,
            update_conflicts=True,
            unique_fields=["student", "course", "session"],
            update_fields=["score", "grade_point", "semester"],
        )
        # Saves all rows in one trip; existing student+course+session rows are updated.
        # bulk_create skips Result.save(), which is why grade_point is set above.

        recalculate_gpa_and_cgpa({o.student_id for o in objs})
        # Rebuild semester GPAs and CGPA for every student in this upload.

    return len(objs), []


# =====================================================================
#  OTHER UPLOADS: courses, students, guardians, files
#  Every importer follows the same rule as import_results:
#  check ALL rows first; if there is any error, save NOTHING.
# =====================================================================

def _missing_columns(df, required):
    """Return a one-item error list if headings are missing, otherwise an empty list."""
    missing = required - set(df.columns)
    return [f"Missing column(s): {', '.join(sorted(missing))}"] if missing else []


def _valid_email(value):
    try:
        validate_email(value)       # Django's own email checker
        return True
    except ValidationError:
        return False


def _valid_url(value):
    try:
        URLValidator(schemes=["http", "https"])(value)    # must start with http:// or https://
        return True
    except ValidationError:
        return False


def _split_codes(text):
    """'CSC101; MTH101' -> ['CSC101', 'MTH101'] (separate with ; or , or |)."""
    return [c.strip().upper() for c in re.split(r"[;,|]+", text) if c.strip()]


def _find_cycle(graph):
    """Look for a prerequisite loop such as A needs B, B needs A (nobody could ever register).
    graph = {course code: set of prerequisite codes}. Returns the loop as a list, or None."""
    state, path = {}, []              # state: 1 = being explored, 2 = finished

    def visit(node):
        state[node] = 1
        path.append(node)
        for nxt in sorted(graph.get(node, ())):
            if state.get(nxt) == 1:
                return path[path.index(nxt):] + [nxt]
            if state.get(nxt) is None:
                found = visit(nxt)
                if found:
                    return found
        state[node] = 2
        path.pop()
        return None

    for node in sorted(graph):
        if state.get(node) is None:
            found = visit(node)
            if found:
                return found
    return None


def import_courses(df):
    """Create new courses or update existing ones (matched on course code).
    Columns: code, title, credit_units, level, semester, and optionally prerequisites.
    If a 'prerequisites' column is present it replaces each listed course's prerequisites
    (blank = none). If the column is absent, existing prerequisites are left alone."""
    bad = _missing_columns(df, {"code", "title", "credit_units", "level", "semester"})
    if bad:
        return 0, bad

    has_prereq = "prerequisites" in df.columns
    valid_levels = {str(v) for v, _ in Student.LEVELS}
    levels_text = ", ".join(sorted(valid_levels))

    errors, objs, seen = [], [], set()
    wanted, lines = {}, {}              # code -> its prerequisite codes / its row number
    for i, row in df.iterrows():
        line = i + 2
        before = len(errors)            # remember how many errors we had before this row

        code = row["code"].strip().upper()
        title = row["title"].strip()
        units = row["credit_units"].strip().removesuffix(".0")
        level = row["level"].strip().removesuffix(".0")
        semester = row["semester"].strip().upper()

        if not code:
            errors.append(f"Row {line}: code is empty")
        elif len(code) > 15:
            errors.append(f"Row {line}: code '{code}' is longer than 15 characters")
        if not title:
            errors.append(f"Row {line}: title is empty")
        if not units.isdigit() or not (1 <= int(units) <= 12):
            errors.append(f"Row {line}: credit_units '{row['credit_units']}' must be a whole number from 1 to 12")
        if level not in valid_levels:
            errors.append(f"Row {line}: level '{row['level']}' must be one of {levels_text}")
        if semester not in VALID_SEMESTERS:
            errors.append(f"Row {line}: semester '{row['semester']}' must be Harmattan or Rain")
        if code in seen:
            errors.append(f"Row {line}: course code '{code}' appears twice in this file")
        seen.add(code)

        if has_prereq:
            prereqs = _split_codes(row["prerequisites"])
            if code in prereqs:
                errors.append(f"Row {line}: {code} cannot be its own prerequisite")
            wanted[code] = prereqs
            lines[code] = line

        if len(errors) == before:       # no new errors, so this row is good
            objs.append(Course(code=code, title=title, credit_units=int(units),
                               level=int(level), semester=semester))

    if has_prereq:
        known = set(Course.objects.values_list("code", flat=True)) | seen
        for code, prereqs in wanted.items():
            for p in prereqs:
                if p not in known:
                    errors.append(f"Row {lines[code]}: prerequisite '{p}' of {code} is not a known course "
                                  f"(add it to the courses file or upload it first)")
        graph = {c.code: {p.code for p in c.prerequisites.all()}
                 for c in Course.objects.prefetch_related("prerequisites")}
        graph.update({code: set(prereqs) for code, prereqs in wanted.items()})
        loop = _find_cycle(graph)
        if loop:
            errors.append("Prerequisite loop: " + " -> ".join(loop) +
                          " (students could never register for these courses)")

    if errors:
        return 0, errors

    with transaction.atomic():
        Course.objects.bulk_create(
            objs,
            update_conflicts=True,
            unique_fields=["code"],                    # existing code -> update it
            update_fields=["title", "credit_units", "level", "semester"],
        )
        if has_prereq:
            by_code = {c.code: c for c in Course.objects.all()}
            for code, prereqs in wanted.items():
                by_code[code].prerequisites.set([by_code[p] for p in prereqs])
        # If an existing course's credit units changed, GPAs and CGPAs that used it
        # are now out of date, so rebuild them for every student with a result in it.
        affected = set(Result.objects.filter(course__code__in=seen).values_list("student_id", flat=True))
        recalculate_gpa_and_cgpa(affected)
    return len(objs), []


def import_students(df):
    """Create new students or update existing ones (matched on matric number).
    CGPA is never read from the file; it is always calculated from results.
    Include an optional 'status' column (Active/Terminated) to change status."""
    bad = _missing_columns(df, {"matric_number", "name", "surname", "email", "level"})
    if bad:
        return 0, bad

    has_status = "status" in df.columns
    valid_levels = {str(v) for v, _ in Student.LEVELS}      # {"100", "200", ...}

    emails_in_file = df["email"].str.strip().str.lower().tolist()
    taken = dict(Student.objects.filter(email__in=emails_in_file).values_list("email", "matric_number"))
    # email -> matric number, for students ALREADY in the database using an email from this file.

    errors, objs = [], []
    seen_matrics, seen_emails = set(), set()

    for i, row in df.iterrows():
        line = i + 2
        before = len(errors)

        matric = row["matric_number"].strip()
        name = row["name"].strip()
        surname = row["surname"].strip()
        email = row["email"].strip().lower()
        level = row["level"].strip().removesuffix(".0")
        status = row["status"].strip().upper() if has_status else None

        if not matric:
            errors.append(f"Row {line}: matric_number is empty")
        elif len(matric) > 30:
            errors.append(f"Row {line}: matric_number '{matric}' is longer than 30 characters")
        if not name:
            errors.append(f"Row {line}: name is empty")
        if not surname:
            errors.append(f"Row {line}: surname is empty (it is the student's first-login password)")
        if not _valid_email(email):
            errors.append(f"Row {line}: '{row['email']}' is not a valid email address")
        if level not in valid_levels:
            errors.append(f"Row {line}: level '{row['level']}' must be 100, 200, 300, 400, 500 or 600")
        if has_status and status not in Student.Status.values:
            errors.append(f"Row {line}: status '{row['status']}' must be Active or Terminated")
        if matric in seen_matrics:
            errors.append(f"Row {line}: matric number '{matric}' appears twice in this file")
        if email in seen_emails:
            errors.append(f"Row {line}: email '{email}' appears twice in this file")
        if email in taken and taken[email] != matric:
            errors.append(f"Row {line}: email '{email}' is already used by another student")
        seen_matrics.add(matric)
        seen_emails.add(email)

        if len(errors) == before:
            fields = dict(matric_number=matric, name=name, surname=surname, email=email, level=int(level))
            if has_status:
                fields["status"] = status
            objs.append(Student(**fields))

    if errors:
        return 0, errors

    update_fields = ["name", "surname", "email", "level"] + (["status"] if has_status else [])
    # Only overwrite status for existing students if the file has a status column.
    # CGPA is deliberately never in this list.

    with transaction.atomic():
        Student.objects.bulk_create(
            objs,
            update_conflicts=True,
            unique_fields=["matric_number"],
            update_fields=update_fields,
        )
        ensure_student_accounts(seen_matrics)
        # Every student now gets a login: username = matric number, first password = surname.
    return len(objs), []


def import_guardians(df):
    """Add guardian emails to students. Re-uploading the same pair is ignored (no duplicates)."""
    bad = _missing_columns(df, {"matric_number", "guardian_email"})
    if bad:
        return 0, bad

    students = {
        s.matric_number: s
        for s in Student.objects.filter(matric_number__in=df["matric_number"].str.strip().tolist())
    }

    errors, objs = [], []
    for i, row in df.iterrows():
        line = i + 2
        before = len(errors)

        matric = row["matric_number"].strip()
        email = row["guardian_email"].strip().lower()

        student = students.get(matric)
        if not student:
            errors.append(f"Row {line}: unknown matric number '{matric}'")
        if not _valid_email(email):
            errors.append(f"Row {line}: '{row['guardian_email']}' is not a valid email address")

        if len(errors) == before:
            objs.append(Guardian(student=student, email=email))

    if errors:
        return 0, errors

    with transaction.atomic():
        count_before = Guardian.objects.count()
        Guardian.objects.bulk_create(objs, ignore_conflicts=True)
        # ignore_conflicts: a student + email pair that already exists is silently skipped.
        added = Guardian.objects.count() - count_before
    return added, []      # number of NEW guardians actually added


def import_files(df):
    """Add cloud-storage links for students. Re-uploading the same link is ignored."""
    bad = _missing_columns(df, {"matric_number", "file_type", "cloud_url"})
    if bad:
        return 0, bad

    students = {
        s.matric_number: s
        for s in Student.objects.filter(matric_number__in=df["matric_number"].str.strip().tolist())
    }

    errors, objs = [], []
    for i, row in df.iterrows():
        line = i + 2
        before = len(errors)

        matric = row["matric_number"].strip()
        file_type = row["file_type"].strip()
        url = row["cloud_url"].strip()

        student = students.get(matric)
        if not student:
            errors.append(f"Row {line}: unknown matric number '{matric}'")
        if not file_type:
            errors.append(f"Row {line}: file_type is empty")
        elif len(file_type) > 50:
            errors.append(f"Row {line}: file_type is longer than 50 characters")
        if not _valid_url(url) or len(url) > 500:
            errors.append(f"Row {line}: cloud_url must be a valid web link starting with http:// or https://")

        if len(errors) == before:
            objs.append(StudentFile(student=student, file_type=file_type, cloud_url=url))

    if errors:
        return 0, errors

    with transaction.atomic():
        count_before = StudentFile.objects.count()
        StudentFile.objects.bulk_create(objs, ignore_conflicts=True)
        added = StudentFile.objects.count() - count_before
    return added, []


# =====================================================================
#  STUDENT LOGIN ACCOUNTS
# =====================================================================

def ensure_student_accounts(matric_numbers):
    """Give every listed student a login account if they don't have one yet.
    Username = matric number. The account starts with NO password; the student's
    first login uses their surname (see auth_backends.py) and then they must
    choose their own password. Creating accounts this way is instant, whereas
    hashing 1,000 passwords up front would take minutes."""
    User = get_user_model()
    students = list(Student.objects.filter(matric_number__in=list(matric_numbers), user__isnull=True))
    if not students:
        return 0

    usernames = [s.matric_number for s in students]
    already = set(User.objects.filter(username__in=usernames).values_list("username", flat=True))
    User.objects.bulk_create([
        User(username=s.matric_number, email=s.email, password=make_password(None))
        # make_password(None) = "no usable password"
        for s in students if s.matric_number not in already
    ])

    users = {u.username: u for u in User.objects.filter(username__in=usernames)}
    for s in students:
        s.user = users[s.matric_number]
    Student.objects.bulk_update(students, ["user"])
    return len(students)


# =====================================================================
#  RESULTS UPLOAD WITH DROPDOWNS (course + session + semester chosen on the page)
#  The file only needs: matric number and/or name, plus the score.
# =====================================================================

COLUMN_ALIASES = {
    "matric_number": {"matric_number", "matric_no", "matric", "matricno", "matric_num", "reg_no", "regno"},
    "name": {"name", "student_name", "full_name", "fullname", "names"},
    "score": {"score", "total", "mark", "marks", "total_score"},
}


def _apply_aliases(df):
    """Accept common heading variants: 'Matric No' -> matric_number, 'Total' -> score, ..."""
    rename, taken = {}, set(df.columns)
    for col in df.columns:
        for canonical, names in COLUMN_ALIASES.items():
            if col in names and canonical not in taken:
                rename[col] = canonical
                taken.add(canonical)
    return df.rename(columns=rename)


def _name_tokens(text):
    """'Okafor, Ada' and 'ada okafor' both become {'ada', 'okafor'}, so word order and
    capital letters don't matter."""
    return frozenset(re.findall(r"[a-z0-9]+", text.lower()))


def import_course_results(df, course, session, semester):
    """Save one course's scores for one session and semester.
    Each row is matched to a student by matric number, or by name when there is no
    matric number. A name is only accepted if it matches exactly ONE student."""
    df = _apply_aliases(df)
    if "score" not in df.columns:
        return 0, ["Missing column: score"]
    has_matric = "matric_number" in df.columns
    has_name = "name" in df.columns
    if not has_matric and not has_name:
        return 0, ["The file needs a 'matric_number' column, a 'name' column, or both."]

    by_matric = {}
    if has_matric:
        wanted = [m.strip() for m in df["matric_number"] if m.strip()]
        by_matric = {s.matric_number: s for s in Student.objects.filter(matric_number__in=wanted)}

    name_index = {}

    def students_named(tokens):
        if not name_index:                                  # built once, only if needed
            for s in Student.objects.all():
                name_index.setdefault(_name_tokens(s.name), []).append(s)
        return name_index.get(tokens, [])

    errors, objs, seen = [], [], set()
    for i, row in df.iterrows():
        line = i + 2
        before = len(errors)

        matric = row["matric_number"].strip() if has_matric else ""
        name = row["name"].strip() if has_name else ""
        raw_score = row["score"].strip()
        if not (matric or name or raw_score):
            continue                                        # completely empty row: skip

        student = None
        name_tokens = _name_tokens(name)
        if matric:
            student = by_matric.get(matric)
            if not student:
                errors.append(f"Row {line}: unknown matric number '{matric}'")
            elif name_tokens and not name_tokens <= _name_tokens(student.name):
                errors.append(
                    f"Row {line}: name '{name}' does not match {student.matric_number} ({student.name})")
                student = None
        elif name:
            matches = students_named(name_tokens)
            if len(matches) == 1:
                student = matches[0]
            elif not matches:
                errors.append(f"Row {line}: no student named '{name}'. Check the spelling or use the matric number")
            else:
                numbers = ", ".join(m.matric_number for m in matches)
                errors.append(f"Row {line}: '{name}' matches {len(matches)} students ({numbers}). Use the matric number")
        else:
            errors.append(f"Row {line}: needs a matric number or a name")

        try:
            score = Decimal(raw_score)
            if not (0 <= score <= 100):
                raise InvalidOperation
        except InvalidOperation:
            errors.append(f"Row {line}: score '{row['score']}' must be a number from 0 to 100")
            score = None

        if student:
            if student.id in seen:
                errors.append(f"Row {line}: {student.matric_number} appears more than once in this file")
            seen.add(student.id)

        if len(errors) == before:
            objs.append(Result(student=student, course=course, score=score,
                               grade_point=grade_point_for(score), session=session, semester=semester))

    if not errors and not objs:
        errors.append("The file has no rows to import.")
    if errors:
        return 0, errors

    with transaction.atomic():
        Result.objects.bulk_create(
            objs,
            update_conflicts=True,
            unique_fields=["student", "course", "session"],
            update_fields=["score", "grade_point", "semester"],
        )
        recalculate_gpa_and_cgpa({o.student_id for o in objs})
    return len(objs), []