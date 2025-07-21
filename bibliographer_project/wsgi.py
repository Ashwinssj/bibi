# bibliographer_project/wsgi.py
"""
WSGI config for bibliographer_project project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application
from whitenoise.middleware import WhiteNoise as DjangoWhiteNoise # Import WhiteNoise (renamed for compatibility)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "bibliographer_project.settings")

application = get_wsgi_application()
application = DjangoWhiteNoise(application) # Wrap your WSGI application with WhiteNoise

