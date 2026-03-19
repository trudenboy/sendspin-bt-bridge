"""Views blueprint — serves the main HTML page."""

import os

from flask import Blueprint, current_app, render_template, session

from config import BUILD_DATE, VERSION

views_bp = Blueprint("views", __name__)


@views_bp.route("/")
def index():
    """Render the main page"""
    auth_enabled = current_app.config.get("AUTH_ENABLED", False)
    is_ha_addon = current_app.config.get("IS_HA_ADDON", False)
    demo_mode = os.environ.get("DEMO_MODE", "").lower() == "true"
    display_version = f"{VERSION}-demo" if demo_mode else VERSION
    return render_template(
        "index.html",
        VERSION=VERSION,
        DISPLAY_VERSION=display_version,
        BUILD_DATE=BUILD_DATE,
        auth_enabled=auth_enabled,
        demo_mode=demo_mode,
        ha_mode=bool(os.environ.get("SUPERVISOR_TOKEN")),
        is_ha_addon=is_ha_addon,
        ha_user=session.get("ha_user", ""),
        auth_method=session.get("auth_method", ""),
    )
