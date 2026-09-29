from django.contrib import admin
from django.conf import settings
from django.urls import include, path, re_path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("photopholio.urls")),
    # Serve uploaded media. Fine for a demo/portfolio deployment;
    # for real traffic use S3 / Cloudinary instead.
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
]
