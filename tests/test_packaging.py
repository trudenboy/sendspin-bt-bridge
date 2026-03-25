from pathlib import Path


def test_dockerfile_copies_all_top_level_python_modules():
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()

    assert "COPY *.py ./" in dockerfile


def test_dockerfile_installs_ffmpeg_runtime_libraries():
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()

    for package in (
        "libavcodec61",
        "libavdevice61",
        "libavfilter10",
        "libavformat61",
        "libavutil59",
        "libswresample5",
        "libswscale8",
    ):
        assert package in dockerfile


def test_entrypoint_sets_ha_config_dir_before_config_diagnostics():
    entrypoint = (Path(__file__).resolve().parents[1] / "entrypoint.sh").read_text()

    assert entrypoint.index("export CONFIG_DIR=/data") < entrypoint.rindex("_refresh_config_diagnostics")


def test_entrypoint_reports_audio_uid_mismatch_diagnostics():
    entrypoint = (Path(__file__).resolve().parents[1] / "entrypoint.sh").read_text()

    assert "RUNTIME_UID=$(id -u" in entrypoint
    assert 'APP_RUNTIME_SPEC="${APP_RUNTIME_UID}:${APP_RUNTIME_GID}"' in entrypoint
    assert "gosu" in entrypoint
    assert "AUDIO_PROBE_STATUS" in entrypoint
    assert "User-scoped audio socket targets UID" in entrypoint
    assert "App UID:" in entrypoint


def test_entrypoint_waits_for_cold_boot_dependencies_before_launch():
    entrypoint = (Path(__file__).resolve().parents[1] / "entrypoint.sh").read_text()

    assert "STARTUP_DEPENDENCY_WAIT_ATTEMPTS" in entrypoint
    assert "STARTUP_DEPENDENCY_WAIT_DELAY_SECONDS" in entrypoint
    assert "Waiting for startup dependencies before launching bridge" in entrypoint
    assert "Startup Wait:" in entrypoint
    assert "_configured_devices_present" in entrypoint


def test_dockerfile_installs_gosu_for_runtime_uid_switch():
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()

    assert "gosu" in dockerfile


def test_docker_compose_passes_audio_uid_to_container():
    compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text()

    assert "- AUDIO_UID=${AUDIO_UID:-1000}" in compose
    assert "- AUDIO_GID=${AUDIO_GID:-1000}" in compose


def test_rpi_check_mentions_container_uid_audio_troubleshooting():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "rpi-check.sh").read_text()

    assert "auto-run the bridge process as AUDIO_UID" in script
    assert "docker exec sendspin-client ps -o user:20,pid,command -C python3" in script
    assert "docker logs --tail 80 sendspin-client" in script


def test_armv7_publish_workflow_builds_pinned_sendspin_and_smoke_tests_import():
    workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "docker-publish-armv7.yml").read_text()

    assert "sendspin_version" in workflow
    assert "SENDSPIN_VERSION=${{ needs.prepare.outputs.sendspin_version }}" in workflow
    assert "scripts/check_sendspin_compat.py" in workflow


def test_requirements_pin_aiosendspin_for_all_architectures():
    requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text()

    assert "aiosendspin==4.3.2" in requirements


def test_dockerfile_relies_on_requirements_pin_for_aiosendspin():
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()

    assert '"aiosendspin~=4.3"' not in dockerfile
