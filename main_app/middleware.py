from django.shortcuts import redirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin

# Framework views every signed-in user may reach: auth (password reset), and
# the static/media serving views the dev URLconf wires up.
ALWAYS_ALLOWED = {
    "django.contrib.auth.views",
    "django.views.static",
    "django.contrib.staticfiles.views",
}

# Views shared by every role - login, logout, FCM tokens, attendance lookups.
SHARED_MODULES = {"main_app.views"}

# The first-run installer. Reachable only while setup is incomplete.
SETUP_MODULE = "main_app.setup_views"

# What each role may reach. Administrators keep the access to the staff pages
# they had before (they review student leave through a staff_views view).
ROLE_MODULES = {
    "1": {"main_app.hod_views", "main_app.roster_views",
          "main_app.staff_views", "main_app.EditResultView"},
    "2": {"main_app.staff_views", "main_app.EditResultView"},
    "3": {"main_app.student_views"},
}

HOME_FOR_ROLE = {"1": "admin_home", "2": "staff_home", "3": "student_home"}


class LoginCheckMiddleWare(MiddlewareMixin):
    """Route users to their own area.

    This used to be a blocklist: it named the modules each role could *not*
    reach, so any module nobody thought to list - EditResultView, and every
    module added later - was reachable by everyone. A student could POST to
    the staff result editor and rewrite their own marks. It is now an
    allowlist: anything not explicitly granted is denied.
    """

    def process_view(self, request, view_func, view_args, view_kwargs):
        module = view_func.__module__
        user = request.user

        # --- first-run installation gate ---------------------------------
        # Until the wizard has been completed the whole site is the wizard;
        # once it has, the wizard is permanently closed. That second half is
        # the security-relevant one: it stops the installer being replayed to
        # create another superuser.
        if module in ALWAYS_ALLOWED:
            return None
        try:
            from .models import SiteSettings
            setup_done = SiteSettings.load().setup_complete
        except Exception:
            # Database not migrated yet - let the request through so the
            # error page explains the real problem.
            setup_done = True

        if not setup_done:
            return None if module == SETUP_MODULE else redirect(reverse("setup"))
        if module == SETUP_MODULE:
            return redirect(reverse("login_page"))

        if not user.is_authenticated:
            # Only the login page and the auth views are open to anonymous users.
            if (request.path == reverse("landing")
                    or request.path == reverse("login_page")
                    or request.path == reverse("user_login")
                    or module in ALWAYS_ALLOWED):
                return None
            return redirect(reverse("login_page"))

        if module in ALWAYS_ALLOWED or module in SHARED_MODULES:
            return None

        # The Django admin site stays available to superusers only.
        if module.startswith("django.contrib.admin"):
            return None if user.is_superuser else self._send_home(user)

        allowed = ROLE_MODULES.get(str(user.user_type))
        if allowed is None:
            return redirect(reverse("login_page"))
        if module in allowed:
            return None
        return self._send_home(user)

    @staticmethod
    def _send_home(user):
        return redirect(reverse(HOME_FOR_ROLE.get(str(user.user_type), "login_page")))
