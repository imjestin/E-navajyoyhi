"""Staff-facing views.

Rewritten to match the administrator side: attendance is taken and corrected
through ordinary form posts rather than a chain of AJAX calls, dashboards are
built from database aggregates instead of per-row queries, and the attendance
report actually reports per-student figures.
"""

import json
from datetime import date as date_cls
from datetime import datetime

from django.contrib import messages
from django.core.files.storage import FileSystemStorage
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .forms import *
from .models import *


def _percent(present, total):
    return round(present / total * 100) if total else None


def _threshold():
    return SiteSettings.load().attendance_threshold


def _my_subjects(staff):
    return (Subject.objects.filter(staff=staff)
            .select_related("course", "session", "department")
            .order_by("session__start_year", "name"))


# --------------------------------------------------------------------- home

def staff_home(request):
    staff = get_object_or_404(Staff, admin=request.user)
    threshold = _threshold()
    subjects = list(_my_subjects(staff))

    classes_per_subject = {
        row["subject_id"]: row["n"] for row in
        Attendance.objects.filter(subject__staff=staff)
        .values("subject_id").annotate(n=Count("id"))
    }
    marked = {
        row["attendance__subject_id"]: (row["total"], row["present"]) for row in
        AttendanceReport.objects.filter(attendance__subject__staff=staff)
        .values("attendance__subject_id")
        .annotate(total=Count("id"), present=Count("id", filter=Q(status=True)))
    }
    enrolment = {
        (row["course_id"], row["session_id"]): row["n"] for row in
        Student.objects.filter(course__in=[s.course_id for s in subjects])
        .values("course_id", "session_id").annotate(n=Count("id"))
    }

    subject_rows, total_marked, total_present = [], 0, 0
    for subject in subjects:
        total, present = marked.get(subject.id, (0, 0))
        total_marked += total
        total_present += present
        subject_rows.append({
            "subject": subject,
            "classes": classes_per_subject.get(subject.id, 0),
            "students": enrolment.get((subject.course_id, subject.session_id), 0),
            "percent": _percent(present, total),
        })

    # Students of mine who are short of the requirement.
    per_student = (AttendanceReport.objects
                   .filter(attendance__subject__staff=staff)
                   .values("student_id")
                   .annotate(total=Count("id"), present=Count("id", filter=Q(status=True))))
    ranked = []
    for row in per_student:
        pct = _percent(row["present"], row["total"])
        if pct is not None and pct < threshold:
            ranked.append((pct, row["student_id"], row["present"], row["total"]))
    ranked.sort(key=lambda r: r[0])
    short_students = {
        s.id: s for s in Student.objects.filter(id__in=[r[1] for r in ranked[:8]])
        .select_related("admin", "course")
    }
    defaulters = [{"student": short_students[sid], "percent": pct,
                   "present": present, "total": total}
                  for pct, sid, present, total in ranked[:8] if sid in short_students]

    pairs = {(s.course_id, s.session_id) for s in subjects}
    student_filter = Q()
    for course_id, session_id in pairs:
        student_filter |= Q(course_id=course_id, session_id=session_id)
    distinct_students = Student.objects.filter(student_filter).distinct().count() if pairs else 0

    context = {
        'page_title': f"{staff.admin.first_name}'s dashboard",
        'staff': staff,
        'subject_rows': subject_rows,
        'total_subject': len(subject_rows),
        'total_students': distinct_students,
        'total_classes': sum(r["classes"] for r in subject_rows),
        'overall_percent': _percent(total_present, total_marked),
        'attendance_ok': _percent(total_present, total_marked) is not None
                         and _percent(total_present, total_marked) >= threshold,
        'threshold': threshold,
        'defaulters': defaulters,
        'defaulter_count': len(ranked),
        'total_leave': LeaveReportStaff.objects.filter(staff=staff).count(),
        'pending_leave': LeaveReportStaff.objects.filter(staff=staff, status=0).count(),
        'unread_notifications': NotificationStaff.objects.filter(staff=staff).count(),
        'recent_classes': (Attendance.objects.filter(subject__staff=staff)
                           .select_related("subject", "subject__course")
                           .order_by("-date", "-id")[:8]),
        'chart_labels': [r["subject"].name for r in subject_rows if r["percent"] is not None],
        'chart_values': [r["percent"] for r in subject_rows if r["percent"] is not None],
    }
    return render(request, 'staff_template/home_content.html', context)


