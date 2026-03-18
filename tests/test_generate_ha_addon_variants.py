import re
from pathlib import Path

from scripts.generate_ha_addon_variants import (
    HaAddonVariant,
    generate_multi_addon_repo_files,
    generate_variant_files,
    sync_multi_addon_repo,
    write_multi_addon_repo,
    write_variant_files,
)


def _current_stable_version() -> str:
    root = Path(__file__).resolve().parents[1]
    text = (root / "ha-addon" / "config.yaml").read_text()
    match = re.search(r'^version: "([^"]+)"$', text, flags=re.MULTILINE)
    assert match
    return match.group(1)


def test_generate_stable_variant_matches_current_addon_files():
    root = Path(__file__).resolve().parents[1]
    variant = HaAddonVariant(channel="stable", version=_current_stable_version())

    rendered = generate_variant_files(variant)

    assert rendered["ha-addon/config.yaml"] == (root / "ha-addon" / "config.yaml").read_text()
    assert rendered["ha-addon/build.yaml"] == (root / "ha-addon" / "build.yaml").read_text()


def test_generate_same_slug_beta_variant_switches_channel_defaults():
    rendered = generate_variant_files(HaAddonVariant(channel="beta", version="2.41.0-beta.1"))
    config_text = rendered["ha-addon/config.yaml"]
    build_text = rendered["ha-addon/build.yaml"]

    assert 'name: "Sendspin Bluetooth Bridge"' in config_text
    assert 'slug: "sendspin_bt_bridge"' in config_text
    assert 'version: "2.41.0-beta.1"' in config_text
    assert 'update_channel: "beta"' in config_text
    assert "stage:" not in config_text

    assert "aarch64: ghcr.io/trudenboy/sendspin-bt-bridge:beta" in build_text
    assert "amd64: ghcr.io/trudenboy/sendspin-bt-bridge:beta" in build_text
    assert "armv7: ghcr.io/trudenboy/sendspin-bt-bridge:beta" in build_text


def test_generate_suffix_slug_rc_variant_supports_multi_addon_layout():
    rendered = generate_variant_files(
        HaAddonVariant(
            channel="rc",
            version="2.41.0-rc.1",
            strategy="suffix_slug",
            stage="experimental",
        )
    )
    config_text = rendered["ha-addon/config.yaml"]

    assert 'name: "Sendspin Bluetooth Bridge (RC)"' in config_text
    assert 'slug: "sendspin_bt_bridge_rc"' in config_text
    assert 'description: "Bridge Music Assistant Sendspin protocol to Bluetooth speakers (RC channel)"' in config_text
    assert 'version: "2.41.0-rc.1"' in config_text
    assert 'update_channel: "rc"' in config_text
    assert "stage: experimental" in config_text


def test_write_variant_files_writes_generated_ha_addon_tree(tmp_path):
    write_variant_files(
        tmp_path,
        HaAddonVariant(channel="beta", version="2.41.0-beta.1", strategy="suffix_slug"),
    )

    config_path = tmp_path / "ha-addon" / "config.yaml"
    build_path = tmp_path / "ha-addon" / "build.yaml"

    assert config_path.exists()
    assert build_path.exists()
    assert 'slug: "sendspin_bt_bridge_beta"' in config_path.read_text()
    assert "ghcr.io/trudenboy/sendspin-bt-bridge:beta" in build_path.read_text()


def test_generate_multi_addon_repo_files_renders_suffix_slug_repository_layout():
    root = Path(__file__).resolve().parents[1]

    rendered = generate_multi_addon_repo_files(
        stable_version=_current_stable_version(),
        rc_version="2.41.0-rc.1",
        beta_version="2.41.0-beta.1",
    )

    assert rendered["repository.yaml"] == (root / "repository.yaml").read_text()
    assert rendered["ha-addon/config.yaml"] == (root / "ha-addon" / "config.yaml").read_text()
    assert 'slug: "sendspin_bt_bridge_rc"' in rendered["ha-addon-rc/config.yaml"]
    assert 'slug: "sendspin_bt_bridge_beta"' in rendered["ha-addon-beta/config.yaml"]
    assert "stage: experimental" in rendered["ha-addon-rc/config.yaml"]
    assert "# Sendspin Bluetooth Bridge (RC)" in rendered["ha-addon-rc/README.md"]
    assert "RC channel notice" in rendered["ha-addon-rc/README.md"]
    assert "**Sendspin Bluetooth Bridge (Beta)** now appears in the store." in rendered["ha-addon-beta/DOCS.md"]
    assert "profile sendspin_bt_bridge_rc " in rendered["ha-addon-rc/apparmor.txt"]


def test_write_multi_addon_repo_writes_expected_repository_tree(tmp_path):
    write_multi_addon_repo(
        tmp_path,
        stable_version=_current_stable_version(),
        rc_version="2.41.0-rc.1",
        beta_version="2.41.0-beta.1",
    )

    assert (tmp_path / "repository.yaml").exists()
    assert (tmp_path / "ha-addon" / "config.yaml").exists()
    assert (tmp_path / "ha-addon-rc" / "README.md").exists()
    assert (tmp_path / "ha-addon-beta" / "DOCS.md").exists()
    assert (tmp_path / "ha-addon" / "icon.png").exists()
    assert (tmp_path / "ha-addon-beta" / "logo.png").exists()
    assert (tmp_path / "ha-addon-rc" / "build.yaml").read_text().count("ghcr.io/trudenboy/sendspin-bt-bridge:rc") == 3


def test_generate_multi_addon_repo_files_skips_channels_without_versions():
    rendered = generate_multi_addon_repo_files(stable_version=_current_stable_version())

    assert "ha-addon/config.yaml" in rendered
    assert "ha-addon-rc/config.yaml" not in rendered
    assert "ha-addon-beta/config.yaml" not in rendered


def test_write_multi_addon_repo_writes_assets_only_for_present_channels(tmp_path):
    write_multi_addon_repo(tmp_path, stable_version=_current_stable_version(), beta_version="2.41.0-beta.1")

    assert (tmp_path / "ha-addon" / "icon.png").exists()
    assert (tmp_path / "ha-addon-beta" / "logo.png").exists()
    assert not (tmp_path / "ha-addon-rc").exists()


def test_sync_multi_addon_repo_removes_absent_prerelease_directories(tmp_path):
    (tmp_path / "ha-addon-rc").mkdir()
    (tmp_path / "ha-addon-rc" / "stale.txt").write_text("old")

    sync_multi_addon_repo(tmp_path, stable_version=_current_stable_version())

    assert (tmp_path / "ha-addon" / "config.yaml").exists()
    assert not (tmp_path / "ha-addon-rc").exists()
