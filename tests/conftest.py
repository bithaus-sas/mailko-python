import django
import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=False,
        SECRET_KEY="tests",
        ALLOWED_HOSTS=["testserver"],
        ROOT_URLCONF="tests.urls",
        INSTALLED_APPS=["django.contrib.contenttypes", "django.contrib.auth"],
        MIDDLEWARE=["django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware"],
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        DATABASES={},
        EMAIL_BACKEND="mailko.contrib.django.EmailBackend",
        DEFAULT_FROM_EMAIL="Tienda <pedidos@tienda.co>",
        MAILKO_API_KEY="mk_test_default",
        MAILKO_API_URL="https://api.mailko.test",
        USE_TZ=True,
    )
    django.setup()

API = "https://api.mailko.test/v1"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Retries wait nothing; the waits asked for are kept to be checked."""
    waits = []
    monkeypatch.setattr("mailko.client.time.sleep", waits.append)
    return waits


@pytest.fixture(autouse=True)
def clear_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()
