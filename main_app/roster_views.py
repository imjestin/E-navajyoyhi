"""Student and staff rosters: server-side search, filtering, sorting,
pagination, CSV export and CSV import.

The old Manage pages rendered every row and hid non-matches with JavaScript,
which stops being usable somewhere around a few hundred students. Everything
here filters in the database and returns one page at a time.

Access is granted to administrators only, via ROLE_MODULES in middleware.py.
"""

import csv

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import CharField, Q, Value
from django.db.models.functions import Concat
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from . import imports
from .models import Course, CustomUser, Department, Session, Staff, Student

PAGE_SIZES = [25, 50, 100]

STUDENT_SORTS = {
    "name": "admin__first_name",
    "register": "register_num",
    "email": "admin__email",
    "course": "course__name",
    "department": "department__name",
    "session": "session__start_year",
}
STAFF_SORTS = {
    "name": "admin__first_name",
    "email": "admin__email",
    "department": "department__name",
    "qualification": "qualification",
}


def _paginate(request, queryset):
    try:
        per_page = int(request.GET.get("per_page", 25))
    except ValueError:
        per_page = 25
    if per_page not in PAGE_SIZES:
        per_page = 25
    paginator = Paginator(queryset, per_page)
    page_obj = paginator.get_page(request.GET.get("page"))
    # Everything except `page`, so pagination links keep the active filters.
    params = request.GET.copy()
    params.pop("page", None)
    return page_obj, per_page, params.urlencode()


def _sorted(queryset, request, allowed, default):
    sort = request.GET.get("sort", default)
    direction = request.GET.get("dir", "asc")
    field = allowed.get(sort, allowed[default])
    if direction == "desc":
        field = "-" + field
    return queryset.order_by(field), sort, direction


def _student_queryset(request):
    qs = (Student.objects
          .select_related("admin", "course", "department", "session")
          .annotate(full_name=Concat("admin__first_name", Value(" "), "admin__last_name",
                                     output_field=CharField())))

    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(full_name__icontains=q) |
            Q(admin__email__icontains=q) |
            Q(register_num__icontains=q) |
            Q(admission_num__icontains=q) |
            Q(phone_num__icontains=q))

    for param, field in (("department", "department_id"),
                         ("course", "course_id"),
                         ("session", "session_id")):
        value = request.GET.get(param)
        if value:
            qs = qs.filter(**{field: value})
    return qs, q


def manage_student(request):
    qs, q = _student_queryset(request)
    total = qs.count()
    qs, sort, direction = _sorted(qs, request, STUDENT_SORTS, "name")
    page_obj, per_page, querystring = _paginate(request, qs)

    return render(request, "hod_template/manage_student.html", {
        "page_title": "Students",
        "page_obj": page_obj,
        "total": total,
        "q": q,
        "sort": sort,
        "dir": direction,
        "per_page": per_page,
        "page_sizes": PAGE_SIZES,
        "querystring": querystring,
        "departments": Department.objects.order_by("name"),
        "courses": Course.objects.order_by("name"),
        "sessions": Session.objects.order_by("-start_year"),
        "selected": {
            "department": request.GET.get("department", ""),
            "course": request.GET.get("course", ""),
            "session": request.GET.get("session", ""),
        },
    })


def _staff_queryset(request):
    qs = (Staff.objects
          .select_related("admin", "department", "course")
          .annotate(full_name=Concat("admin__first_name", Value(" "), "admin__last_name",
                                     output_field=CharField())))
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(full_name__icontains=q) |
            Q(admin__email__icontains=q) |
            Q(qualification__icontains=q) |
            Q(phone_num__icontains=q))
    department = request.GET.get("department")
    if department:
        qs = qs.filter(department_id=department)
    return qs, q


def manage_staff(request):
    qs, q = _staff_queryset(request)
    total = qs.count()
    qs, sort, direction = _sorted(qs, request, STAFF_SORTS, "name")
    page_obj, per_page, querystring = _paginate(request, qs)

    return render(request, "hod_template/manage_staff.html", {
        "page_title": "Staff",
        "page_obj": page_obj,
        "total": total,
        "q": q,
        "sort": sort,
        "dir": direction,
        "per_page": per_page,
        "page_sizes": PAGE_SIZES,
        "querystring": querystring,
        "departments": Department.objects.order_by("name"),
        "selected": {"department": request.GET.get("department", "")},
    })


# --- CSV export -----------------------------------------------------------

def _csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


def export_students(request):
    """Exports exactly what the current filters select, not the whole table."""
    qs, _ = _student_queryset(request)
    qs = qs.order_by("admin__first_name")
    rows = ([s.admin.first_name, s.admin.last_name, s.admin.email, s.admin.gender,
             s.phone_num or "", s.aadhar_num or "",
             s.date_of_birth.isoformat() if s.date_of_birth else "",
             s.admin.address, s.religion or "", s.register_num or "", s.admission_num or "",
             s.department.name if s.department else "",
             s.course.name if s.course else "",
             s.session.start_year.year if s.session else ""]
            for s in qs)
    header = [c for c in imports.STUDENT_COLUMNS if c != "password"]
    return _csv_response("students.csv", header, rows)


