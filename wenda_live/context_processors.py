"""Template context processors for Wenda-Live."""

from django.conf import settings


def wenda_quiz_url(request):
    """Expose the sibling Wenda-Quiz base URL to every template (navbar link)."""
    return {'WENDA_QUIZ_URL': settings.WENDA_QUIZ_URL}
