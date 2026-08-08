import csv
import json
import logging
import requests
import django_filters
from django.conf import settings
from django.contrib import messages
from django.core.files.storage import FileSystemStorage
from django.http import HttpResponse, JsonResponse
from django.shortcuts import (HttpResponse, HttpResponseRedirect,
                              get_object_or_404, redirect, render)
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import UpdateView
from django.db.models import Count, Q
from .forms import *
from .models import *
from .forms import SearchForm
from .filters import TableFilter
from .sms import send_sms, send_whatsapp

logger = logging.getLogger(__name__)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter





from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib import pagesizes
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle, Image



def _percent(present, total):
    return round(present / total * 100) if total else None


def admin_home(request):
    """Administrative dashboard.

    Everything is aggregated in the database. The previous version ran three
    queries per student inside a Python loop, so the page cost grew with
    enrolment; this version is a fixed number of queries.
    """
    threshold = attendance_threshold()
    total_students = Student.objects.count()
    total_staff = Staff.objects.count()
    total_course = Course.objects.count()
    total_subject = Subject.objects.count()
    total_department = Department.objects.count()
    total_session = Session.objects.count()

    # --- things somebody has to act on -----------------------------------
    pending_student_leave = LeaveReportStudent.objects.filter(status=0).count()
    pending_staff_leave = LeaveReportStaff.objects.filter(status=0).count()
    unanswered_feedback = (FeedbackStudent.objects.filter(reply="").count()
                           + FeedbackStaff.objects.filter(reply="").count())

    # --- attendance ------------------------------------------------------
    totals = AttendanceReport.objects.aggregate(
        total=Count("id"), present=Count("id", filter=Q(status=True)))
    overall_percent = _percent(totals["present"] or 0, totals["total"] or 0)

    # One row per student, then rank in Python over a small result set.
    per_student = (AttendanceReport.objects
                   .values("student_id")
                   .annotate(total=Count("id"), present=Count("id", filter=Q(status=True))))
    ranked = []
    for row in per_student:
        pct = _percent(row["present"], row["total"])
        if pct is not None:
            ranked.append((pct, row["student_id"], row["present"], row["total"]))
    ranked.sort(key=lambda r: r[0])

    defaulter_rows = [r for r in ranked if r[0] < threshold]
    defaulter_count = len(defaulter_rows)
    worst = defaulter_rows[:10]
    worst_students = {
        s.id: s for s in Student.objects
        .filter(id__in=[r[1] for r in worst])
        .select_related("admin", "course", "department")
    }
    defaulters = [{
        "student": worst_students[sid],
        "percent": pct,
        "present": present,
        "total": total,
    } for pct, sid, present, total in worst if sid in worst_students]

    # --- attendance by course (answers "which cohort is slipping?") ------
    by_course = (AttendanceReport.objects
                 .values("student__course__name")
                 .annotate(total=Count("id"), present=Count("id", filter=Q(status=True)))
                 .order_by("student__course__name"))
    course_attendance = [
        {"name": row["student__course__name"] or "Unassigned",
         "percent": _percent(row["present"], row["total"])}
        for row in by_course if row["total"]
    ]

    # --- attendance trend over the last 14 class days --------------------
    by_date = (AttendanceReport.objects
               .values("attendance__date")
               .annotate(total=Count("id"), present=Count("id", filter=Q(status=True)))
               .order_by("-attendance__date")[:14])
    trend = [{"date": row["attendance__date"], "percent": _percent(row["present"], row["total"])}
             for row in reversed(list(by_date))]

    # --- cohort composition ----------------------------------------------
    courses = (Course.objects.annotate(n=Count("student")).order_by("-n"))
    departments = (Department.objects.annotate(n=Count("staff")).order_by("name"))

    # --- recent activity --------------------------------------------------
    recent_attendance = (Attendance.objects
                         .select_related("subject", "subject__course", "subject__staff__admin")
                         .order_by("-id")[:8])
    recent_students = (Student.objects
                       .select_related("admin", "course")
                       .order_by("-id")[:5])

    context = {
        'page_title': "Administrative Dashboard",
        'total_students': total_students,
        'total_staff': total_staff,
        'total_course': total_course,
        'total_subject': total_subject,
        'total_department': total_department,
        'total_session': total_session,

        'pending_student_leave': pending_student_leave,
        'pending_staff_leave': pending_staff_leave,
        'unanswered_feedback': unanswered_feedback,

        'overall_percent': overall_percent,
        'total_marked': totals["total"] or 0,
        'total_present': totals["present"] or 0,
        'threshold': threshold,
        'defaulter_count': defaulter_count,
        'defaulters': defaulters,
        'attendance_ok': overall_percent is not None and overall_percent >= threshold,

        'course_labels': [c["name"] for c in course_attendance],
        'course_percents': [c["percent"] for c in course_attendance],
        'trend_labels': [t["date"].strftime("%d %b") for t in trend],
        'trend_percents': [t["percent"] for t in trend],
        'enrolment_labels': [c.name for c in courses],
        'enrolment_counts': [c.n for c in courses],
        'department_labels': [d.name for d in departments],
        'department_counts': [d.n for d in departments],

        'recent_attendance': recent_attendance,
        'recent_students': recent_students,
    }
    return render(request, 'hod_template/home_content.html', context)


