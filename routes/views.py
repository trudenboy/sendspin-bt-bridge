"""Views blueprint — serves the main HTML page."""
from flask import Blueprint, render_template
from config import VERSION, BUILD_DATE

views_bp = Blueprint('views', __name__)


@views_bp.route('/')
def index():
    """Render the main page"""
    return render_template('index.html', VERSION=VERSION, BUILD_DATE=BUILD_DATE)
