"""Real services for ``pytest -m integration --ds=tests.integration_settings`` (see CONTRIBUTING.md)."""

import os

from tests.settings import *  # noqa: F403

_PG = {
    'ENGINE': 'django.db.backends.postgresql',
    'HOST': os.environ.get('OFFSITE_BACKUP_TEST_PG_HOST', '127.0.0.1'),
    'PORT': os.environ.get('OFFSITE_BACKUP_TEST_PG_PORT', '5432'),
    'USER': os.environ.get('OFFSITE_BACKUP_TEST_PG_USER', 'ob'),
    'PASSWORD': os.environ.get('OFFSITE_BACKUP_TEST_PG_PASSWORD', 'ob'),
}
DATABASES = {
    'default': {**_PG, 'NAME': os.environ.get('OFFSITE_BACKUP_TEST_PG_SOURCE', 'source')},
    'target': {**_PG, 'NAME': os.environ.get('OFFSITE_BACKUP_TEST_PG_TARGET', 'target')},
}
