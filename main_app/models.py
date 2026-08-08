from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import UserManager
from django.core.validators import (MaxValueValidator, MinValueValidator,
                                    RegexValidator)
from django.dispatch import receiver
from django.db.models.signals import post_save
from django.db import models
from django.contrib.auth.models import AbstractUser

class CustomUserManager(UserManager):
    def _create_user(self, email, password, **extra_fields):
        email = self.normalize_email(email)
        user = CustomUser(email=email, **extra_fields)
        user.password = make_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        assert extra_fields["is_staff"]
        assert extra_fields["is_superuser"]
        return self._create_user(email, password, **extra_fields)


class Session(models.Model):
    start_year = models.DateField()
    end_year = models.DateField()

    def __str__(self):
        return "From " + str(self.start_year) + " to " + str(self.end_year)


HEX_COLOUR = RegexValidator(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
                            "Enter a hex colour such as #0d6b7c.")


class SiteSettings(models.Model):
    """Institution-wide configuration, edited from Settings by an administrator.

    A singleton: there is only ever row 1. Use SiteSettings.load() to read it -
    it creates the row with sensible defaults on first access, so a fresh
    install needs no fixture.
    """

    THEME_CHOICES = [("system", "Follow the device"), ("light", "Light"), ("dark", "Dark")]

    # --- Institution ------------------------------------------------------
    college_name = models.CharField(max_length=200, default="Navajyothi College")
    short_name = models.CharField(
        max_length=60, default="E-Navajyothi",
        help_text="Shown in the sidebar and browser tab.")
    tagline = models.CharField(max_length=200, blank=True)
    affiliated_to = models.CharField(max_length=200, blank=True,
                                     help_text="University the college is affiliated to.")
    principal_name = models.CharField(max_length=120, blank=True)

    # --- Contact ----------------------------------------------------------
    address = models.TextField(blank=True)
    city = models.CharField(max_length=80, blank=True)
    district = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=80, blank=True)
    pincode = models.CharField(max_length=10, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)

    # --- Branding ---------------------------------------------------------
    logo = models.ImageField(upload_to="branding/", blank=True, null=True,
                             help_text="Square works best. Shown in the sidebar and on printouts.")
    brand_color = models.CharField(max_length=7, default="#0d6b7c", validators=[HEX_COLOUR],
                                   help_text="Primary colour for navigation, buttons and links.")
    accent_color = models.CharField(max_length=7, default="#b8802a", validators=[HEX_COLOUR],
                                    help_text="Used sparingly for highlights.")
    default_theme = models.CharField(max_length=10, choices=THEME_CHOICES, default="system")
    login_background = models.ImageField(
        upload_to="branding/", blank=True, null=True,
        help_text="Photograph shown behind the sign-in card. A wide campus shot works well.")
    fee_payment_url = models.URLField(
        blank=True, verbose_name="Fee payment link",
        help_text="Shown as 'Pay Your Fee' on the sign-in page. Leave blank to hide the button.")

    # --- Academic rules ---------------------------------------------------
    attendance_threshold = models.PositiveSmallIntegerField(
        default=75, validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text="Minimum attendance percentage a student must hold.")
    current_session = models.ForeignKey(
        "Session", on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Batch selected by default on new forms and reports.")
    max_internal_mark = models.PositiveSmallIntegerField(default=20)
    max_exam_mark = models.PositiveSmallIntegerField(default=80)
    pass_percentage = models.PositiveSmallIntegerField(
        default=40, validators=[MinValueValidator(0), MaxValueValidator(100)])

    # --- Communication ----------------------------------------------------
    sms_enabled = models.BooleanField(
        default=False, help_text="Send SMS for notices. Needs Twilio credentials in .env.")
    whatsapp_enabled = models.BooleanField(default=False)
    push_enabled = models.BooleanField(default=False,
                                       help_text="Browser push notifications via Firebase.")
    notify_on_leave_decision = models.BooleanField(
        default=True, help_text="Tell the applicant when leave is approved or rejected.")

    # --- System -----------------------------------------------------------
    rows_per_page = models.PositiveSmallIntegerField(
        default=25, validators=[MinValueValidator(10), MaxValueValidator(200)])
    allow_student_profile_edit = models.BooleanField(
        default=True, help_text="Let students change their own name, address and photo.")
    allow_staff_profile_edit = models.BooleanField(default=True)
    footer_note = models.CharField(max_length=200, blank=True,
                                   help_text="Appears at the bottom of every page.")

    # Set by the first-run wizard. While False the whole site redirects to
    # /setup/; once True the wizard refuses to run again, so the installer
    # cannot be used to mint a second administrator.
    setup_complete = models.BooleanField(default=False)
    setup_step = models.PositiveSmallIntegerField(default=1)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self):
        return self.college_name

    def save(self, *args, **kwargs):
        self.pk = 1              # singleton: always row 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass                     # the settings row is not deletable

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def full_address(self):
        parts = [self.address, self.city, self.district, self.state, self.pincode]
        return ", ".join(p for p in parts if p)


