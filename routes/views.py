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
    return render_template(
        "index.html",
        VERSION=VERSION,
        BUILD_DATE=BUILD_DATE,
        auth_enabled=auth_enabled,
        ha_mode=bool(os.environ.get("SUPERVISOR_TOKEN")),
        is_ha_addon=is_ha_addon,
        ha_user=session.get("ha_user", ""),
    )