def course_student_list(request, course_ids):
    # Split the course IDs into a list
    course_ids = course_ids.split('-')

    # Create the PDF response
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="student_lists.pdf"'

    # Create the PDF document using ReportLab
    pdf_canvas = canvas.Canvas(response, pagesize=letter)

    # Letterhead from Settings, so exported lists carry the institution's name.
    site = SiteSettings.load()
    y = 780
    pdf_canvas.setFont("Helvetica-Bold", 15)
    heading = site.college_name + (f", {site.city}" if site.city else "")
    pdf_canvas.drawString(50, y, heading)
    y -= 16
    if site.affiliated_to:
        pdf_canvas.setFont("Helvetica", 9)
        pdf_canvas.drawString(50, y, f"Affiliated to {site.affiliated_to}")
        y -= 12
    pdf_canvas.setLineWidth(1)
    pdf_canvas.line(50, y, 560, y)
    y -= 22
    for course_id in course_ids:

        # Get the students for the current course
        students = Student.objects.filter(course_id=course_id,session_id=1)

        # Get the course name
        course = Course.objects.get(id=course_id)
        pdf_canvas.setFont("Helvetica-Bold", 14)
        # Add the course name to the PDF document
        pdf_canvas.drawString(50, y, f"3rd year {course.name} Student List")
        y -= 20
        pdf_canvas.setFont("Helvetica-Bold", 12)
        # Add the student list to the PDF document
        pdf_canvas.drawString(50, y, "Name")
        pdf_canvas.drawString(200, y, "Reg No")
        pdf_canvas.drawString(350, y, "Phone Number")
        y -= 20
        for student in students:
            pdf_canvas.setFont("Helvetica", 12)
            pdf_canvas.drawString(50, y, f"{student.admin.first_name} {student.admin.last_name}")
            pdf_canvas.drawString(200, y, student.register_num)
            pdf_canvas.drawString(350, y, str(student.phone_num))
            y -= 20

            if y <= 50:
                # If not, create a new page
                pdf_canvas.showPage()
                y = 750
        y -= 20



        # Get the students for the current course
        students = Student.objects.filter(course_id=course_id,session_id=2)

        # Get the course name
        course = Course.objects.get(id=course_id)
        pdf_canvas.setFont("Helvetica-Bold", 14)
        # Add the course name to the PDF document
        pdf_canvas.drawString(50, y, f"2nd year {course.name} Student List")
        y -= 20
        pdf_canvas.setFont("Helvetica-Bold", 12)
        # Add the student list to the PDF document
        pdf_canvas.drawString(50, y, "Name")
        pdf_canvas.drawString(200, y, "Reg No")
        pdf_canvas.drawString(350, y, "Phone Number")
        y -= 20
        for student in students:
            pdf_canvas.setFont("Helvetica", 12)
            pdf_canvas.drawString(50, y, f"{student.admin.first_name} {student.admin.last_name}")
            pdf_canvas.drawString(200, y, student.register_num)
            pdf_canvas.drawString(350, y, str(student.phone_num))
            y -= 20

            if y <= 50:
                # If not, create a new page
                pdf_canvas.showPage()
                y = 750
        y -= 20


        # Get the students for the current course
        students = Student.objects.filter(course_id=course_id,session_id=3)

        # Get the course name
        course = Course.objects.get(id=course_id)
        pdf_canvas.setFont("Helvetica-Bold", 14)
        # Add the course name to the PDF document
        pdf_canvas.drawString(50, y, f"1st year {course.name} Student List")
        y -= 20
        pdf_canvas.setFont("Helvetica-Bold", 12)
        # Add the student list to the PDF document
        pdf_canvas.drawString(50, y, "Name")
        pdf_canvas.drawString(200, y, "Reg No")
        pdf_canvas.drawString(350, y, "Phone Number")
        y -= 20
        for student in students:
            pdf_canvas.setFont("Helvetica", 12)
            pdf_canvas.drawString(50, y, f"{student.admin.first_name} {student.admin.last_name}")
            pdf_canvas.drawString(200, y, student.register_num)
            pdf_canvas.drawString(350, y, str(student.phone_num))
            y -= 20

            if y <= 50:
                # If not, create a new page
                pdf_canvas.showPage()
                y = 750
        y -= 20

    pdf_canvas.save()
    return response


def add_staff(request):
    form = StaffForm(request.POST or None, request.FILES or None)
    context = {'form': form, 'page_title': 'Add Staff'}
    if request.method == 'POST':
        if form.is_valid():
            first_name = form.cleaned_data.get('first_name')
            last_name = form.cleaned_data.get('last_name')
            address = form.cleaned_data.get('address')
            email = form.cleaned_data.get('email')
            gender = form.cleaned_data.get('gender')
            password = form.cleaned_data.get('password')
            department = form.cleaned_data.get('department')
            phone_num =form.cleaned_data.get('phone_num')
            passport = request.FILES.get('profile_pic')
            aadhar_num = form.cleaned_data.get('aadhar_num')
            qualification = form.cleaned_data.get('qualification')
            fs = FileSystemStorage()
            filename = fs.save(passport.name,passport)
            passport_url = fs.url(filename)
            try:
                user = CustomUser.objects.create_user(
                    email=email, password=password, user_type=2, first_name=first_name, last_name=last_name, profile_pic=passport_url)
                user.gender = gender
                user.address = address
                user.staff.qualification = qualification
                user.staff.aadhar_num = aadhar_num
                user.staff.phone_num = phone_num
                user.staff.department = department
                user.save()
                messages.success(request, "Successfully Added")
                return redirect(reverse('add_staff'))

            except Exception as e:
                messages.error(request, "Could Not Add " + str(e))
        else:
            messages.error(request, "Please fulfil all requirements")

    return render(request, 'hod_template/add_staff_template.html', context)


