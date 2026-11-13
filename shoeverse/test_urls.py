from django.urls import include, path

urlpatterns = [
    path("", include("users.urls")),
    path("", include("shop.urls")),
    path("wallet/", include("wallet.urls")),
    path("support/", include("support.urls")),
]