# -------------------------------------------------------------- attendance

def _roster(subject):
    return (Student.objects
            .filter(course=subject.course, session=subject.session)
            .select_related("admin")
            .order_by("admin__first_name", "admin__last_name"))


def staff_take_attendance(request):
    """Pick a subject and date, tick the register, save. One page, no AJAX."""
    staff = get_object_or_404(Staff, admin=request.user)
    subjects = list(_my_subjects(staff))

    subject_id = request.POST.get("subject") or request.GET.get("subject") or ""
    date_str = request.POST.get("date") or request.GET.get("date") or ""
    subject = next((s for s in subjects if str(s.id) == str(subject_id)), None)

    students, existing = [], None
    if subject and date_str:
        students = list(_roster(subject))
        existing = Attendance.objects.filter(subject=subject, date=date_str).first()

    if request.method == "POST" and request.POST.get("save") == "1":
        if not (subject and date_str):
            messages.error(request, "Choose a subject and a date first.")
        elif not students:
            messages.error(request, "No students are enrolled for this course and batch.")
        else:
            present_ids = set(request.POST.getlist("present"))
            attendance = existing or Attendance(subject=subject, session=subject.session,
                                                date=date_str)
            attendance.session = subject.session
            attendance.save()
            for student in students:
                AttendanceReport.objects.update_or_create(
                    student=student, attendance=attendance,
                    defaults={"status": str(student.id) in present_ids})
            messages.success(
                request,
                f"Attendance saved for {subject.name} on {date_str} "
                f"({len(present_ids)} of {len(students)} present).")
            return redirect(f"{reverse('staff_take_attendance')}?subject={subject.id}&date={date_str}")

    marks = {}
    if existing:
        marks = {r.student_id: r.status for r in
                 AttendanceReport.objects.filter(attendance=existing)}

    return render(request, 'staff_template/staff_take_attendance.html', {
        'page_title': 'Take Attendance',
        'subjects': subjects,
        'selected_subject': str(subject_id),
        'subject': subject,
        'date': date_str,
        'today': date_cls.today().isoformat(),
        'students': students,
        'existing': existing,
        'marks': marks,
        'rows': [{"student": s, "present": marks.get(s.id, True)} for s in students],
    })


def staff_update_attendance(request):
    """Correct a class that has already been recorded."""
    staff = get_object_or_404(Staff, admin=request.user)
    subjects = list(_my_subjects(staff))

    subject_id = request.POST.get("subject") or request.GET.get("subject") or ""
    attendance_id = request.POST.get("attendance") or request.GET.get("attendance") or ""
    subject = next((s for s in subjects if str(s.id) == str(subject_id)), None)

    classes = (Attendance.objects.filter(subject=subject).order_by("-date")
               if subject else Attendance.objects.none())
    attendance = classes.filter(id=attendance_id).first() if attendance_id else None

    rows = []
    if attendance:
        reports = (AttendanceReport.objects.filter(attendance=attendance)
                   .select_related("student__admin")
                   .order_by("student__admin__first_name"))
        rows = [{"student": r.student, "present": r.status} for r in reports]

    if request.method == "POST" and request.POST.get("save") == "1":
        if not attendance:
            messages.error(request, "Choose a class to correct first.")
        else:
            present_ids = set(request.POST.getlist("present"))
            for row in rows:
                AttendanceReport.objects.filter(
                    attendance=attendance, student=row["student"]
                ).update(status=str(row["student"].id) in present_ids)
            messages.success(request, f"Attendance updated for {attendance.date}.")
            return redirect(f"{reverse('staff_update_attendance')}"
                            f"?subject={subject.id}&attendance={attendance.id}")

    return render(request, 'staff_template/staff_update_attendance.html', {
        'page_title': 'Update Attendance',
        'subjects': subjects,
        'selected_subject': str(subject_id),
        'subject': subject,
        'classes': classes,
        'attendance': attendance,
        'selected_attendance': str(attendance_id),
        'rows': rows,
    })


