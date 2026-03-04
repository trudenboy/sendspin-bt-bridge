"""Views blueprint — serves the main HTML page."""

import os

from flask import Blueprint, current_app, render_template

from config import BUILD_DATE, VERSION

views_bp = Blueprint("views", __name__)


@views_bp.route("/")
def index():
    """Render the main page"""
    auth_enabled = current_app.config.get("AUTH_ENABLED", False)
    return render_template(
        "index.html",
        VERSION=VERSION,
        BUILD_DATE=BUILD_DATE,
        auth_enabled=auth_enabled,
        ha_mode=bool(os.environ.get("SUPERVISOR_TOKEN")),
    )
