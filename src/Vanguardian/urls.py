"""
URL configuration for Vanguardian project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
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

from django.conf import settings
from django.http import HttpResponse
from django.urls import include, path

from apps.shared.navigation import redirect_to_default_application_page


def robots_txt(_request):
    if getattr(settings, "SEARCH_ENGINE_INDEXING_ENABLED", False):
        content = "User-agent: *\nAllow: /\n"
    else:
        content = "User-agent: *\nDisallow: /\n"
    return HttpResponse(content, content_type="text/plain; charset=utf-8")

urlpatterns = [
    path("i18n/", include("django.conf.urls.i18n")),
    path("robots.txt", robots_txt),
    path("", redirect_to_default_application_page),
    path("api/", include("apps.crf.presentation.api.urls")),
    path("crf/", include("apps.crf.presentation.web.urls")),
    path("api/", include("apps.datacapture.presentation.api.urls")),
    path("api/", include("apps.study.presentation.api.urls")),
    path("api/", include("apps.reconcile.presentation.api.urls")),
    path("", include("apps.identity.presentation.web.urls")),
    path("", include("apps.dashboard.presentation.web.urls")),
    path("", include("apps.study.presentation.web.urls")),
    path("", include("apps.subject.presentation.web.urls")),
    path("", include("apps.reconcile.presentation.web.urls")),
    # path('admin/', admin.site.urls),
]