def add_student(request):
    student_form = StudentForm(request.POST or None, request.FILES or None)
    context = {'form': student_form, 'page_title': 'Add Student'}
    if request.method == 'POST':
        if student_form.is_valid():
            first_name = student_form.cleaned_data.get('first_name')
            last_name = student_form.cleaned_data.get('last_name')
            address = student_form.cleaned_data.get('address')
            email = student_form.cleaned_data.get('email')
            gender = student_form.cleaned_data.get('gender')
            password = student_form.cleaned_data.get('password')
            course = student_form.cleaned_data.get('course')
            aadhar_num = student_form.cleaned_data.get('aadhar_num')
            phone_num = student_form.cleaned_data.get('phone_num')
            register_num =student_form.cleaned_data.get('register_num')
            admission_num =student_form.cleaned_data.get('admission_num')
            religion = student_form.cleaned_data.get('religion')
            department  = student_form.cleaned_data.get('department')
            date_of_birth = student_form.cleaned_data.get('date_of_birth')
            session = student_form.cleaned_data.get('session')
            passport = request.FILES['profile_pic']

            fs = FileSystemStorage()

            filename = fs.save(passport.name, passport)
            passport_url = fs.url(filename)

            try:
                user = CustomUser.objects.create_user(
                    email=email, password=password, user_type=3, first_name=first_name, last_name=last_name, profile_pic=passport_url)
                user.gender = gender
                user.address = address

                user.student.session = session
                user.student.department = department
                user.student.course = course
                user.student.religion = religion
                user.student.register_num = register_num
                user.student.admission_num = admission_num
                user.student.aadhar_num = aadhar_num
                user.student.date_of_birth = date_of_birth
                user.student.phone_num = phone_num
                user.save()
                messages.success(request, "Successfully Added")
                return redirect(reverse('add_student'))
            except Exception as e:
                messages.error(request, "Could Not Add: " + str(e))
        else:
            messages.error(request, "Could Not Add: ")
    return render(request, 'hod_template/add_student_template.html', context)

def add_course(request):
    form = CourseForm(request.POST or None)
    context = {
        'form': form,
        'page_title': 'Add Course'
    }

    if request.method == 'POST':
        if form.is_valid():
            name = form.cleaned_data.get('name')
            department = form.cleaned_data.get('department')
            try:

                course = Course()
                course.department = department
                course.name = name
                course.save()
                messages.success(request, "Successfully Added")
                return redirect(reverse('add_course'))
            except:
                messages.error(request, "Could Not Add")
        else:
            messages.error(request, "Could Not Add")
    return render(request, 'hod_template/add_course_template.html', context)


def add_department(request):
    form = DepartmentForm(request.POST or None)
    context = {
        'form': form,
        'page_title': 'Add Department'
    }
    if request.method == 'POST':
        if form.is_valid():
            name = form.cleaned_data.get('name')
            try:
                department = Department()
                department.name = name
                department.save()
                messages.success(request, "Successfully Added")
                return redirect(reverse('add_department'))
            except:
                messages.error(request, "Could Not Add")
        else:
            messages.error(request, "Could Not Add")
    return render(request, 'hod_template/add_department_template.html', context)


def add_subject(request):
    form = SubjectForm(request.POST or None)
    context = {
        'form': form,
        'page_title': 'Add Subject'
    }
    if request.method == 'POST':
        if form.is_valid():
            name = form.cleaned_data.get('name')
            course = form.cleaned_data.get('course')
            staff = form.cleaned_data.get('staff')
            session = form.cleaned_data.get('session')
            try:
                subject = Subject()
                subject.name = name
                subject.staff = staff
                subject.course = course
                subject.session=session
                subject.save()
                messages.success(request, "Successfully Added")
                return redirect(reverse('add_subject'))

            except Exception as e:
                messages.error(request, "Could Not Add " + str(e))
        else:
            messages.error(request, "Fill Form Properly")

    return render(request, 'hod_template/add_subject_template.html', context)


def manage_staff(request):

    allStaff = CustomUser.objects.filter(user_type=2)
    context = {
        'allStaff': allStaff,

        'page_title': 'Manage Staff',



    }
    return render(request, "hod_template/manage_staff.html", context)


def manage_student(request):
    students = CustomUser.objects.filter(user_type=3)
    context = {
        'students': students,
        'page_title': 'Manage Students'
    }
    return render(request, "hod_template/manage_student.html", context)

def attendance_threshold():
    """Minimum attendance percentage, from Settings > Academic rules.

    Was a module constant; it now follows whatever the college has configured.
    """
    return SiteSettings.load().attendance_threshold


