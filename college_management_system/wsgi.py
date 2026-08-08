"""
WSGI config for the SkillyCMS project.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'college_management_system.settings')

application = get_wsgi_application()

# --- Serve uploaded files in production ----------------------------------
# WhiteNoise (via the middleware) serves STATIC_ROOT, but uploaded logos and
# photographs live in MEDIA_ROOT. Django's `static()` URL helper only returns
# patterns when DEBUG is on - documented as "for serving files in debug mode" -
# so with DEBUG=False nothing served /media/ and every uploaded image 404'd.
#
# autorefresh=True is required, not optional: without it WhiteNoise scans the
# directory once at start-up, and anything uploaded afterwards would 404 until
# the container was restarted.
from django.conf import settings  # noqa: E402  (must follow get_wsgi_application)
from whitenoise import WhiteNoise  # noqa: E402

os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
application = WhiteNoise(application, autorefresh=True)
application.add_files(settings.MEDIA_ROOT, prefix=settings.MEDIA_URL)
