"""Post-Apply verification uses the recording contract, but never writes another delay."""

from tests.unit.api.test_sync_api import _measured_session, _upload, sync_env  # noqa: F401


def test_post_apply_verification_is_a_real_measurement(api_client, sync_env):  # noqa: F811
    control, _store, _events, _clock = sync_env
    sid = _measured_session(api_client)
    assert api_client.post(f"/api/v1/sync/sessions/{sid}/proposal").status_code == 200
    assert api_client.post(f"/api/v1/sync/sessions/{sid}/apply", json={}).status_code == 200
    start = api_client.post(f"/api/v1/sync/sessions/{sid}/verification", json={"action": "start"})
    assert start.status_code == 200, start.text
    assert start.json()["verification"]["state"] == "recording"
    assert _upload(api_client, sid, "a", 0.0).status_code == 200
    assert _upload(api_client, sid, "b", 0.001).status_code == 200
    calls = list(control.calls)
    finish = api_client.post(f"/api/v1/sync/sessions/{sid}/verification", json={"action": "finish"})
    assert finish.status_code == 200, finish.text
    data = finish.json()
    assert data["state"] == "tuning"
    assert data["verification"]["passed"] is True
    assert 0.5 <= data["verification"]["spread_ms"] <= 1.5
    assert not [c for c in control.calls[len(calls) :] if c[0] == "set"]


def test_verification_cannot_start_before_apply(api_client, sync_env):  # noqa: F811
    sid = _measured_session(api_client)
    response = api_client.post(f"/api/v1/sync/sessions/{sid}/verification", json={"action": "start"})
    assert response.status_code == 409
