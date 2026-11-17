"""``path("mailko/", include("mailko.contrib.django.urls"))`` → ``/mailko/webhook/``."""

from django.urls import path

from mailko.contrib.django.views import webhook_view

app_name = "mailko"

urlpatterns = [path("webhook/", webhook_view, name="webhook")]
