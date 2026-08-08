"""First-run installation wizard.

Reachable only while SiteSettings.setup_complete is False - the gate lives in
middleware.py. Each step commits its own part, so an interrupted install
resumes where it stopped instead of starting over, and `setup_step` records how
far the installer got.

Deliberately does NOT create demo data or academic records: a college's first
administrator should land on an empty, honest system.
"""

from django import forms
from django.contrib import messages
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.shortcuts import redirect, render
from django.urls import reverse

from .forms import FormSettings, SiteSettingsForm
from .models import CustomUser, SiteSettings

TOTAL_STEPS = 3


class AdministratorForm(forms.Form):
    """Creates the first superuser. Not a ModelForm: the confirmation field and
    the password rules are specific to installation."""

    first_name = forms.CharField(label="First name", max_length=60)
    last_name = forms.CharField(label="Last name", max_length=60)
    email = forms.EmailField(
        label="Email address",
        help_text="This is the username you will sign in with.")
    password = forms.CharField(label="Password", widget=forms.PasswordInput,
                               help_text="At least 8 characters.")
    confirm = forms.CharField(label="Confirm password", widget=forms.PasswordInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"
        self.fields["email"].widget.attrs["autocomplete"] = "username"
        for name in ("password", "confirm"):
            self.fields[name].widget.attrs["autocomplete"] = "new-password"

    def clean_email(self):
        email = self.cleaned_data["email"].lower().strip()
        if CustomUser.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with that email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        password, confirm = cleaned.get("password"), cleaned.get("confirm")
        if password and confirm and password != confirm:
            self.add_error("confirm", "The two passwords do not match.")
        if password:
            # AUTH_PASSWORD_VALIDATORS is deliberately emptied under DEBUG for
            # local demo accounts. The installation password must not slip
            # through that hole, so validate explicitly here.
            try:
                validate_password(password)
            except ValidationError as exc:
                for message in exc.messages:
                    self.add_error("password", message)
            if len(password) < 8:
                self.add_error("password", "Use at least 8 characters.")
        return cleaned

    def create_administrator(self):
        data = self.cleaned_data
        # create_superuser fires the post_save signal that builds the linked
        # Admin profile, so no extra work is needed here.
        return CustomUser.objects.create_superuser(
            email=data["email"], password=data["password"],
            user_type=1, first_name=data["first_name"], last_name=data["last_name"])


class InstitutionForm(SiteSettingsForm):
    """The institution and branding parts of the settings form, nothing else."""

    WIZARD_FIELDS = [
        "college_name", "short_name", "tagline", "affiliated_to",
        "city", "district", "state", "phone", "email",
        "logo", "brand_color", "accent_color",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in list(self.fields):
            if name not in self.WIZARD_FIELDS:
                self.fields.pop(name)

    def sections(self):
        yield ("institution", "Institution", "fas fa-building",
               [self[n] for n in ("college_name", "short_name", "tagline", "affiliated_to")])
        yield ("contact", "Contact", "fas fa-address-book",
               [self[n] for n in ("city", "district", "state", "phone", "email")])
        yield ("branding", "Branding", "fas fa-palette",
               [self[n] for n in ("logo", "brand_color", "accent_color")])


def _database_ready():
    try:
        connection.ensure_connection()
        return True, connection.vendor
    except Exception as exc:
        return False, str(exc)


def setup(request):
    """Single entry point; `step` decides which screen is shown."""
    settings_obj = SiteSettings.load()
    step = request.GET.get("step") or request.POST.get("step") or settings_obj.setup_step
    try:
        step = max(1, min(int(step), TOTAL_STEPS))
    except (TypeError, ValueError):
        step = 1

    handler = {1: _step_welcome, 2: _step_administrator, 3: _step_institution}[step]
    return handler(request, settings_obj)


def _render(request, template, step, extra):
    context = {"step": step, "total_steps": TOTAL_STEPS,
               "page_title": "Set up your college"}
    context.update(extra)
    return render(request, f"setup/{template}", context)


def _step_welcome(request, settings_obj):
    ready, detail = _database_ready()
    has_admin = CustomUser.objects.filter(is_superuser=True).exists()
    if request.method == "POST" and ready:
        settings_obj.setup_step = 3 if has_admin else 2
        settings_obj.save()
        return redirect(f"{reverse('setup')}?step={settings_obj.setup_step}")
    return _render(request, "welcome.html", 1,
                   {"db_ready": ready, "db_detail": detail, "has_admin": has_admin})


def _step_administrator(request, settings_obj):
    if CustomUser.objects.filter(is_superuser=True).exists():
        # Resumed install: the account already exists, do not offer a second.
        settings_obj.setup_step = 3
        settings_obj.save()
        return redirect(f"{reverse('setup')}?step=3")

    form = AdministratorForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            form.create_administrator()
            settings_obj.setup_step = 3
            settings_obj.save()
        messages.success(request, "Administrator account created.")
        return redirect(f"{reverse('setup')}?step=3")
    return _render(request, "administrator.html", 2, {"form": form})


def _step_institution(request, settings_obj):
    form = InstitutionForm(request.POST or None, request.FILES or None,
                           instance=settings_obj)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.setup_complete = True      # closes the wizard for good
            obj.setup_step = 99
            obj.save()
        messages.success(
            request, "Setup complete. Sign in with the administrator account you just created.")
        return redirect(reverse("login_page"))
    return _render(request, "institution.html", 3, {"form": form})
