from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.urls import include, path, re_path
from django.views.static import serve


def serve_media(request: HttpRequest, path: str) -> HttpResponse:
    """Serve ``MEDIA_ROOT/<path>`` under ``MEDIA_URL``.

    The API hands the frontend relative URLs like
    ``/media/artifacts/<job>/clip_0_v1.mp4`` (SPEC §9.3) and
    ``/media/packages/<zip>`` — without this route every preview and the
    "Download All" link 404. The MVP has no nginx in front of Django, so
    this is intentionally not gated on DEBUG; swap for a reverse proxy /
    S3 before real production traffic.

    ``MEDIA_ROOT`` is read per request (not captured at import) so
    ``override_settings`` in tests and env changes at boot both apply.

    Files under MEDIA_ROOT are user-supplied (uploads, logos). Django's
    ``serve`` already refuses ``..`` traversal; on top of that we forbid
    the browser from sniffing a type and from running anything the file
    might contain (CSP ``sandbox``), so a crafted upload can't become a
    same-origin XSS.
    """
    from django.http import Http404

    from services.access import can_access_media_path

    if not can_access_media_path(request, path):
        raise Http404("not yours")
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "sandbox; default-src 'none'; media-src 'self'; img-src 'self'"
    return response


urlpatterns = [
    path("api/", include("api.urls")),
    re_path(r"^media/(?P<path>.*)$", serve_media, name="media"),
]