class CustomUser(AbstractUser):
    USER_TYPE = ((1, "HOD"), (2, "Staff"), (3, "Student"))
    GENDER = [("M", "Male"), ("F", "Female")]


    username = None  # Removed username, using email instead
    email = models.EmailField(unique=True)
    user_type = models.CharField(default=1, choices=USER_TYPE, max_length=1)
    gender = models.CharField(max_length=1, choices=GENDER)
    profile_pic = models.ImageField()

    address = models.TextField()
    fcm_token = models.TextField(default="")  # For firebase notifications
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = CustomUserManager()
    phone_num = models.CharField(max_length=15, null=True, blank=True)
    aadhar_num = models.CharField(max_length=12, null=True, blank=True)
    date_of_birth = models.DateField(null=True,blank=False)

    def __str__(self):
        return self.first_name + "  " + self.last_name


class Admin(models.Model):
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)

class Department(models.Model):
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

class Course(models.Model):
    name = models.CharField(max_length=120)
    department = models.ForeignKey(Department, on_delete=models.DO_NOTHING, null=True, blank=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Student(models.Model):

    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)
    department = models.ForeignKey(Department, on_delete=models.DO_NOTHING, null=True, blank=False)
    course = models.ForeignKey(Course, on_delete=models.DO_NOTHING, null=True, blank=False)
    session = models.ForeignKey(Session, on_delete=models.DO_NOTHING, null=True)
    phone_num = models.CharField(max_length=15, null=True, blank=True)
    aadhar_num = models.CharField(max_length=12, null=True, blank=True)
    date_of_birth = models.DateField(null=True,blank=False)
    religion = models.CharField(max_length=12,null=True,blank=False)
    register_num = models.CharField(max_length=12,null=True,blank=False)
    admission_num = models.CharField(max_length=16,null=True,blank=False)


    def __str__(self):
        return self.admin.first_name + ", " + self.admin.last_name


class Staff(models.Model):
    course = models.ForeignKey(Course, on_delete=models.DO_NOTHING, null=True, blank=False)
    department = models.ForeignKey(Department, on_delete=models.DO_NOTHING, null=True, blank=False)
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)
    phone_num = models.CharField(max_length=15, null=True, blank=True)
    qualification = models.CharField(max_length=20,null=True,blank=False)
    aadhar_num = models.CharField(max_length=12, null=True, blank=True)
    def __str__(self):
        return self.admin.first_name + " " + self.admin.last_name


class Subject(models.Model):
    name = models.CharField(max_length=120)
    staff = models.ForeignKey(Staff,on_delete=models.CASCADE,)
    department = models.ForeignKey(Department, on_delete=models.DO_NOTHING, null=True, blank=False)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    session = models.ForeignKey(Session, on_delete=models.DO_NOTHING, null=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Attendance(models.Model):
    session = models.ForeignKey(Session, on_delete=models.DO_NOTHING)
    subject = models.ForeignKey(Subject, on_delete=models.DO_NOTHING)
    date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class AttendanceReport(models.Model):
    student = models.ForeignKey(Student, on_delete=models.DO_NOTHING)
    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE)
    status = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class LeaveReportStudent(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    date = models.CharField(max_length=60)
    message = models.TextField()
    status = models.SmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class LeaveReportStaff(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    date = models.CharField(max_length=60)
    message = models.TextField()
    status = models.SmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class FeedbackStudent(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    feedback = models.TextField()
    reply = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class FeedbackStaff(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    feedback = models.TextField()
    reply = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class NotificationStaff(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


    def __str__(self):
        return self.message


class NotificationStudent(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.message


class StudentResult(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    test = models.FloatField(default=0)
    exam = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


@receiver(post_save, sender=CustomUser)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        if instance.user_type == 1:
            Admin.objects.create(admin=instance)
        if instance.user_type == 2:
            Staff.objects.create(admin=instance)
        if instance.user_type == 3:
            Student.objects.create(admin=instance)


@receiver(post_save, sender=CustomUser)
def save_user_profile(sender, instance, **kwargs):
    if instance.user_type == 1:
        instance.admin.save()
    if instance.user_type == 2:
        instance.staff.save()
    if instance.user_type == 3:
        instance.student.save()
