"""Focused commerce tests without SMTP, Cloudinary, or optional PDF/Cairo services."""
import os
from .settings import *  # noqa: F403

SECRET_KEY = "test-only-key-never-use-in-production"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
# The focused suite imports the real customer routes, excluding PDF/admin routes
# which require native Cairo libraries. Use PostgreSQL to exercise row locking.
ROOT_URLCONF = "shoeverse.test_urls"
if os.getenv("TEST_POSTGRES", "false").lower() != "true":
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
RAZORPAY_KEY_ID = "test-key"
RAZORPAY_KEY_SECRET = "test-secret"
HOMEPAGE_FEATURED_PRODUCT_IDS = []
