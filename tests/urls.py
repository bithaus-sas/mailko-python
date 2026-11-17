from django.urls import include, path

urlpatterns = [path("mailko/", include("mailko.contrib.django.urls"))]