def site_settings_view(request):
    """Edit institution settings. Administrators only."""
    settings_obj = SiteSettings.load()
    form = SiteSettingsForm(request.POST or None, request.FILES or None,
                            instance=settings_obj)
    if request.method == 'POST':
        if form.is_valid():
            form.save()
            messages.success(request, "Settings saved.")
            return redirect(reverse('site_settings'))
        messages.error(request, "Some settings could not be saved - check the fields marked below.")
    return render(request, "hod_template/settings.html", {
        'form': form,
        'settings_obj': settings_obj,
        'page_title': 'Settings',
    })


def view_profile(request, student_id):
    """Full student record: identity, academics, attendance, marks, requests."""
    threshold = attendance_threshold()
    student = get_object_or_404(
        Student.objects.select_related("admin", "course", "department", "session"),
        id=student_id)

    subjects = (Subject.objects
                .filter(course=student.course, session=student.session)
                .select_related("staff__admin"))

    reports = (AttendanceReport.objects
               .filter(student=student)
               .select_related("attendance__subject"))

    # Aggregate in Python: one query, then a pass over a small result set,
    # rather than two COUNT queries per subject.
    per_subject = {s.id: {"subject": s, "present": 0, "total": 0} for s in subjects}
    total_classes = present_classes = 0
    for report in reports:
        total_classes += 1
        if report.status:
            present_classes += 1
        bucket = per_subject.get(report.attendance.subject_id)
        if bucket is not None:
            bucket["total"] += 1
            if report.status:
                bucket["present"] += 1

    attendance_rows = []
    for row in per_subject.values():
        pct = round(row["present"] / row["total"] * 100) if row["total"] else None
        attendance_rows.append({
            "subject": row["subject"],
            "present": row["present"],
            "total": row["total"],
            "absent": row["total"] - row["present"],
            "percent": pct,
            "short": pct is not None and pct < threshold,
        })
    attendance_rows.sort(key=lambda r: r["subject"].name)

    overall_percent = (round(present_classes / total_classes * 100)
                       if total_classes else None)

    results = (StudentResult.objects
               .filter(student=student)
               .select_related("subject")
               .order_by("subject__name"))
    result_rows = [{
        "subject": r.subject,
        "test": r.test,
        "exam": r.exam,
        "total": r.test + r.exam,
    } for r in results]

    context = {
        'student': student,
        'page_title': f"{student.admin.first_name} {student.admin.last_name}",
        'subjects': subjects,
        'attendance_rows': attendance_rows,
        'overall_percent': overall_percent,
        'total_classes': total_classes,
        'present_classes': present_classes,
        'absent_classes': total_classes - present_classes,
        'threshold': threshold,
        'below_threshold': overall_percent is not None and overall_percent < threshold,
        'result_rows': result_rows,
        'leaves': LeaveReportStudent.objects.filter(student=student).order_by('-id'),
        'feedbacks': FeedbackStudent.objects.filter(student=student).order_by('-id'),
    }
    return render(request, "hod_template/view_profile.html", context)



def view_staff_profile(request, staff_id):
    """Full staff record: identity, teaching load, class attendance, requests."""
    threshold = attendance_threshold()
    staff = get_object_or_404(
        Staff.objects.select_related("admin", "department", "course"), id=staff_id)

    subjects = (Subject.objects
                .filter(staff=staff)
                .select_related("course", "session", "department")
                .order_by("session__start_year", "name"))

    # Three aggregate queries instead of a handful per subject.
    class_counts = {
        row["subject_id"]: row["n"]
        for row in (Attendance.objects.filter(subject__staff=staff)
                    .values("subject_id").annotate(n=Count("id")))
    }
    marked = {
        row["attendance__subject_id"]: (row["total"], row["present"])
        for row in (AttendanceReport.objects.filter(attendance__subject__staff=staff)
                    .values("attendance__subject_id")
                    .annotate(total=Count("id"),
                              present=Count("id", filter=Q(status=True))))
    }
    enrolment = {
        (row["course_id"], row["session_id"]): row["n"]
        for row in (Student.objects
                    .filter(course__in=[s.course_id for s in subjects])
                    .values("course_id", "session_id").annotate(n=Count("id")))
    }

    subject_rows = []
    for subject in subjects:
        total, present = marked.get(subject.id, (0, 0))
        subject_rows.append({
            "subject": subject,
            "classes": class_counts.get(subject.id, 0),
            "students": enrolment.get((subject.course_id, subject.session_id), 0),
            "avg_percent": round(present / total * 100) if total else None,
        })

    # Distinct people, not the sum of per-subject enrolments: a student taking
    # two of this teacher's subjects is still one student.
    pairs = {(s.course_id, s.session_id) for s in subjects}
    student_filter = Q()
    for course_id, session_id in pairs:
        student_filter |= Q(course_id=course_id, session_id=session_id)
    distinct_students = Student.objects.filter(student_filter).distinct().count() if pairs else 0

    context = {
        'staff': staff,
        'page_title': f"{staff.admin.first_name} {staff.admin.last_name}",
        'subject_rows': subject_rows,
        'total_subjects': len(subject_rows),
        'total_classes': sum(r["classes"] for r in subject_rows),
        'total_students': distinct_students,
        'threshold': threshold,
        'leaves': LeaveReportStaff.objects.filter(staff=staff).order_by('-id'),
        'feedbacks': FeedbackStaff.objects.filter(staff=staff).order_by('-id'),
        'notifications': NotificationStaff.objects.filter(staff=staff).order_by('-id')[:10],
    }
    return render(request, "hod_template/view_staff_profile.html", context)


