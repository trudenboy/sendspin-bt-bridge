"""Views blueprint — serves the main HTML page."""
import os

from flask import Blueprint, render_template

from config import VERSION, BUILD_DATE

views_bp = Blueprint('views', __name__)


@views_bp.route('/')
def index():
    """Render the main page"""
    # Import the cached flag from web_interface to avoid re-reading config.json
    from web_interface import _auth_enabled
    return render_template(
        'index.html',
        VERSION=VERSION,
        BUILD_DATE=BUILD_DATE,
        auth_enabled=_auth_enabled,
        ha_mode=bool(os.environ.get('SUPERVISOR_TOKEN')),
    )
