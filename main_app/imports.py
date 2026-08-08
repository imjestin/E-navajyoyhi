"""CSV import for students and staff.

Parsing and validation live here, apart from the views, so the rules can be
tested and reused. An import is always validated in full first and shown back
as a preview; nothing is written until the admin confirms, and the write runs
in a single transaction so a bad row can never leave a half-loaded batch.
"""

import csv
import io
import re
import secrets
import string
from datetime import datetime

from django.db import transaction

from .models import Course, CustomUser, Department, Session, Staff, Student

STUDENT_COLUMNS = [
    "first_name", "last_name", "email", "gender", "phone_num", "aadhar_num",
    "date_of_birth", "address", "religion", "register_num", "admission_num",
    "department", "course", "session_start_year", "password",
]
STUDENT_REQUIRED = [
    "first_name", "last_name", "email", "department", "course", "session_start_year",
]

STAFF_COLUMNS = [
    "first_name", "last_name", "email", "gender", "phone_num", "aadhar_num",
    "address", "qualification", "department", "password",
]
STAFF_REQUIRED = ["first_name", "last_name", "email", "department"]

SAMPLE_STUDENT_ROWS = [
    ["Aravind", "Menon", "aravind.menon@example.com", "M", "9846000001", "500000000001",
     "2005-04-12", "Cherupuzha, Kannur", "Hindu", "NJC2025101", "ADM20250101",
     "Computer Science", "BCA", "2025", ""],
    ["Nithya", "Krishnan", "nithya.krishnan@example.com", "F", "9846000002", "500000000002",
     "2005-08-30", "Payyanur, Kannur", "Hindu", "NJC2025102", "ADM20250102",
     "Commerce", "B.Com Finance", "2025", ""],
]
SAMPLE_STAFF_ROWS = [
    ["Anil", "Kurian", "anil.kurian@example.com", "M", "9847000001", "300000000001",
     "Taliparamba, Kannur", "M.Tech, Ph.D", "Computer Science", ""],
]


def generate_password(length=10):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class RowResult:
    """One parsed CSV row: either ready to create, or carrying errors."""

    def __init__(self, line_no, raw):
        self.line_no = line_no
        self.raw = raw
        self.errors = []
        self.data = {}
        self.generated_password = None

    @property
    def ok(self):
        return not self.errors

    @property
    def name(self):
        return f"{self.raw.get('first_name', '')} {self.raw.get('last_name', '')}".strip()

    @property
    def email(self):
        return (self.raw.get("email") or "").strip()


def _clean(value):
    return (value or "").strip()


def _parse_date(value, field, result):
    value = _clean(value)
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    result.errors.append(f"{field}: '{value}' is not a date (use YYYY-MM-DD)")
    return None


def _parse_digits(value, field, length, result):
    value = _clean(value).replace(" ", "").replace("-", "")
    if not value:
        return None
    if not value.isdigit():
        result.errors.append(f"{field}: '{value}' must contain digits only")
        return None
    if length and len(value) != length:
        result.errors.append(f"{field}: expected {length} digits, got {len(value)}")
        return None
    # Kept as text: these are identifiers, not quantities.
    return value


def _lookup(model, name, field, result, extra=None):
    name = _clean(name)
    if not name:
        return None
    qs = model.objects.filter(name__iexact=name)
    if extra:
        qs = qs.filter(**extra)
    obj = qs.first()
    if obj is None:
        result.errors.append(f"{field}: '{name}' does not exist - create it first")
    return obj


def read_csv(uploaded_file, expected_columns):
    """Decode an uploaded file into dict rows. Returns (rows, error)."""
    try:
        raw = uploaded_file.read()
    except Exception:
        return [], "Could not read the uploaded file."
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return [], "Could not decode the file. Save it as UTF-8 CSV and try again."

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], "The file appears to be empty."

    present = {(f or "").strip().lower() for f in reader.fieldnames}
    missing = [c for c in expected_columns if c not in present and c != "password"]
    if missing:
        return [], "Missing required column(s): " + ", ".join(missing)

    rows = []
    for i, row in enumerate(reader, start=2):  # line 1 is the header
        rows.append((i, {(k or "").strip().lower(): v for k, v in row.items()}))
    if not rows:
        return [], "The file has a header but no data rows."
    return rows, None