def attendance_report(request):
    """Per-student attendance summary for the staff member's own subjects.

    The previous version accumulated totals inside a loop but printed them
    outside it, so every row showed the last student's numbers, and it ignored
    which staff member was asking.
    """
    staff = get_object_or_404(Staff, admin=request.user)
    threshold = _threshold()
    site = SiteSettings.load()

    subject_id = request.GET.get("subject") or ""
    start, end = request.GET.get("start") or "", request.GET.get("end") or ""

    subjects = _my_subjects(staff)
    if subject_id:
        subjects = subjects.filter(id=subject_id)

    reports = AttendanceReport.objects.filter(attendance__subject__in=subjects)
    if start:
        reports = reports.filter(attendance__date__gte=start)
    if end:
        reports = reports.filter(attendance__date__lte=end)

    per_student = (reports.values("student_id")
                   .annotate(total=Count("id"), present=Count("id", filter=Q(status=True))))
    students = {s.id: s for s in Student.objects
                .filter(id__in=[r["student_id"] for r in per_student])
                .select_related("admin", "course")}

    rows = []
    for row in per_student:
        student = students.get(row["student_id"])
        if student is None:
            continue
        rows.append({
            "name": f"{student.admin.first_name} {student.admin.last_name}",
            "register": student.register_num or "",
            "course": student.course.name if student.course else "",
            "present": row["present"],
            "total": row["total"],
            "percent": _percent(row["present"], row["total"]),
        })
    rows.sort(key=lambda r: (r["percent"] if r["percent"] is not None else 0, r["name"]))

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="attendance-report.pdf"'
    pdf = canvas.Canvas(response, pagesize=A4)
    width, height = A4

    def header(y):
        pdf.setFont("Helvetica-Bold", 14)
        pdf.drawString(40, y, site.college_name + (f", {site.city}" if site.city else ""))
        y -= 15
        pdf.setFont("Helvetica", 9)
        who = f"{staff.admin.first_name} {staff.admin.last_name}"
        span = f" ({start or 'start'} to {end or 'today'})" if (start or end) else ""
        pdf.drawString(40, y, f"Attendance report - {who}{span}")
        y -= 12
        pdf.line(40, y, width - 40, y)
        y -= 20
        pdf.setFont("Helvetica-Bold", 9)
        for label, x in (("Student", 40), ("Register no", 210), ("Course", 310),
                         ("Present", 430), ("Classes", 480), ("%", 535)):
            pdf.drawString(x, y, label)
        return y - 14

    y = header(height - 50)
    pdf.setFont("Helvetica", 9)
    for row in rows:
        if y < 60:
            pdf.showPage()
            y = header(height - 50)
            pdf.setFont("Helvetica", 9)
        pdf.drawString(40, y, row["name"][:28])
        pdf.drawString(210, y, row["register"][:16])
        pdf.drawString(310, y, row["course"][:18])
        pdf.drawString(430, y, str(row["present"]))
        pdf.drawString(480, y, str(row["total"]))
        pdf.drawString(535, y, "-" if row["percent"] is None else f"{row['percent']}%")
        y -= 14

    if not rows:
        pdf.drawString(40, y, "No attendance has been recorded for this selection.")
        y -= 14
    y -= 6
    pdf.setFont("Helvetica-Oblique", 8)
    pdf.drawString(40, y, f"Requirement is {threshold}%. "
                          f"{sum(1 for r in rows if r['percent'] is not None and r['percent'] < threshold)}"
                          f" of {len(rows)} student(s) fall short.")
    pdf.showPage()
    pdf.save()
    return response


# ------------------------------------------------------------------ results

