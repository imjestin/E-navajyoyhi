from django.db import migrations


def mark_existing_installs(apps, schema_editor):
    """An install that already has an administrator has clearly been set up.

    Without this, upgrading an existing deployment would send the whole site to
    the first-run wizard - and the wizard would happily create a second
    superuser. Fresh installs have no users, so they correctly stay at step 1.
    """
    CustomUser = apps.get_model("main_app", "CustomUser")
    SiteSettings = apps.get_model("main_app", "SiteSettings")

    if not CustomUser.objects.filter(is_superuser=True).exists():
        return

    settings_obj = SiteSettings.objects.first()
    if settings_obj is None:
        settings_obj = SiteSettings(pk=1)
    settings_obj.pk = 1
    settings_obj.setup_complete = True
    settings_obj.setup_step = 99
    settings_obj.save()


def unmark(apps, schema_editor):
    SiteSettings = apps.get_model("main_app", "SiteSettings")
    SiteSettings.objects.filter(pk=1).update(setup_complete=False, setup_step=1)


class Migration(migrations.Migration):

    dependencies = [
        ("main_app", "0036_sitesettings_setup_complete_sitesettings_setup_step_and_more"),
    ]

    operations = [
        migrations.RunPython(mark_existing_installs, unmark),
    ]
