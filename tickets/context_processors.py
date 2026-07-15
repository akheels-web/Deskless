from django.conf import settings


def branding(request):
    return {
        "brand_name": settings.BRAND_NAME,
        "brand_color": settings.BRAND_COLOR,
        "brand_accent": settings.BRAND_ACCENT,
    }
