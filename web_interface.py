#!/usr/bin/env python3
"""
Web Interface for Sendspin Client
Provides configuration and monitoring UI.

This module is intentionally slim: Flask app initialisation, WSGI middleware,
blueprint registration, and the main() entry-point.  All route handlers live in
routes/api.py and routes/views.py; shared helpers live in config.py and state.py.
"""

import logging
import os

from flask import Flask
from flask_cors import CORS
from waitress import serve

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='templates', static_folder='static')
CORS(app)


class _IngressMiddleware:
    """WSGI middleware: sets SCRIPT_NAME from X-Ingress-Path header before Flask
    creates its URL adapter, so that url_for() correctly prefixes all URLs."""
    def __init__(self, wsgi_app):
        self._app = wsgi_app

    def __call__(self, environ, start_response):
        ingress_path = environ.get('HTTP_X_INGRESS_PATH', '').rstrip('/')
        if ingress_path:
            environ['SCRIPT_NAME'] = ingress_path
        return self._app(environ, start_response)


app.wsgi_app = _IngressMiddleware(app.wsgi_app)

# Register blueprints (imported after app is created to avoid circular imports)
from routes.views import views_bp  # noqa: E402
from routes.api import api_bp      # noqa: E402

app.register_blueprint(views_bp)
app.register_blueprint(api_bp)


def main():
    """Start the web interface"""
    port = int(os.getenv('WEB_PORT', 8080))
    logger.info(f"Starting web interface on port {port}")
    serve(app, host='0.0.0.0', port=port, threads=4)


if __name__ == '__main__':
    main()
