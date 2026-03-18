from pathlib import Path


def test_dockerfile_copies_all_top_level_python_modules():
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()

    assert "COPY *.py ./" in dockerfile


def test_entrypoint_sets_ha_config_dir_before_config_diagnostics():
    entrypoint = (Path(__file__).resolve().parents[1] / "entrypoint.sh").read_text()

    assert entrypoint.index("export CONFIG_DIR=/data") < entrypoint.index(
        'CONFIG_PATH="${CONFIG_DIR:-/config}/config.json"'
    )
