from django import forms
from django.core.validators import RegexValidator
from django.forms.widgets import DateInput

from .models import (Admin, Course, CustomUser, Department, FeedbackStaff,
                     FeedbackStudent, LeaveReportStaff, LeaveReportStudent,
                     Session, SiteSettings, Staff, Student, StudentResult,
                     Subject)

# Reused so the Add/Edit screens enforce exactly what the CSV importer does.
phone_validator = RegexValidator(
    r"^\d{10}$", "Enter a 10 digit phone number, digits only.")
aadhaar_validator = RegexValidator(
    r"^\d{12}$", "An Aadhaar number is exactly 12 digits.")

GENDER_CHOICES = [("", "Select..."), ("M", "Male"), ("F", "Female")]


class FormSettings(forms.ModelForm):
    """Applies the shared field styling and marks required fields."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.visible_fields():
            widget = field.field.widget
            css = widget.attrs.get("class", "")
            if not isinstance(widget, (forms.CheckboxInput, forms.RadioSelect)):
                widget.attrs["class"] = (css + " form-control").strip()
            if field.field.required:
                widget.attrs["required"] = "required"


class CustomUserForm(FormSettings):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={"placeholder": "name@example.com",
                                       "autocomplete": "email"}))
    # Optional on purpose. It used to be required, which meant an account
    # created without one - every account the setup wizard makes - could not
    # save any profile change at all, including uploading a photograph: the
    # form failed validation and silently discarded the file.
    gender = forms.ChoiceField(choices=GENDER_CHOICES, required=False)
    first_name = forms.CharField(required=True,
                                 widget=forms.TextInput(attrs={"autocomplete": "given-name"}))
    last_name = forms.CharField(required=True,
                                widget=forms.TextInput(attrs={"autocomplete": "family-name"}))
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    profile_pic = forms.ImageField(
        required=True, label="Photograph", help_text="JPG or PNG.",
        widget=forms.ClearableFileInput(attrs={"accept": "image/*"}))
    phone_num = forms.CharField(
        validators=[phone_validator], required=False, label="Phone number",
        widget=forms.TextInput(attrs={"placeholder": "9876543210", "inputmode": "numeric",
                                      "maxlength": "10", "autocomplete": "tel"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `instance` may arrive positionally, so read it off the form, not kwargs.
        if self.instance is not None and self.instance.pk is not None:
            user = getattr(self.instance, "admin", None)
            if user is not None:
                # Only seed fields that live on CustomUser and NOT on this
                # form's own model. Student and Staff carry their own
                # phone_num / aadhar_num / date_of_birth, and those are the
                # authoritative copies - ModelForm already fills them in.
                own_fields = {f.name for f in self._meta.model._meta.get_fields()}
                for name in self.fields:
                    if name not in own_fields and hasattr(user, name):
                        self.fields[name].initial = getattr(user, name)
            # On edit, both are "only if you want to change it".
            self.fields["password"].required = False
            self.fields["password"].widget.attrs.pop("required", None)
            self.fields["password"].widget.attrs["placeholder"] = "Leave blank to keep current password"
            self.fields["profile_pic"].required = False
            self.fields["profile_pic"].widget.attrs.pop("required", None)
            self.fields["profile_pic"].help_text = "Leave empty to keep the current photo."

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        qs = CustomUser.objects.filter(email__iexact=email)
        if self.instance is not None and self.instance.pk is not None:
            user = getattr(self.instance, "admin", None)
            if user is not None:
                qs = qs.exclude(pk=user.pk)
        if qs.exists():
            raise forms.ValidationError("That email address is already registered.")
        return email

    def clean_password(self):
        password = self.cleaned_data.get("password") or ""
        if password and len(password) < 8:
            raise forms.ValidationError("Use at least 8 characters.")
        return password

    class Meta:
        model = CustomUser
        fields = ['first_name', 'last_name', 'email', 'gender', 'phone_num',
                  'password', 'profile_pic', 'address']


class StudentForm(CustomUserForm):
    aadhar_num = forms.CharField(
        validators=[aadhaar_validator], required=False, label="Aadhaar number",
        widget=forms.TextInput(attrs={"placeholder": "12 digits", "inputmode": "numeric",
                                      "maxlength": "12"}))
    register_num = forms.CharField(required=False, label="Register number")
    admission_num = forms.CharField(required=False, label="Admission number")

    def clean(self):
        """A course must belong to the chosen department."""
        cleaned = super().clean()
        department = cleaned.get("department")
        course = cleaned.get("course")
        if department and course and course.department_id and course.department_id != department.id:
            self.add_error(
                "course",
                f"{course.name} belongs to {course.department}, not {department}.")
        return cleaned

    def clean_register_num(self):
        value = (self.cleaned_data.get("register_num") or "").strip()
        if value:
            qs = Student.objects.filter(register_num=value)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError("That register number is already in use.")
        return value

    class Meta(CustomUserForm.Meta):
        model = Student
        fields = CustomUserForm.Meta.fields + \
            ['religion', 'date_of_birth', 'aadhar_num', 'department', 'course',
             'session', 'admission_num', 'register_num']
        widgets = {
            'date_of_birth': DateInput(attrs={'type': 'date'}),
        }


class AdminForm(CustomUserForm):
    class Meta(CustomUserForm.Meta):
        model = Admin
        fields = CustomUserForm.Meta.fields


class StaffForm(CustomUserForm):
    aadhar_num = forms.CharField(
        validators=[aadhaar_validator], required=False, label="Aadhaar number",
        widget=forms.TextInput(attrs={"placeholder": "12 digits", "inputmode": "numeric",
                                      "maxlength": "12"}))

    class Meta(CustomUserForm.Meta):
        model = Staff
        fields = CustomUserForm.Meta.fields + ['aadhar_num', 'department', 'qualification']


class SearchForm(forms.Form):
    search_query = forms.CharField(
        max_length=100, required=False, label=False,
        widget=forms.TextInput(attrs={'placeholder': 'Search by name or phone number'}))


class DepartmentForm(FormSettings):
    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        qs = Department.objects.filter(name__iexact=name)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A department with that name already exists.")
        return name

    class Meta:
        model = Department
        fields = ['name']


class CourseForm(FormSettings):
    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        qs = Course.objects.filter(name__iexact=name)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A course with that name already exists.")
        return name

    class Meta:
        model = Course
        fields = ['name', 'department']


class SubjectForm(FormSettings):
    class Meta:
        model = Subject
        fields = ['name', 'staff', 'course', 'session']


class SessionForm(FormSettings):
    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_year"), cleaned.get("end_year")
        if start and end and end <= start:
            self.add_error("end_year", "The end date must be after the start date.")
        return cleaned

    class Meta:
        model = Session
        fields = '__all__'
        widgets = {
            'start_year': DateInput(attrs={'type': 'date'}),
            'end_year': DateInput(attrs={'type': 'date'}),
        }


class LeaveReportStaffForm(FormSettings):
    class Meta:
        model = LeaveReportStaff
        fields = ['date', 'message']
        widgets = {'date': DateInput(attrs={'type': 'date'})}


class FeedbackStaffForm(FormSettings):
    class Meta:
        model = FeedbackStaff
        fields = ['feedback']
        widgets = {'feedback': forms.Textarea(attrs={'rows': 4})}


class LeaveReportStudentForm(FormSettings):
    class Meta:
        model = LeaveReportStudent
        fields = ['date', 'message']
        widgets = {'date': DateInput(attrs={'type': 'date'})}


class FeedbackStudentForm(FormSettings):
    class Meta:
        model = FeedbackStudent
        fields = ['feedback']
        widgets = {'feedback': forms.Textarea(attrs={'rows': 4})}


class StudentEditForm(CustomUserForm):
    class Meta(CustomUserForm.Meta):
        model = Student
        fields = CustomUserForm.Meta.fields


class StaffEditForm(CustomUserForm):
    class Meta(CustomUserForm.Meta):
        model = Staff
        fields = CustomUserForm.Meta.fields


class SiteSettingsForm(FormSettings):
    """Institution settings. `SECTIONS` drives the tabbed layout in the template."""

    SECTIONS = [
        ("institution", "Institution", "fas fa-building", [
            "college_name", "short_name", "tagline", "affiliated_to", "principal_name"]),
        ("contact", "Contact", "fas fa-address-book", [
            "address", "city", "district", "state", "pincode",
            "phone", "email", "website"]),
        ("branding", "Branding", "fas fa-palette", [
            "logo", "remove_logo", "login_background", "remove_login_background",
            "brand_color", "accent_color", "default_theme", "fee_payment_url"]),
        ("academic", "Academic rules", "fas fa-graduation-cap", [
            "attendance_threshold", "current_session", "max_internal_mark",
            "max_exam_mark", "pass_percentage"]),
        ("communication", "Notifications", "fas fa-bullhorn", [
            "sms_enabled", "whatsapp_enabled", "push_enabled", "notify_on_leave_decision"]),
        ("system", "System", "fas fa-sliders-h", [
            "rows_per_page", "allow_student_profile_edit",
            "allow_staff_profile_edit", "footer_note"]),
    ]

    # Rendered as checkboxes beside each image; handled in save().
    remove_logo = forms.BooleanField(
        required=False, label="Remove the logo and use the default")
    remove_login_background = forms.BooleanField(
        required=False, label="Remove the photograph and use the default")

    IMAGE_FIELDS = {"logo": "remove_logo",
                    "login_background": "remove_login_background"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("brand_color", "accent_color"):
            self.fields[name].widget = forms.TextInput(
                attrs={"type": "color", "class": "form-control colour-input"})
        self.fields["address"].widget = forms.Textarea(attrs={"rows": 3, "class": "form-control"})
        self.fields["current_session"].empty_label = "No default batch"

        # Plain file inputs: the preview and the remove checkbox are rendered
        # by the template, so ClearableFileInput's own markup is not wanted.
        for name in self.IMAGE_FIELDS:
            self.fields[name].widget = forms.FileInput(
                attrs={"class": "form-control", "accept": "image/*"})
            # Only offer "remove" when there is something to remove.
            if not getattr(self.instance, name, None):
                self.fields.pop(self.IMAGE_FIELDS[name], None)

    def save(self, commit=True):
        obj = super().save(commit=False)
        for field, remove_flag in self.IMAGE_FIELDS.items():
            if self.cleaned_data.get(remove_flag):
                getattr(obj, field).delete(save=False)
                setattr(obj, field, None)
        if commit:
            obj.save()
        return obj

    def clean(self):
        cleaned = super().clean()
        # Subclasses (the setup wizard) render only a subset of the fields;
        # add_error() raises if the field is not on the form.
        if "max_exam_mark" in self.fields and "max_internal_mark" in self.fields:
            internal = cleaned.get("max_internal_mark") or 0
            exam = cleaned.get("max_exam_mark") or 0
            if internal + exam == 0:
                self.add_error("max_exam_mark",
                               "Internal and exam marks cannot both be zero.")
        return cleaned

    def sections(self):
        """Yield (key, label, icon, [bound fields]) for the template."""
        for key, label, icon, names in self.SECTIONS:
            yield key, label, icon, [self[n] for n in names if n in self.fields]

    class Meta:
        model = SiteSettings
        exclude = ["updated_at"]


class EditResultForm(FormSettings):
    session_year = forms.ModelChoiceField(
        label="Session Year", queryset=Session.objects.none(), required=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Resolved per request rather than once at import, so a session added
        # today shows up without restarting the server.
        self.fields['session_year'].queryset = Session.objects.all()

    class Meta:
        model = StudentResult
        fields = ['session_year', 'subject', 'student', 'test', 'exam']
