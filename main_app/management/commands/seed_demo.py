"""Populate the database with a realistic set of demo records.

    python manage.py seed_demo            # create (or top up) the demo data
    python manage.py seed_demo --reset    # delete the demo data, then recreate it
    python manage.py seed_demo --delete   # delete the demo data and stop

Everything it creates is tagged by the DEMO_DOMAIN e-mail suffix, so --delete
and --reset only ever touch demo rows and leave real accounts alone.

Notification rows are written with bulk_create() on purpose: NotificationStaff
.save() and NotificationStudent.save() place a live Twilio SMS/WhatsApp call on
every save, and seeding must never send messages or bill the account.
"""

import random
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from main_app.models import (Attendance, AttendanceReport, Course, CustomUser,
                             Department, FeedbackStaff, FeedbackStudent,
                             LeaveReportStaff, LeaveReportStudent,
                             NotificationStaff, NotificationStudent, Session,
                             Staff, Student, StudentResult, Subject)

DEMO_DOMAIN = "@demo.skillycms.local"
# Accounts seeded before the product was renamed. Kept so --delete
# can still clean them up.
LEGACY_DEMO_DOMAINS = ["@demo.enavajyothi.local"]
DEMO_PASSWORD = "demo1234"

DEPARTMENTS = ["Computer Science", "Commerce", "English", "Mathematics", "Physics"]

# course name -> department
COURSES = {
    "BCA": "Computer Science",
    "B.Sc Computer Science": "Computer Science",
    "B.Com Finance": "Commerce",
    "BA English": "English",
    "B.Sc Mathematics": "Mathematics",
    "B.Sc Physics": "Physics",
}

# Sessions are ordered so that session 1 is the senior batch, matching the
# "3rd year / 2nd year / 1st year" ordering that course_student_list assumes.
SESSIONS = [
    (date(2023, 6, 1), date(2026, 3, 31)),
    (date(2024, 6, 1), date(2027, 3, 31)),
    (date(2025, 6, 1), date(2028, 3, 31)),
]

STAFF = [
    ("Anil", "Kurian", "Computer Science", "M.Tech, Ph.D"),
    ("Deepa", "Menon", "Computer Science", "M.Sc, M.Phil"),
    ("Rajesh", "Nair", "Commerce", "M.Com, NET"),
    ("Sreelatha", "Pillai", "English", "MA, Ph.D"),
    ("Thomas", "Varghese", "Mathematics", "M.Sc, NET"),
    ("Fathima", "Beevi", "Physics", "M.Sc, Ph.D"),
]

FIRST_NAMES = [
    "Aravind", "Nithya", "Vishnu", "Meera", "Sandeep", "Anjali", "Rahul",
    "Divya", "Jithin", "Keerthi", "Manoj", "Sneha", "Arun", "Lakshmi",
    "Praveen", "Aiswarya", "Sujith", "Reshma", "Vivek", "Gayathri",
    "Nikhil", "Athira", "Sanjay", "Parvathy", "Akhil", "Haritha",
    "Ranjith", "Swathi", "Bibin", "Neethu", "Alan", "Krishnapriya",
    "Jerin", "Anupama", "Midhun", "Soorya",
]
LAST_NAMES = [
    "Nair", "Menon", "Pillai", "Kurup", "Thomas", "Joseph", "Mathew",
    "Krishnan", "Raj", "Das", "Varghese", "Sebastian",
]

SUBJECTS = {
    "BCA": ["Data Structures", "Operating Systems", "Web Technologies"],
    "B.Sc Computer Science": ["Discrete Mathematics", "Computer Networks", "Database Systems"],
    "B.Com Finance": ["Financial Accounting", "Corporate Law", "Cost Accounting"],
    "BA English": ["British Literature", "Linguistics", "Modern Poetry"],
    "B.Sc Mathematics": ["Real Analysis", "Linear Algebra", "Differential Equations"],
    "B.Sc Physics": ["Classical Mechanics", "Quantum Physics", "Electronics"],
}

RELIGIONS = ["Hindu", "Christian", "Muslim"]

LEAVE_REASONS = [
    "Attending a cousin's wedding at Kannur.",
    "Down with viral fever, advised two days rest.",
    "Travelling home for Onam celebrations.",
    "Representing the college at the inter-collegiate meet.",
    "Family function at native place.",
]
STUDENT_FEEDBACK = [
    "The lab sessions are very helpful, could we have more of them?",
    "Please upload the previous year question papers on the portal.",
    "The library closes too early during exam season.",
    "Requesting extra tutorials before the model exam.",
]
STAFF_FEEDBACK = [
    "The projector in Room 204 needs replacing.",
    "Requesting more lab assistants for the practical batches.",
    "Attendance portal could use a bulk-entry screen.",
]
STUDENT_NOTICES = [
    "Model examinations begin on 12 January. Hall tickets are available at the office.",
    "College will remain closed tomorrow on account of heavy rain.",
    "Last date for paying the semester fee is 28 February.",
]
STAFF_NOTICES = [
    "Department meeting on Friday at 3:00 PM in the seminar hall.",
    "Please submit internal assessment marks before the 25th.",
]