def staff_add_result(request):
    staff = get_object_or_404(Staff, admin=request.user)
    subjects = _my_subjects(staff)
    site = SiteSettings.load()

    subject_id = request.POST.get("subject") or request.GET.get("subject") or ""
    subject = subjects.filter(id=subject_id).first() if subject_id else None
    students = _roster(subject) if subject else []

    existing = {}
    if subject:
        existing = {r.student_id: r for r in
                    StudentResult.objects.filter(subject=subject, student__in=students)}

    if request.method == "POST" and request.POST.get("save") == "1" and subject:
        saved = 0
        for student in students:
            test = request.POST.get(f"test_{student.id}", "").strip()
            exam = request.POST.get(f"exam_{student.id}", "").strip()
            if test == "" and exam == "":
                continue
            try:
                StudentResult.objects.update_or_create(
                    student=student, subject=subject,
                    defaults={"test": float(test or 0), "exam": float(exam or 0)})
                saved += 1
            except ValueError:
                messages.error(request, f"Marks for {student.admin.first_name} are not numbers.")
        if saved:
            messages.success(request, f"Marks saved for {saved} student(s).")
        return redirect(f"{reverse('staff_add_result')}?subject={subject.id}")

    rows = [{"student": s,
             "test": existing[s.id].test if s.id in existing else "",
             "exam": existing[s.id].exam if s.id in existing else ""}
            for s in students]

    return render(request, "staff_template/staff_add_result.html", {
        'page_title': 'Enter Marks',
        'subjects': subjects,
        'selected_subject': str(subject_id),
        'subject': subject,
        'rows': rows,
        'max_internal': site.max_internal_mark,
        'max_exam': site.max_exam_mark,
    })


@csrf_exempt
def fetch_student_result(request):
    try:
        student = get_object_or_404(Student, id=request.POST.get('student'))
        subject = get_object_or_404(Subject, id=request.POST.get('subject'))
        result = StudentResult.objects.get(student=student, subject=subject)
        return JsonResponse({'exam': result.exam, 'test': result.test})
    except Exception:
        return JsonResponse({'exam': '', 'test': ''})


# ------------------------------------------------------- leave / feedback

def staff_apply_leave(request):
    form = LeaveReportStaffForm(request.POST or None)
    staff = get_object_or_404(Staff, admin_id=request.user.id)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.staff = staff
        obj.save()
        messages.success(request, "Your leave application has been submitted for review.")
        return redirect(reverse('staff_apply_leave'))
    if request.method == 'POST':
        messages.error(request, "Please correct the form below.")
    return render(request, "staff_template/staff_apply_leave.html", {
        'form': form,
        'leave_history': LeaveReportStaff.objects.filter(staff=staff).order_by('-id'),
        'page_title': 'Apply for Leave',
    })


def staff_feedback(request):
    form = FeedbackStaffForm(request.POST or None)
    staff = get_object_or_404(Staff, admin_id=request.user.id)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.staff = staff
        obj.save()
        messages.success(request, "Your feedback has been sent.")
        return redirect(reverse('staff_feedback'))
    if request.method == 'POST':
        messages.error(request, "Please correct the form below.")
    return render(request, "staff_template/staff_feedback.html", {
        'form': form,
        'feedbacks': FeedbackStaff.objects.filter(staff=staff).order_by('-id'),
        'page_title': 'Send Feedback',
    })


def staff_view_profile(request):
    staff = get_object_or_404(Staff, admin=request.user)
    site = SiteSettings.load()
    if not site.allow_staff_profile_edit and request.method == 'POST':
        messages.error(request, "Profile editing has been turned off by the administrator.")
        return redirect(reverse('staff_view_profile'))

    form = StaffEditForm(request.POST or None, request.FILES or None, instance=staff)
    if request.method == 'POST':
        if form.is_valid():
            try:
                admin = staff.admin
                password = form.cleaned_data.get('password') or None
                if password:
                    admin.set_password(password)
                passport = request.FILES.get('profile_pic')
                if passport:
                    fs = FileSystemStorage()
                    admin.profile_pic = fs.url(fs.save(passport.name, passport))
                admin.first_name = form.cleaned_data.get('first_name')
                admin.last_name = form.cleaned_data.get('last_name')
                admin.address = form.cleaned_data.get('address')
                admin.gender = form.cleaned_data.get('gender')
                admin.save()
                staff.save()
                messages.success(request, "Profile updated.")
                return redirect(reverse('staff_view_profile'))
            except Exception as e:
                messages.error(request, "Could not update your profile: " + str(e))
        else:
            messages.error(request, "Please correct the form below.")
    return render(request, "staff_template/staff_view_profile.html", {
        'form': form, 'page_title': 'My Profile', 'can_edit': site.allow_staff_profile_edit})


