from django.conf import settings

from .models import OrgSettings


def sso(request):
    """Expose SSO providers that an admin has actually configured (has a SocialApp).
    Buttons only show once credentials exist — no dead buttons.
    """
    providers = []
    try:
        from allauth.socialaccount.models import SocialApp
        from allauth.socialaccount.providers import registry
        for app in SocialApp.objects.all():
            provider = registry.get_class(app.provider)
            name = provider.name if provider else app.provider.title()
            providers.append({
                "name": name,
                "login_url": f"/accounts/{app.provider}/login/",
            })
    except Exception:
        pass
    return {"sso_providers": providers}


def branding(request):
    """DB org settings win; fall back to env-configured defaults."""
    try:
        org = OrgSettings.load()
    except Exception:  # e.g. before migrations run
        org = None
    if org:
        return {
            "brand_name": org.name or settings.BRAND_NAME,
            "brand_color": org.color or settings.BRAND_COLOR,
            "brand_accent": org.accent or settings.BRAND_ACCENT,
            "brand_logo": org.logo.url if org.logo else "",
        }
    return {
        "brand_name": settings.BRAND_NAME,
        "brand_color": settings.BRAND_COLOR,
        "brand_accent": settings.BRAND_ACCENT,
        "brand_logo": "",
    }