# Existing files under media/ so the demo avatars actually resolve.
AVATARS = [
    "/media/IMG-20230225-WA0010.jpg",
    "/media/IMG-20230225-WA0011.jpg",
    "/media/unnamed.jpg",
    "",  # templates fall back to a default when profile_pic is blank
]


class Command(BaseCommand):
    help = "Create a realistic set of demo departments, staff, students, attendance and results."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true",
                            help="Delete existing demo data before seeding.")
        parser.add_argument("--delete", action="store_true",
                            help="Delete the demo data and exit without seeding.")

    def handle(self, *args, **options):
        random.seed(20260808)  # reproducible runs

        if options["reset"] or options["delete"]:
            self.delete_demo_data()
            if options["delete"]:
                return

        with transaction.atomic():
            self.seed()

    # ------------------------------------------------------------------ delete

    def delete_demo_data(self):
        from django.db.models import Q
        match = Q(email__endswith=DEMO_DOMAIN)
        for legacy in LEGACY_DEMO_DOMAINS:
            match |= Q(email__endswith=legacy)
        users = CustomUser.objects.filter(match)
        count = users.count()
        # Staff/Student rows cascade from CustomUser; attendance, results,
        # leave, feedback and notifications cascade from those in turn.
        users.delete()
        Subject.objects.filter(name__in=[s for subs in SUBJECTS.values() for s in subs]).delete()
        Course.objects.filter(name__in=COURSES).delete()
        Department.objects.filter(name__in=DEPARTMENTS).delete()
        Session.objects.filter(start_year__in=[s for s, _ in SESSIONS]).delete()
        self.stdout.write(self.style.WARNING(f"Deleted demo data ({count} demo users)."))

    # -------------------------------------------------------------------- seed

    def seed(self):
        departments = {
            name: Department.objects.get_or_create(name=name)[0]
            for name in DEPARTMENTS
        }
        courses = {
            name: Course.objects.get_or_create(
                name=name, defaults={"department": departments[dept]})[0]
            for name, dept in COURSES.items()
        }
        sessions = [
            Session.objects.get_or_create(start_year=start, end_year=end)[0]
            for start, end in SESSIONS
        ]

        staff_by_dept = {}
        staff_records = []
        for i, (first, last, dept, qualification) in enumerate(STAFF):
            user = self.make_user(first, last, f"{first}.{last}".lower(), user_type=2, index=i)
            staff = Staff.objects.get(admin=user)
            staff.department = departments[dept]
            staff.course = next(c for n, c in courses.items() if COURSES[n] == dept)
            staff.qualification = qualification
            staff.phone_num = f"98470{i:05d}"
            staff.aadhar_num = f"3000000{i:05d}"
            staff.save()
            staff_by_dept.setdefault(dept, []).append(staff)
            staff_records.append(staff)

        # Two subjects per course per session, taught by that department's staff.
        subjects = []
        for course_name, course in courses.items():
            dept_name = COURSES[course_name]
            dept_staff = staff_by_dept[dept_name]
            for session_index, session in enumerate(sessions):
                for subject_name in SUBJECTS[course_name][:2]:
                    teacher = dept_staff[session_index % len(dept_staff)]
                    subject, _ = Subject.objects.get_or_create(
                        name=subject_name, course=course, session=session,
                        defaults={"staff": teacher, "department": departments[dept_name]})
                    subjects.append(subject)

        # Two students in every course/session bucket.
        students = []
        name_pool = list(zip(FIRST_NAMES, (random.choice(LAST_NAMES) for _ in FIRST_NAMES)))
        n = 0
        for course_name, course in courses.items():
            for year_offset, session in enumerate(sessions):
                for _ in range(2):
                    first, last = name_pool[n % len(name_pool)]
                    user = self.make_user(first, last, f"{first}.{last}{n}".lower(),
                                          user_type=3, index=n)
                    student = Student.objects.get(admin=user)
                    student.department = departments[COURSES[course_name]]
                    student.course = course
                    student.session = session
                    student.religion = random.choice(RELIGIONS)
                    student.phone_num = f"98460{n:05d}"
                    student.aadhar_num = f"5000000{n:05d}"
                    student.date_of_birth = date(2004 + year_offset, random.randint(1, 12),
                                                 random.randint(1, 28))
                    student.register_num = f"NJC{2023 + year_offset}{n:03d}"
                    student.admission_num = f"ADM{2023 + year_offset}{n:04d}"
                    student.save()
                    students.append(student)
                    n += 1

        self.seed_attendance(subjects, students)
        self.seed_results(subjects, students)
        self.seed_leave_and_feedback(staff_records, students)
        self.seed_notifications(staff_records, students)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(departments)} departments, {len(courses)} courses, "
            f"{len(sessions)} sessions, {len(staff_records)} staff, "
            f"{len(students)} students, {len(subjects)} subjects."))
        self.stdout.write(f"Demo login: any {DEMO_DOMAIN} address / password '{DEMO_PASSWORD}'")

    # ----------------------------------------------------------------- helpers

    def make_user(self, first, last, slug, user_type, index):
        email = f"{slug}{DEMO_DOMAIN}"
        existing = CustomUser.objects.filter(email=email).first()
        if existing:
            return existing
        user = CustomUser.objects.create_user(
            email=email, password=DEMO_PASSWORD, user_type=user_type,
            first_name=first, last_name=last)
        user.gender = random.choice(["M", "F"])
        user.address = random.choice([
            "Cherupuzha, Kannur", "Payyanur, Kannur", "Taliparamba, Kannur",
            "Iritty, Kannur", "Kanhangad, Kasaragod",
        ])
        user.profile_pic = AVATARS[index % len(AVATARS)]
        user.save()
        return user

    def seed_attendance(self, subjects, students):
        """Six class dates per subject, ~85% attendance."""
        attendances, reports = [], []
        for subject in subjects:
            enrolled = [s for s in students
                        if s.course_id == subject.course_id and s.session_id == subject.session_id]
            if not enrolled:
                continue
            if Attendance.objects.filter(subject=subject).exists():
                continue
            class_date = date(2026, 1, 5)
            for week in range(6):
                attendance = Attendance.objects.create(
                    session=subject.session, subject=subject,
                    date=class_date + timedelta(days=week * 7))
                attendances.append(attendance)
                for student in enrolled:
                    reports.append(AttendanceReport(
                        student=student, attendance=attendance,
                        status=random.random() > 0.15))
        AttendanceReport.objects.bulk_create(reports)
        self.stdout.write(f"  attendance: {len(attendances)} classes, {len(reports)} entries")

    def seed_results(self, subjects, students):
        results = []
        for subject in subjects:
            enrolled = [s for s in students
                        if s.course_id == subject.course_id and s.session_id == subject.session_id]
            for student in enrolled:
                if StudentResult.objects.filter(student=student, subject=subject).exists():
                    continue
                results.append(StudentResult(
                    student=student, subject=subject,
                    test=round(random.uniform(10, 20), 1),      # out of 20
                    exam=round(random.uniform(35, 78), 1)))     # out of 80
        StudentResult.objects.bulk_create(results)
        self.stdout.write(f"  results: {len(results)} mark entries")

    def seed_leave_and_feedback(self, staff_records, students):
        leave_student, leave_staff, fb_student, fb_staff = [], [], [], []
        for i, student in enumerate(students[::4]):
            leave_student.append(LeaveReportStudent(
                student=student, date=str(date(2026, 2, 1) + timedelta(days=i * 3)),
                message=random.choice(LEAVE_REASONS),
                status=random.choice([0, 1, -1])))
        for i, student in enumerate(students[::6]):
            fb_student.append(FeedbackStudent(
                student=student, feedback=random.choice(STUDENT_FEEDBACK),
                reply="" if i % 2 else "Noted, we will look into this."))
        for i, staff in enumerate(staff_records[::2]):
            leave_staff.append(LeaveReportStaff(
                staff=staff, date=str(date(2026, 2, 10) + timedelta(days=i * 5)),
                message=random.choice(LEAVE_REASONS),
                status=random.choice([0, 1, -1])))
            fb_staff.append(FeedbackStaff(
                staff=staff, feedback=random.choice(STAFF_FEEDBACK), reply=""))
        LeaveReportStudent.objects.bulk_create(leave_student)
        LeaveReportStaff.objects.bulk_create(leave_staff)
        FeedbackStudent.objects.bulk_create(fb_student)
        FeedbackStaff.objects.bulk_create(fb_staff)
        self.stdout.write(
            f"  leave: {len(leave_student)} student / {len(leave_staff)} staff, "
            f"feedback: {len(fb_student)} student / {len(fb_staff)} staff")

    def seed_notifications(self, staff_records, students):
        """bulk_create skips Model.save(), so no Twilio message is sent."""
        student_notices = [
            NotificationStudent(student=student, message=random.choice(STUDENT_NOTICES))
            for student in students[::5]
        ]
        staff_notices = [
            NotificationStaff(staff=staff, message=random.choice(STAFF_NOTICES))
            for staff in staff_records[::2]
        ]
        NotificationStudent.objects.bulk_create(student_notices)
        NotificationStaff.objects.bulk_create(staff_notices)
        self.stdout.write(
            f"  notifications: {len(student_notices)} student / {len(staff_notices)} staff "
            f"(written without triggering Twilio)")
