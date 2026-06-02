"""Django settings for configWendaLive.

Wenda-Live is a sibling app to wenda-quiz that reuses the same MySQL database
to read questions from the shared question_bank. Authentication reuses wenda's
quiz.User table (via an unmanaged mirror in the wenda_live app), but Wenda-Live
keeps its own session/login flow.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')


SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'django-insecure-dev-only-change-me')

DEBUG = os.getenv('DJANGO_DEBUG', '0').lower() in ('1', 'true', 'yes', 'on')

allowed_hosts = os.getenv('DJANGO_ALLOWED_HOSTS', '')
ALLOWED_HOSTS = [host.strip() for host in allowed_hosts.split(',') if host.strip()]
if DEBUG and not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ['localhost', '127.0.0.1']

csrf_trusted_origins = os.getenv('DJANGO_CSRF_TRUSTED_ORIGINS', '')
CSRF_TRUSTED_ORIGINS = [
    origin.strip() for origin in csrf_trusted_origins.split(',') if origin.strip()
]

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# App-specific cookie names so Wenda-Live keeps its own session even when it
# shares a hostname with wenda-quiz (e.g. both on 127.0.0.1 in dev, or a shared
# domain in prod). Without this they'd both use Django's default `sessionid`
# and clobber each other's login. Changing these logs existing users out once.
SESSION_COOKIE_NAME = 'wendalive_sessionid'
CSRF_COOKIE_NAME = 'wendalive_csrftoken'


# Application definition

INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'channels',
    'wenda_live',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'configWendaLive.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# Daphne (ASGI) is the primary server; WSGI kept for compatibility.
WSGI_APPLICATION = 'configWendaLive.wsgi.application'
ASGI_APPLICATION = 'configWendaLive.asgi.application'


DATABASES = {
    'default': {
        'ENGINE': os.getenv('DB_ENGINE', 'django.db.backends.mysql'),
        'NAME': os.getenv('DB_NAME', 'wenda_db'),
        'USER': os.getenv('DB_USER', 'root'),
        'PASSWORD': os.getenv('DB_PASSWORD', ''),
        'HOST': os.getenv('DB_HOST', '127.0.0.1'),
        'PORT': os.getenv('DB_PORT', '3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
        },
    }
}


# Use Wenda-Live's mirror of wenda's User table (same row, separate model).
AUTH_USER_MODEL = 'wenda_live.User'

# Tests build an isolated test DB from the models (see wenda_live/test_runner.py),
# so they never touch the shared wenda_db.
TEST_RUNNER = 'wenda_live.test_runner.WendaLiveTestRunner'


# Channel layers: in-memory for dev, Redis for prod (set CHANNEL_REDIS_URL).
channel_redis_url = os.getenv('CHANNEL_REDIS_URL', '').strip()
if channel_redis_url:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {'hosts': [channel_redis_url]},
        }
    }
else:
    CHANNEL_LAYERS = {
        'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'},
    }


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# After a successful Wenda-Live login, send hosts to the game-create page.
LOGIN_URL = 'wenda_live:login'
LOGIN_REDIRECT_URL = 'wenda_live:host_create_game'
LOGOUT_REDIRECT_URL = 'wenda_live:home'
