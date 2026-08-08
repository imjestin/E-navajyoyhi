from .models import SiteSettings


def site_settings(request):
    """Expose the institution settings to every template as `site`.

    Wrapped defensively: this runs on every render, including error pages and
    the very first request before migrations have been applied, and a failure
    here would take down pages that have nothing to do with settings.
    """
    try:
        return {"site": SiteSettings.load()}
    except Exception:
        return {"site": None}
