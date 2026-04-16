import os

from flask import Blueprint, current_app, render_template, session

from config import BUILD_DATE, detect_ha_addon_channel, get_runtime_version, resolve_web_port
from routes.auth import _generate_csrf_token
from services.ha_addon import get_ma_addon_ui_url

views_bp = Blueprint("views", __name__)


@views_bp.route("/")
def index():
    """Render the main page"""
    auth_enabled = current_app.config.get("AUTH_ENABLED", False)
    is_ha_addon = current_app.config.get("IS_HA_ADDON", False)
    demo_mode = os.environ.get("DEMO_MODE", "").lower() == "true"
    runtime_version = get_runtime_version()
    display_version = f"{runtime_version}-demo" if demo_mode else runtime_version
    ma_ui_url = get_ma_addon_ui_url()
    ma_profile_url = f"{ma_ui_url}/#/settings/profile" if ma_ui_url else ""
    ha_mode = bool(os.environ.get("SUPERVISOR_TOKEN"))
    return render_template(
        "index.html",
        VERSION=runtime_version,
        DISPLAY_VERSION=display_version,
        BUILD_DATE=BUILD_DATE,
        auth_enabled=auth_enabled,
        demo_mode=demo_mode,
        ha_mode=ha_mode,
        is_ha_addon=is_ha_addon,
        ha_user=session.get("ha_user", ""),
        auth_method=session.get("auth_method", ""),
        ma_ui_url=ma_ui_url,
        ma_profile_url=ma_profile_url,
        ha_ingress_web_port=resolve_web_port() if ha_mode else None,
        ha_delivery_channel=detect_ha_addon_channel() if ha_mode else "",
        csrf_token=_generate_csrf_token(),
    )
