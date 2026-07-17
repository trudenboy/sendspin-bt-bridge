from sendspin_bridge.services.infrastructure.runtime_detection import classify_installation


def test_bare_python_process_is_standalone():
    assert classify_installation() == "standalone"


def test_lxc_is_detected_from_container_hint_or_cgroup():
    assert classify_installation(container_hint="lxc") == "lxc"
    assert classify_installation(cgroup_text="0::/lxc/guest-105") == "lxc"


def test_managed_runtime_markers_take_precedence():
    assert classify_installation(supervisor_token="token") == "ha-addon"
    assert classify_installation(docker_marker=True) == "docker"
    assert classify_installation(systemd_service=True) == "systemd"