def export_staff(request):
    qs, _ = _staff_queryset(request)
    qs = qs.order_by("admin__first_name")
    rows = ([s.admin.first_name, s.admin.last_name, s.admin.email, s.admin.gender,
             s.phone_num or "", s.aadhar_num or "", s.admin.address,
             s.qualification or "", s.department.name if s.department else ""]
            for s in qs)
    header = [c for c in imports.STAFF_COLUMNS if c != "password"]
    return _csv_response("staff.csv", header, rows)


def sample_student_csv(request):
    return _csv_response("students_template.csv", imports.STUDENT_COLUMNS,
                         imports.SAMPLE_STUDENT_ROWS)


def sample_staff_csv(request):
    return _csv_response("staff_template.csv", imports.STAFF_COLUMNS,
                         imports.SAMPLE_STAFF_ROWS)


# --- CSV import -----------------------------------------------------------

def _import_view(request, *, kind, columns, validate, commit,
                 sample_url, redirect_url, title):
    """Shared upload -> validate -> preview -> commit flow."""
    context = {
        "page_title": title,
        "kind": kind,
        "columns": columns,
        "sample_url": sample_url,
        "required": (imports.STUDENT_REQUIRED if kind == "students"
                     else imports.STAFF_REQUIRED),
    }

    if request.method != "POST":
        return render(request, "hod_template/import_data.html", context)

    upload = request.FILES.get("csv_file")
    if not upload:
        messages.error(request, "Choose a CSV file to upload.")
        return render(request, "hod_template/import_data.html", context)

    rows, error = imports.read_csv(upload, columns)
    if error:
        messages.error(request, error)
        return render(request, "hod_template/import_data.html", context)

    results = validate(rows)
    invalid = [r for r in results if not r.ok]

    # Two-step: the first POST previews, a second POST with confirm=1 writes.
    if request.POST.get("confirm") == "1" and not invalid:
        created = commit(results)
        messages.success(request, f"Imported {len(created)} record(s).")
        context.update({"created": created, "done": True})
        return render(request, "hod_template/import_data.html", context)

    if invalid:
        messages.error(
            request,
            f"{len(invalid)} of {len(results)} row(s) have problems. "
            "Nothing was imported - fix the file and upload it again.")
    else:
        messages.info(request,
                      f"{len(results)} row(s) look good. Review them and confirm to import.")

    context.update({
        "results": results,
        "invalid_count": len(invalid),
        "valid_count": len(results) - len(invalid),
        "can_commit": not invalid,
        # Re-post the parsed rows on confirm rather than asking for the file twice.
        "payload": _encode_rows(rows),
    })
    return render(request, "hod_template/import_data.html", context)


def _encode_rows(rows):
    """Serialise parsed rows so the confirm step does not need a re-upload."""
    import base64
    import json
    return base64.b64encode(json.dumps(rows).encode()).decode()


def _decode_rows(payload):
    import base64
    import json
    return [(int(n), d) for n, d in json.loads(base64.b64decode(payload).decode())]


def import_students(request):
    if request.method == "POST" and request.POST.get("confirm") == "1":
        return _confirm(request, "students")
    return _import_view(
        request, kind="students", columns=imports.STUDENT_COLUMNS,
        validate=imports.validate_students, commit=imports.commit_students,
        sample_url=reverse("sample_student_csv"), redirect_url=reverse("manage_student"),
        title="Import Students")


def import_staff(request):
    if request.method == "POST" and request.POST.get("confirm") == "1":
        return _confirm(request, "staff")
    return _import_view(
        request, kind="staff", columns=imports.STAFF_COLUMNS,
        validate=imports.validate_staff, commit=imports.commit_staff,
        sample_url=reverse("sample_staff_csv"), redirect_url=reverse("manage_staff"),
        title="Import Staff")


def _confirm(request, kind):
    """Second step: re-validate the carried rows, then write them."""
    is_students = kind == "students"
    context = {
        "page_title": "Import Students" if is_students else "Import Staff",
        "kind": kind,
        "columns": imports.STUDENT_COLUMNS if is_students else imports.STAFF_COLUMNS,
        "sample_url": reverse("sample_student_csv" if is_students else "sample_staff_csv"),
        "required": imports.STUDENT_REQUIRED if is_students else imports.STAFF_REQUIRED,
    }
    payload = request.POST.get("payload", "")
    try:
        rows = _decode_rows(payload)
    except Exception:
        messages.error(request, "The upload expired. Please choose the file again.")
        return render(request, "hod_template/import_data.html", context)

    validate = imports.validate_students if is_students else imports.validate_staff
    commit = imports.commit_students if is_students else imports.commit_staff

    # Re-validate: the database may have changed between preview and confirm.
    results = validate(rows)
    invalid = [r for r in results if not r.ok]
    if invalid:
        messages.error(request,
                       f"{len(invalid)} row(s) are no longer valid. Nothing was imported.")
        context.update({"results": results, "invalid_count": len(invalid),
                        "valid_count": len(results) - len(invalid),
                        "can_commit": False, "payload": payload})
        return render(request, "hod_template/import_data.html", context)

    created = commit(results)
    messages.success(request, f"Imported {len(created)} record(s) successfully.")
    context.update({"created": created, "done": True})
    return render(request, "hod_template/import_data.html", context)
