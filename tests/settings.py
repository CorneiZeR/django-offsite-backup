SECRET_KEY = 'tests'
INSTALLED_APPS = ['offsite_backup']
DATABASES: dict[str, dict[str, str]] = {}
USE_TZ = True
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
