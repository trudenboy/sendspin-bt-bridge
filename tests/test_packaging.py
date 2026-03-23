from pathlib import Path


def test_dockerfile_copies_all_top_level_python_modules():
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()

    assert "COPY *.py ./" in dockerfile


def test_entrypoint_sets_ha_config_dir_before_config_diagnostics():
    entrypoint = (Path(__file__).resolve().parents[1] / "entrypoint.sh").read_text()

    assert entrypoint.index("export CONFIG_DIR=/data") < entrypoint.index(
        'CONFIG_PATH="${CONFIG_DIR:-/config}/config.json"'
    )


def test_entrypoint_reports_audio_uid_mismatch_diagnostics():
    entrypoint = (Path(__file__).resolve().parents[1] / "entrypoint.sh").read_text()

    assert "RUNTIME_UID=$(id -u" in entrypoint
    assert "AUDIO_PROBE_STATUS" in entrypoint
    assert "User-scoped audio socket targets UID" in entrypoint
    assert 'user: "${AUDIO_UID:-1000}:${AUDIO_UID:-1000}"' in entrypoint


def test_rpi_check_mentions_container_uid_audio_troubleshooting():
    script = (Path(__file__).resolve().parents[1] / "scripts" / "rpi-check.sh").read_text()

    assert "Docker containers still run as root by default" in script
    assert "docker exec sendspin-client id" in script
    assert 'user: \\"${DETECTED_UID}:${DETECTED_UID}\\"' in script
