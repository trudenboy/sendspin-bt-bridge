"""Views blueprint — serves the main HTML page."""
import os

from flask import Blueprint, render_template

from config import VERSION, BUILD_DATE, load_config

views_bp = Blueprint('views', __name__)


@views_bp.route('/')
def index():
    """Render the main page"""
    config = load_config()
    return render_template(
        'index.html',
        VERSION=VERSION,
        BUILD_DATE=BUILD_DATE,
        auth_enabled=bool(config.get('AUTH_ENABLED', False)),
        ha_mode=bool(os.environ.get('SUPERVISOR_TOKEN')),
    )