@csrf_exempt
def staff_fcmtoken(request):
    try:
        user = get_object_or_404(CustomUser, id=request.user.id)
        user.fcm_token = request.POST.get('token')
        user.save()
        return HttpResponse("True")
    except Exception:
        return HttpResponse("False")


def staff_view_notification(request):
    staff = get_object_or_404(Staff, admin=request.user)
    return render(request, "staff_template/staff_view_notification.html", {
        'notifications': NotificationStaff.objects.filter(staff=staff).order_by('-id'),
        'page_title': "Notifications",
    })


@csrf_exempt
def view_student_leave(request):
    """Shared with administrators - review and decide student leave."""
    if request.method == 'POST':
        leave = get_object_or_404(LeaveReportStudent, id=request.POST.get('id'))
        leave.status = 1 if request.POST.get('status') == '1' else -1
        leave.save()
        messages.success(
            request,
            f"Leave {'approved' if leave.status == 1 else 'rejected'} for "
            f"{leave.student.admin.first_name} {leave.student.admin.last_name}.")
        return redirect(reverse('view_student_leave'))

    return render(request, "hod_template/student_leave_view.html", {
        'allLeave': (LeaveReportStudent.objects
                     .select_related("student__admin", "student__course")
                     .order_by('-id')),
        'page_title': 'Student Leave Applications',
    })


# -------------------------------------------- legacy AJAX endpoints (kept)

@csrf_exempt
def get_students(request):
    """Still used by older screens; returns proper JSON, never an exception."""
    try:
        subject = get_object_or_404(Subject, id=request.POST.get('subject'))
        session = get_object_or_404(Session, id=request.POST.get('session'))
        students = Student.objects.filter(course_id=subject.course.id, session=session)
        return JsonResponse([{"id": s.id,
                              "name": f"{s.admin.first_name} {s.admin.last_name}"}
                             for s in students], safe=False)
    except Exception:
        return JsonResponse([], safe=False)


@csrf_exempt
def get_student_attendance(request):
    try:
        attendance = get_object_or_404(Attendance, id=request.POST.get('attendance_date_id'))
        data = AttendanceReport.objects.filter(attendance=attendance).select_related("student__admin")
        return JsonResponse([{"id": r.student.admin.id,
                              "name": f"{r.student.admin.first_name} {r.student.admin.last_name}",
                              "status": r.status} for r in data], safe=False)
    except Exception:
        return JsonResponse([], safe=False)


@csrf_exempt
def save_attendance(request):
    try:
        students = json.loads(request.POST.get('student_ids'))
        session = get_object_or_404(Session, id=request.POST.get('session'))
        subject = get_object_or_404(Subject, id=request.POST.get('subject'))
        attendance = Attendance(session=session, subject=subject, date=request.POST.get('date'))
        attendance.save()
        for entry in students:
            student = get_object_or_404(Student, id=entry.get('id'))
            AttendanceReport.objects.create(student=student, attendance=attendance,
                                            status=entry.get('status'))
        return HttpResponse("OK")
    except Exception:
        return HttpResponse("Error", status=400)


@csrf_exempt
def update_attendance(request):
    try:
        students = json.loads(request.POST.get('student_ids'))
        attendance = get_object_or_404(Attendance, id=request.POST.get('date'))
        for entry in students:
            student = get_object_or_404(Student, admin_id=entry.get('id'))
            AttendanceReport.objects.filter(student=student, attendance=attendance).update(
                status=entry.get('status'))
        return HttpResponse("OK")
    except Exception:
        return HttpResponse("Error", status=400)