def validate_students(rows):
    results = []
    seen_emails = set()
    seen_register = set()
    for line_no, raw in rows:
        r = RowResult(line_no, raw)

        for field in STUDENT_REQUIRED:
            if not _clean(raw.get(field)):
                r.errors.append(f"{field} is required")

        email = _clean(raw.get("email")).lower()
        if email:
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
                r.errors.append(f"email: '{email}' is not a valid address")
            elif email in seen_emails:
                r.errors.append(f"email: '{email}' appears more than once in this file")
            elif CustomUser.objects.filter(email__iexact=email).exists():
                r.errors.append(f"email: '{email}' is already registered")
            else:
                seen_emails.add(email)

        register = _clean(raw.get("register_num"))
        if register:
            if register in seen_register:
                r.errors.append(f"register_num: '{register}' is duplicated in this file")
            elif Student.objects.filter(register_num=register).exists():
                r.errors.append(f"register_num: '{register}' already exists")
            else:
                seen_register.add(register)

        gender = _clean(raw.get("gender")).upper()[:1]
        if gender and gender not in ("M", "F"):
            r.errors.append(f"gender: '{raw.get('gender')}' must be M or F")

        department = _lookup(Department, raw.get("department"), "department", r)
        course = _lookup(Course, raw.get("course"), "course", r)
        if department and course and course.department_id and course.department_id != department.id:
            r.errors.append(
                f"course: '{course.name}' belongs to {course.department}, not {department}")

        session = None
        year = _clean(raw.get("session_start_year"))
        if year:
            if not year.isdigit():
                r.errors.append(f"session_start_year: '{year}' must be a year like 2025")
            else:
                session = Session.objects.filter(start_year__year=int(year)).first()
                if session is None:
                    r.errors.append(f"session_start_year: no session starting in {year}")

        r.data = {
            "first_name": _clean(raw.get("first_name")),
            "last_name": _clean(raw.get("last_name")),
            "email": email,
            "gender": gender or "M",
            "address": _clean(raw.get("address")),
            "religion": _clean(raw.get("religion")),
            "register_num": register,
            "admission_num": _clean(raw.get("admission_num")),
            "phone_num": _parse_digits(raw.get("phone_num"), "phone_num", 10, r),
            "aadhar_num": _parse_digits(raw.get("aadhar_num"), "aadhar_num", 12, r),
            "date_of_birth": _parse_date(raw.get("date_of_birth"), "date_of_birth", r),
            "department": department,
            "course": course,
            "session": session,
            "password": _clean(raw.get("password")),
        }
        results.append(r)
    return results


def validate_staff(rows):
    results = []
    seen_emails = set()
    for line_no, raw in rows:
        r = RowResult(line_no, raw)

        for field in STAFF_REQUIRED:
            if not _clean(raw.get(field)):
                r.errors.append(f"{field} is required")

        email = _clean(raw.get("email")).lower()
        if email:
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
                r.errors.append(f"email: '{email}' is not a valid address")
            elif email in seen_emails:
                r.errors.append(f"email: '{email}' appears more than once in this file")
            elif CustomUser.objects.filter(email__iexact=email).exists():
                r.errors.append(f"email: '{email}' is already registered")
            else:
                seen_emails.add(email)

        gender = _clean(raw.get("gender")).upper()[:1]
        if gender and gender not in ("M", "F"):
            r.errors.append(f"gender: '{raw.get('gender')}' must be M or F")

        r.data = {
            "first_name": _clean(raw.get("first_name")),
            "last_name": _clean(raw.get("last_name")),
            "email": email,
            "gender": gender or "M",
            "address": _clean(raw.get("address")),
            "qualification": _clean(raw.get("qualification")),
            "phone_num": _parse_digits(raw.get("phone_num"), "phone_num", 10, r),
            "aadhar_num": _parse_digits(raw.get("aadhar_num"), "aadhar_num", 12, r),
            "department": _lookup(Department, raw.get("department"), "department", r),
            "password": _clean(raw.get("password")),
        }
        results.append(r)
    return results


@transaction.atomic
def commit_students(results):
    """Create every validated row, or nothing at all."""
    created = []
    for r in results:
        d = r.data
        password = d["password"] or generate_password()
        user = CustomUser.objects.create_user(
            email=d["email"], password=password, user_type=3,
            first_name=d["first_name"], last_name=d["last_name"])
        user.gender = d["gender"]
        user.address = d["address"]
        user.save()

        student = Student.objects.get(admin=user)
        student.department = d["department"]
        student.course = d["course"]
        student.session = d["session"]
        student.religion = d["religion"]
        student.register_num = d["register_num"]
        student.admission_num = d["admission_num"]
        student.phone_num = d["phone_num"]
        student.aadhar_num = d["aadhar_num"]
        student.date_of_birth = d["date_of_birth"]
        student.save()

        r.generated_password = password if not d["password"] else None
        created.append(r)
    return created


@transaction.atomic
def commit_staff(results):
    created = []
    for r in results:
        d = r.data
        password = d["password"] or generate_password()
        user = CustomUser.objects.create_user(
            email=d["email"], password=password, user_type=2,
            first_name=d["first_name"], last_name=d["last_name"])
        user.gender = d["gender"]
        user.address = d["address"]
        user.save()

        staff = Staff.objects.get(admin=user)
        staff.department = d["department"]
        staff.qualification = d["qualification"]
        staff.phone_num = d["phone_num"]
        staff.aadhar_num = d["aadhar_num"]
        staff.save()

        r.generated_password = password if not d["password"] else None
        created.append(r)
    return created
