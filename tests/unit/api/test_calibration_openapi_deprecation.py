"""The per-speaker microphone compare and click track left the web UI (one calibration path).

"Play" stays in use by the Add speaker dialog. The endpoints stay for other clients (the Home Assistant component); OpenAPI says so.
"""

import pytest

from sendspin_bridge.api.app import create_app

_UI_ONLY_REMOVED = [
    ("post", "/api/v1/calibration/metronome"),
    ("post", "/api/v1/calibration/sessions"),
    ("post", "/api/v1/calibration/sessions/{session_id}/audio"),
    ("delete", "/api/v1/calibration/sessions/{session_id}"),
]


@pytest.mark.parametrize(("method", "path"), _UI_ONLY_REMOVED)
def test_calibration_endpoint_is_marked_deprecated_for_the_ui(method, path):
    operation = create_app().openapi()["paths"][path][method]
    assert "Deprecated for the web UI" in operation["description"]
    assert "group sync tuning" in operation["description"]