def edit_Department(request, department_id):
    instance = get_object_or_404(Department,id=department_id)
    form = DepartmentForm(request.POST or None, instance=instance)
    context = {
        'form': form,
        'department_id': department_id,
        'page_title': 'Edit Department'
    }
    if request.method == 'POST':
        if form.is_valid():
            name = form.cleaned_data.get('name')
            try:
                department = Department.objects.get(id=department_id)
                department.name = name
                department.save()
                messages.success(request, "Successfully Updated")
            except:
                messages.error(request, "Could Not Update")
        else:
            messages.error(request, "Could Not Update")

    return render(request, 'hod_template/edit_department_template.html', context)




def manage_course(request):

    courses = Course.objects.all()


    context = {
        'courses': courses,
        'page_title': 'Manage Courses'
    }
    return render(request, "hod_template/manage_course.html", context)

def manage_department(request):
    departments = Department.objects.all()
    context = {
        'departments': departments,
        'page_title': 'Manage Department'
    }
    return render(request, "hod_template/manage_department.html", context)


def manage_subject(request):
    subjects = Subject.objects.all()
    context = {
        'subjects': subjects,
        'page_title': 'Manage Subjects'
    }
    return render(request, "hod_template/manage_subject.html", context)


def edit_staff(request, staff_id):
    staff = get_object_or_404(Staff, id=staff_id)
    form = StaffForm(request.POST or None, request.FILES or None, instance=staff)
    context = {
        'form': form,
        'staff_id': staff_id,
        'page_title': 'Edit Staff'
    }
    if request.method == 'POST':
        if form.is_valid():
            first_name = form.cleaned_data.get('first_name')
            last_name = form.cleaned_data.get('last_name')
            address = form.cleaned_data.get('address')
            email = form.cleaned_data.get('email')
            gender = form.cleaned_data.get('gender')
            password = form.cleaned_data.get('password') or None
            department = form.cleaned_data.get('department')
            qualification = form.cleaned_data.get('qualification')
            aadhar_num= form.cleaned_data.get('aadhar_num')
            phone_num = form.cleaned_data.get('phone_num')
            passport = request.FILES.get('profile_pic') or None
            try:
                user = CustomUser.objects.get(id=staff.admin.id)
                user.email = email
                if password != None:
                    user.set_password(password)
                if passport != None:
                    fs = FileSystemStorage()
                    filename = fs.save(passport.name, passport)
                    passport_url = fs.url(filename)
                    user.profile_pic = passport_url
                user.first_name = first_name
                user.last_name = last_name
                user.gender = gender
                user.address = address
                user.staff.phone_num = phone_num
                user.staff.department = department
                user.staff.qualification = qualification
                user.staff.aadhar_num = aadhar_num
                user.save()
                staff.save()
                messages.success(request, "Successfully Updated")
                return redirect(reverse('edit_staff', args=[staff_id]))
            except Exception as e:
                messages.error(request, "Could Not Update " + str(e))
        else:
            messages.error(request, "Please fill the form properly")
    # Falls through on GET, on an invalid form and on a failed save, so the
    # admin gets the form back with the error instead of a 500.
    return render(request, "hod_template/edit_staff_template.html", context)


