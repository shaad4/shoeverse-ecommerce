"""
URL configuration for shoeverse project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import render
from django.conf.urls import handler404

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('users.urls')),
    path('accounts/', include('allauth.urls')),
    path("adminpanel/", include("adminpanel.urls")),
    path("", include("shop.urls")),
    path('wallet/', include('wallet.urls')),
    path('support/', include('support.urls')),



]


# Cloudinary storage does not configure a local media URL or directory.
if settings.DEBUG and settings.MEDIA_URL and settings.MEDIA_ROOT:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

def custom_404(request, exception):
    return render(request, "404.html", status=404)

handler404 = "shoeverse.urls.custom_404"