def edit_student(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    form = StudentForm(request.POST or None, request.FILES or None, instance=student)
    context = {
        'form': form,
        'student_id': student_id,
        'page_title': 'Edit Student'
    }
    if request.method == 'POST':
        if form.is_valid():
            first_name = form.cleaned_data.get('first_name')
            last_name = form.cleaned_data.get('last_name')
            address = form.cleaned_data.get('address')
            email = form.cleaned_data.get('email')
            gender = form.cleaned_data.get('gender')
            password = form.cleaned_data.get('password') or None
            course = form.cleaned_data.get('course')
            department  = form.cleaned_data.get('department')
            aadhar_num = form.cleaned_data.get('aadhar_num')
            date_of_birth = form.cleaned_data.get('date_of_birth')
            religion = form.cleaned_data.get('religion')
            register_num =form.cleaned_data.get('register_num')
            admission_num =form.cleaned_data.get('admission_num')
            session = form.cleaned_data.get('session')
            phone_num = form.cleaned_data.get('phone_num')
            passport = request.FILES.get('profile_pic') or None


            try:
                user = CustomUser.objects.get(id=student.admin.id)
                if passport != None:
                    fs = FileSystemStorage()
                    filename = fs.save(passport.name, passport)
                    passport_url = fs.url(filename)
                    user.profile_pic = passport_url
                user.email = email
                if password != None:
                    user.set_password(password)
                user.first_name = first_name
                user.last_name = last_name
                user.gender = gender
                user.address = address

                # These all live on Student, not on CustomUser. Assigning them
                # to the user object just set throwaway attributes, so every
                # one of these edits was silently discarded.
                student.session = session
                student.course = course
                student.department = department
                student.phone_num = phone_num
                student.aadhar_num = aadhar_num
                student.religion = religion
                student.register_num = register_num
                student.admission_num = admission_num
                student.date_of_birth = date_of_birth

                user.save()
                student.save()
                messages.success(request, "Successfully Updated")
                return redirect(reverse('edit_student', args=[student_id]))
            except Exception as e:
                messages.error(request, "Could Not Update " + str(e))
        else:
            messages.error(request, "Please Fill Form Properly!")
    # Falls through on GET, on an invalid form and on a failed save.
    return render(request, "hod_template/edit_student_template.html", context)

def edit_Department(request, department_id):
    instance = get_object_or_404(Department,id=department_id)
    form = DepartmentForm(request.POST or None, instance=instance)
    context = {
        'form': form,
        'department_id': department_id,
        'page_title': 'Edit Department'
    }
    if request.method == 'POST':
        if form.is_valid():
            name = form.cleaned_data.get('name')
            try:
                department = Department.objects.get(id=department_id)
                department.name = name
                department.save()
                messages.success(request, "Successfully Updated")
            except:
                messages.error(request, "Could Not Update")
        else:
            messages.error(request, "Could Not Update")

    return render(request, 'hod_template/edit_department_template.html', context)


def edit_course(request, course_id):
    instance = get_object_or_404(Course, id=course_id)
    form = CourseForm(request.POST or None, instance=instance)
    context = {
        'form': form,
        'course_id': course_id,
        'page_title': 'Edit Course'
    }
    if request.method == 'POST':
        if form.is_valid():
            name = form.cleaned_data.get('name')
            department = form.cleaned_data.get('department')
            try:
                course = Course.objects.get(id=course_id)
                course.department = department
                course.name = name
                course.save()
                messages.success(request, "Successfully Updated")
            except:
                messages.error(request, "Could Not Update")
        else:
            messages.error(request, "Could Not Update")

    return render(request, 'hod_template/edit_course_template.html', context)


def edit_subject(request, subject_id):
    instance = get_object_or_404(Subject, id=subject_id)
    form = SubjectForm(request.POST or None, instance=instance)
    context = {
        'form': form,
        'subject_id': subject_id,
        'page_title': 'Edit Subject'
    }
    if request.method == 'POST':
        if form.is_valid():
            name = form.cleaned_data.get('name')
            course = form.cleaned_data.get('course')
            staff = form.cleaned_data.get('staff')
            try:
                subject = Subject.objects.get(id=subject_id)
                subject.name = name
                subject.staff = staff
                subject.course = course
                subject.save()
                messages.success(request, "Successfully Updated")
                return redirect(reverse('edit_subject', args=[subject_id]))
            except Exception as e:
                messages.error(request, "Could Not Add " + str(e))
        else:
            messages.error(request, "Fill Form Properly")
    return render(request, 'hod_template/edit_subject_template.html', context)


def add_session(request):
    form = SessionForm(request.POST or None)
    context = {'form': form, 'page_title': 'Add Session'}
    if request.method == 'POST':
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Session Created")
                return redirect(reverse('add_session'))
            except Exception as e:
                messages.error(request, 'Could Not Add ' + str(e))
        else:
            messages.error(request, 'Fill Form Properly ')
    return render(request, "hod_template/add_session_template.html", context)


def manage_session(request):
    sessions = Session.objects.all()
    context = {'sessions': sessions, 'page_title': 'Manage Sessions'}
    return render(request, "hod_template/manage_session.html", context)


def edit_session(request, session_id):
    instance = get_object_or_404(Session, id=session_id)
    form = SessionForm(request.POST or None, instance=instance)
    context = {'form': form, 'session_id': session_id,
               'page_title': 'Edit Session'}
    if request.method == 'POST':
        if form.is_valid():
            try:
                form.save()
                messages.success(request, "Session Updated")
                return redirect(reverse('edit_session', args=[session_id]))
            except Exception as e:
                messages.error(
                    request, "Session Could Not Be Updated " + str(e))
                return render(request, "hod_template/edit_session_template.html", context)
        else:
            messages.error(request, "Invalid Form Submitted ")
            return render(request, "hod_template/edit_session_template.html", context)

    else:
        return render(request, "hod_template/edit_session_template.html", context)


@csrf_exempt
def check_email_availability(request):
    email = request.POST.get("email")
    try:
        user = CustomUser.objects.filter(email=email).exists()
        if user:
            return HttpResponse(True)
        return HttpResponse(False)
    except Exception as e:
        return HttpResponse(False)


@csrf_exempt
def student_feedback_message(request):
    if request.method != 'POST':
        feedbacks = FeedbackStudent.objects.all()
        context = {
            'feedbacks': feedbacks,
            'page_title': 'Student Feedback Messages'
        }
        return render(request, 'hod_template/student_feedback_template.html', context)
    else:
        feedback_id = request.POST.get('id')
        try:
            feedback = get_object_or_404(FeedbackStudent, id=feedback_id)
            reply = request.POST.get('reply')
            feedback.reply = reply
            feedback.save()
            return HttpResponse(True)
        except Exception as e:
            return HttpResponse(False)


@csrf_exempt
def staff_feedback_message(request):
    if request.method != 'POST':
        feedbacks = FeedbackStaff.objects.all()
        context = {
            'feedbacks': feedbacks,
            'page_title': 'Staff Feedback Messages'
        }
        return render(request, 'hod_template/staff_feedback_template.html', context)
    else:
        feedback_id = request.POST.get('id')
        try:
            feedback = get_object_or_404(FeedbackStaff, id=feedback_id)
            reply = request.POST.get('reply')
            feedback.reply = reply
            feedback.save()
            return HttpResponse(True)
        except Exception as e:
            return HttpResponse(False)


@csrf_exempt
def view_staff_leave(request):
    if request.method != 'POST':
        allLeave = LeaveReportStaff.objects.all()
        context = {
            'allLeave': allLeave,
            'page_title': 'Leave Applications From Staff'
        }
        return render(request, "hod_template/staff_leave_view.html", context)
    else:
        id = request.POST.get('id')
        status = request.POST.get('status')
        if (status == '1'):
            status = 1
        else:
            status = -1
        try:
            leave = get_object_or_404(LeaveReportStaff, id=id)
            leave.status = status
            leave.save()
            return HttpResponse(True)
        except Exception as e:
            return False




def _attendance_register(request):
    """Build the register for the selected subject and date range.

    Returns (context_dict, subject). Shared by the page and the CSV export so
    the download always matches exactly what is on screen.
    """
    threshold = attendance_threshold()
    subjects = (Subject.objects
                .select_related("course", "session", "staff__admin")
                .order_by("course__name", "name"))
    sessions = Session.objects.order_by("-start_year")

    subject_id = request.GET.get("subject") or ""
    start = request.GET.get("start") or ""
    end = request.GET.get("end") or ""

    ctx = {
        "page_title": "View Attendance",
        "subjects": subjects,
        "sessions": sessions,
        "selected_subject": subject_id,
        "start": start,
        "end": end,
        "threshold": threshold,
        "subject": None,
        "dates": [],
        "rows": [],
    }

    if not subject_id:
        return ctx, None

    subject = (Subject.objects
               .select_related("course", "session", "staff__admin", "department")
               .filter(id=subject_id).first())
    if subject is None:
        return ctx, None

    classes = Attendance.objects.filter(subject=subject)
    if start:
        classes = classes.filter(date__gte=start)
    if end:
        classes = classes.filter(date__lte=end)
    classes = list(classes.order_by("date", "id"))

    students = list(Student.objects
                    .filter(course=subject.course, session=subject.session)
                    .select_related("admin")
                    .order_by("admin__first_name", "admin__last_name"))

    # One query for every mark in range, then assemble the grid in memory.
    marks = {
        (r["student_id"], r["attendance_id"]): r["status"]
        for r in AttendanceReport.objects
        .filter(attendance__in=classes, student__in=students)
        .values("student_id", "attendance_id", "status")
    }

    rows = []
    for student in students:
        cells, present = [], 0
        for klass in classes:
            status = marks.get((student.id, klass.id))
            cells.append(status)          # True / False / None (not recorded)
            if status:
                present += 1
        counted = sum(1 for c in cells if c is not None)
        percent = _percent(present, counted)
        rows.append({
            "student": student,
            "cells": cells,
            "present": present,
            "counted": counted,
            "percent": percent,
            "short": percent is not None and percent < threshold,
        })

    # Per-class totals for the footer row.
    date_totals = []
    for klass in classes:
        present = sum(1 for s in students if marks.get((s.id, klass.id)) is True)
        recorded = sum(1 for s in students if marks.get((s.id, klass.id)) is not None)
        date_totals.append({"attendance": klass, "present": present, "recorded": recorded,
                            "percent": _percent(present, recorded)})

    total_marks = sum(r["counted"] for r in rows)
    total_present = sum(r["present"] for r in rows)

    ctx.update({
        "subject": subject,
        "dates": date_totals,
        "rows": rows,
        "classes_held": len(classes),
        "student_count": len(students),
        "average_percent": _percent(total_present, total_marks),
        "short_count": sum(1 for r in rows if r["short"]),
    })
    return ctx, subject


def admin_view_attendance(request):
    ctx, _ = _attendance_register(request)
    return render(request, "hod_template/admin_view_attendance.html", ctx)


def export_attendance(request):
    """Download the register currently on screen as CSV."""
    ctx, subject = _attendance_register(request)
    if subject is None:
        messages.error(request, "Choose a subject before exporting.")
        return redirect(reverse("admin_view_attendance"))

    response = HttpResponse(content_type="text/csv")
    filename = f"attendance-{subject.name}-{subject.course.name}.csv".replace(" ", "-")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    header = (["Student", "Register no"]
              + [d["attendance"].date.isoformat() for d in ctx["dates"]]
              + ["Present", "Classes", "Attendance %"])
    writer.writerow(header)
    for row in ctx["rows"]:
        student = row["student"]
        cells = ["P" if c is True else "A" if c is False else "" for c in row["cells"]]
        writer.writerow([f"{student.admin.first_name} {student.admin.last_name}",
                         student.register_num or ""]
                        + cells
                        + [row["present"], row["counted"],
                           "" if row["percent"] is None else row["percent"]])
    return response


@csrf_exempt
def get_admin_attendance(request):
    subject_id = request.POST.get('subject')
    session_id = request.POST.get('session')
    attendance_date_id = request.POST.get('attendance_date_id')
    try:
        subject = get_object_or_404(Subject, id=subject_id)
        session = get_object_or_404(Session, id=session_id)
        attendance = get_object_or_404(
            Attendance, id=attendance_date_id, session=session)
        attendance_reports = AttendanceReport.objects.filter(
            attendance=attendance)
        json_data = []
        for report in attendance_reports:
            data = {
                "status":  str(report.status),
                "name": str(report.student)
            }
            json_data.append(data)
        return JsonResponse(json.dumps(json_data), safe=False)
    except Exception as e:
        return None


def admin_view_profile(request):
    admin = get_object_or_404(Admin, admin=request.user)
    form = AdminForm(request.POST or None, request.FILES or None,
                     instance=admin)
    context = {'form': form,
               'page_title': 'View/Edit Profile'
               }
    if request.method == 'POST':
        try:
            if form.is_valid():
                first_name = form.cleaned_data.get('first_name')
                last_name = form.cleaned_data.get('last_name')
                password = form.cleaned_data.get('password') or None
                passport = request.FILES.get('profile_pic') or None
                custom_user = admin.admin
                if password != None:
                    custom_user.set_password(password)
                if passport != None:
                    fs = FileSystemStorage()
                    filename = fs.save(passport.name, passport)
                    passport_url = fs.url(filename)
                    custom_user.profile_pic = passport_url
                custom_user.first_name = first_name
                custom_user.last_name = last_name
                custom_user.save()
                messages.success(request, "Profile Updated!")
                return redirect(reverse('admin_view_profile'))
            else:
                messages.error(request, "Invalid Data Provided")
        except Exception as e:
            messages.error(
                request, "Error Occured While Updating Profile " + str(e))
    return render(request, "hod_template/admin_view_profile.html", context)


def admin_notify_staff(request):
    staff = CustomUser.objects.filter(user_type=2)
    context = {
        'page_title': "Send Notifications To Staff",
        'allStaff': staff
    }
    return render(request, "hod_template/staff_notification.html", context)


def admin_notify_student(request):
    student = CustomUser.objects.filter(user_type=3)
    context = {
        'page_title': "Send Notifications To Students",
        'students': student
    }
    return render(request, "hod_template/student_notification.html", context)


def _send_push(fcm_token, message, click_action):
    """Best-effort web push. Never raises - a push failure must not lose the notice."""
    if not (settings.FCM_SERVER_KEY and fcm_token):
        return
    try:
        requests.post(
            "https://fcm.googleapis.com/fcm/send",
            data=json.dumps({
                'notification': {
                    'title': "E-Navajyothi",
                    'body': message,
                    'click_action': reverse(click_action),
                    'icon': static('dist/img/AdminLTELogo.png'),
                },
                'to': fcm_token,
            }),
            headers={'Authorization': f'key={settings.FCM_SERVER_KEY}',
                     'Content-Type': 'application/json'},
            timeout=5,
        )
    except Exception:
        logger.exception("Push notification failed")


@csrf_exempt
def send_student_notification(request):
    id = request.POST.get('id')
    message = request.POST.get('message')
    student = get_object_or_404(Student, admin_id=id)
    try:
        # Record it first: the notice is the thing that matters, delivery is
        # best-effort. (Sending used to live in NotificationStudent.save(),
        # so every save - including seeds and fixtures - sent a real message.)
        NotificationStudent.objects.create(student=student, message=message)
        _send_push(student.admin.fcm_token, message, 'student_view_notification')
        send_whatsapp(student.phone_num, message)
        return HttpResponse("True")
    except Exception:
        logger.exception("Could not send student notification")
        return HttpResponse("False")


@csrf_exempt
def send_staff_notification(request):
    id = request.POST.get('id')
    message = request.POST.get('message')
    staff = get_object_or_404(Staff, admin_id=id)
    try:
        NotificationStaff.objects.create(staff=staff, message=message)
        _send_push(staff.admin.fcm_token, message, 'staff_view_notification')
        send_sms(staff.phone_num, message)
        return HttpResponse("True")
    except Exception:
        logger.exception("Could not send staff notification")
        return HttpResponse("False")


def delete_staff(request, staff_id):
    staff = get_object_or_404(CustomUser, staff__id=staff_id)
    staff.delete()
    messages.success(request, "Staff deleted successfully!")
    return redirect(reverse('manage_staff'))


def delete_student(request, student_id):
    student = get_object_or_404(CustomUser, student__id=student_id)
    student.delete()
    messages.success(request, "Student deleted successfully!")
    return redirect(reverse('manage_student'))

def delete_department(request, department_id):
    department = get_object_or_404(Department, id=department_id)
    try:
        department.delete()
        messages.success(request, "Course deleted successfully!")
    except Exception:
        messages.error(
            request, "Sorry, some students are assigned to this department already. Kindly change the affected student course and try again")
    return redirect(reverse('manage_department'))


def delete_course(request, course_id):
    course = get_object_or_404(Course, id=course_id)
    try:
        course.delete()
        messages.success(request, "Course deleted successfully!")
    except Exception:
        messages.error(
            request, "Sorry, some students are assigned to this course already. Kindly change the affected student course and try again")
    return redirect(reverse('manage_course'))


def delete_subject(request, subject_id):
    subject = get_object_or_404(Subject, id=subject_id)
    subject.delete()
    messages.success(request, "Subject deleted successfully!")
    return redirect(reverse('manage_subject'))


def delete_session(request, session_id):
    session = get_object_or_404(Session, id=session_id)
    try:
        session.delete()
        messages.success(request, "Session deleted successfully!")
    except Exception:
        messages.error(
            request, "There are students assigned to this session. Please move them to another session.")
    return redirect(reverse('manage_session'))
