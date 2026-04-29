import re
from pathlib import Path

import scripts.generate_ha_addon_variants as addon_variants
from scripts.generate_ha_addon_variants import (
    HaAddonVariant,
    generate_multi_addon_repo_files,
    generate_variant_files,
    render_changelog_md,
    sync_multi_addon_repo,
    write_multi_addon_repo,
    write_variant_files,
)


def _current_stable_version() -> str:
    root = Path(__file__).resolve().parents[2]
    text = (root / "ha-addon" / "config.yaml").read_text()
    match = re.search(r'^version: "([^"]+)"$', text, flags=re.MULTILINE)
    assert match
    return match.group(1)


def test_generate_stable_variant_matches_current_addon_files():
    root = Path(__file__).resolve().parents[2]
    variant = HaAddonVariant(channel="stable", version=_current_stable_version())

    rendered = generate_variant_files(variant)

    assert rendered["ha-addon/config.yaml"] == (root / "ha-addon" / "config.yaml").read_text()
    assert rendered["ha-addon/build.yaml"] == (root / "ha-addon" / "build.yaml").read_text()
    assert rendered["ha-addon/translations/en.yaml"] == (root / "ha-addon" / "translations" / "en.yaml").read_text()


def test_generate_same_slug_beta_variant_switches_channel_defaults():
    rendered = generate_variant_files(HaAddonVariant(channel="beta", version="2.41.0-beta.1"))
    config_text = rendered["ha-addon/config.yaml"]
    build_text = rendered["ha-addon/build.yaml"]

    assert 'name: "Sendspin Bluetooth Bridge"' in config_text
    assert 'slug: "sendspin_bt_bridge"' in config_text
    assert 'version: "2.41.0-beta.1"' in config_text
    assert "update_channel:" not in config_text
    assert "web_port:" not in config_text
    assert "boot: manual" in config_text
    assert "ingress_port: 0" in config_text
    assert "web_port: null" not in config_text
    assert "base_listen_port: null" not in config_text
    assert "panel_icon: mdi:flask-outline" in config_text
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
    assert "update_channel:" not in config_text
    assert "web_port:" not in config_text
    assert "boot: manual" in config_text
    assert "ingress_port: 0" in config_text
    assert "panel_icon: mdi:flag-checkered" in config_text
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
    root = Path(__file__).resolve().parents[2]

    rendered = generate_multi_addon_repo_files(
        stable_version=_current_stable_version(),
        rc_version="2.41.0-rc.1",
        beta_version="2.41.0-beta.1",
    )

    assert rendered["repository.yaml"] == (root / "repository.yaml").read_text()
    assert rendered["ha-addon/config.yaml"] == (root / "ha-addon" / "config.yaml").read_text()
    assert 'slug: "sendspin_bt_bridge_rc"' in rendered["ha-addon-rc/config.yaml"]
    assert 'slug: "sendspin_bt_bridge_beta"' in rendered["ha-addon-beta/config.yaml"]
    assert "boot: manual" in rendered["ha-addon-rc/config.yaml"]
    assert "boot: manual" in rendered["ha-addon-beta/config.yaml"]
    assert "ingress_port: 0" in rendered["ha-addon-rc/config.yaml"]
    assert "ingress_port: 0" in rendered["ha-addon-beta/config.yaml"]
    assert "panel_icon: mdi:flag-checkered" in rendered["ha-addon-rc/config.yaml"]
    assert "panel_icon: mdi:flask-outline" in rendered["ha-addon-beta/config.yaml"]
    assert "stage: experimental" in rendered["ha-addon-rc/config.yaml"]
    assert "# Sendspin Bluetooth Bridge (RC)" in rendered["ha-addon-rc/README.md"]
    assert "RC channel notice" in rendered["ha-addon-rc/README.md"]
    assert "![RC channel notice]" in rendered["ha-addon-rc/README.md"]
    assert "https://img.shields.io/badge/RC%20channel-Prerelease-f2c94c" in rendered["ha-addon-rc/README.md"]
    assert "different default HA ingress ports" in rendered["ha-addon-rc/README.md"]
    assert "**Sendspin Bluetooth Bridge (Beta)** now appears in the store." in rendered["ha-addon-beta/DOCS.md"]
    assert "![Beta channel notice]" in rendered["ha-addon-beta/DOCS.md"]
    assert "https://img.shields.io/badge/Beta%20channel-Experimental-ef4444" in rendered["ha-addon-beta/DOCS.md"]
    assert "different default HA ingress ports" in rendered["ha-addon-beta/DOCS.md"]
    assert "profile sendspin_bt_bridge_rc " in rendered["ha-addon-rc/apparmor.txt"]
    assert "## [2.40.5]" in rendered["ha-addon/CHANGELOG.md"]
    assert "## [2.40.5-rc.3]" not in rendered["ha-addon/CHANGELOG.md"]
    assert "## [2.40.5-rc.3]" in rendered["ha-addon-rc/CHANGELOG.md"]
    assert "## [2.40.5]" not in rendered["ha-addon-rc/CHANGELOG.md"]
    assert "## [2.40.5-beta.1]" in rendered["ha-addon-beta/CHANGELOG.md"]
    assert "## [2.40.5-rc.1]" not in rendered["ha-addon-beta/CHANGELOG.md"]


def test_render_changelog_md_filters_entries_to_variant_channel():
    changelog_text = """# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

- upcoming root-only note

## [2.40.6-rc.4] - 2026-03-19

### Fixed
- rc fix

## [2.40.6-beta.1] - 2026-03-19

### Fixed
- beta fix

## [2.40.5] - 2026-03-18

### Fixed
- stable fix
"""

    stable = render_changelog_md(HaAddonVariant(channel="stable", version="2.40.5"), base_text=changelog_text)
    rc = render_changelog_md(HaAddonVariant(channel="rc", version="2.40.6-rc.4"), base_text=changelog_text)
    beta = render_changelog_md(HaAddonVariant(channel="beta", version="2.40.6-beta.1"), base_text=changelog_text)

    assert "## [Unreleased]" in stable
    assert "upcoming root-only note" not in stable
    assert "## [2.40.5]" in stable
    assert "## [2.40.6-rc.4]" not in stable
    assert "## [2.40.6-beta.1]" not in stable

    assert "## [2.40.6-rc.4]" in rc
    assert "## [2.40.5]" not in rc
    assert "## [2.40.6-beta.1]" not in rc

    assert "## [2.40.6-beta.1]" in beta
    assert "## [2.40.6-rc.4]" not in beta
    assert "## [2.40.5]" not in beta


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
    assert (tmp_path / "ha-addon-beta" / "translations" / "en.yaml").exists()
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


def test_write_multi_addon_repo_skips_copying_binary_assets_over_themselves(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    output_root = source_root
    source_addon = source_root / "ha-addon"
    source_addon.mkdir(parents=True)
    for filename in addon_variants._BINARY_VARIANT_FILES:
        (source_addon / filename).write_bytes(filename.encode())

    monkeypatch.setattr(addon_variants, "_HA_ADDON_DIR", source_addon)

    write_multi_addon_repo(output_root, stable_version=_current_stable_version())

    for filename in addon_variants._BINARY_VARIANT_FILES:
        assert (source_addon / filename).read_bytes() == filename.encode()


def test_binary_asset_source_prefers_channel_specific_assets(tmp_path, monkeypatch):
    base_addon = tmp_path / "ha-addon"
    variant_assets = tmp_path / "ha-addon-assets"
    base_addon.mkdir(parents=True)
    (variant_assets / "rc").mkdir(parents=True)
    (base_addon / "icon.png").write_bytes(b"stable-icon")
    (variant_assets / "rc" / "icon.png").write_bytes(b"rc-icon")

    monkeypatch.setattr(addon_variants, "_HA_ADDON_DIR", base_addon)
    monkeypatch.setattr(addon_variants, "_VARIANT_ASSETS_DIR", variant_assets)

    assert addon_variants._binary_asset_source("stable", "icon.png").read_bytes() == b"stable-icon"
    assert addon_variants._binary_asset_source("rc", "icon.png").read_bytes() == b"rc-icon"
    assert addon_variants._binary_asset_source("beta", "icon.png").read_bytes() == b"stable-icon"


def test_write_multi_addon_repo_copies_channel_specific_binary_assets(tmp_path, monkeypatch):
    base_addon = tmp_path / "base" / "ha-addon"
    variant_assets = tmp_path / "base" / "ha-addon-assets"
    output_root = tmp_path / "output"
    base_addon.mkdir(parents=True)
    for filename in addon_variants._BINARY_VARIANT_FILES:
        (base_addon / filename).write_bytes(f"stable-{filename}".encode())
    for channel in ("rc", "beta"):
        asset_dir = variant_assets / channel
        asset_dir.mkdir(parents=True)
        for filename in addon_variants._BINARY_VARIANT_FILES:
            (asset_dir / filename).write_bytes(f"{channel}-{filename}".encode())

    monkeypatch.setattr(addon_variants, "_HA_ADDON_DIR", base_addon)
    monkeypatch.setattr(addon_variants, "_VARIANT_ASSETS_DIR", variant_assets)

    write_multi_addon_repo(
        output_root,
        stable_version=_current_stable_version(),
        rc_version="2.41.0-rc.1",
        beta_version="2.41.0-beta.1",
    )

    assert (output_root / "ha-addon" / "icon.png").read_bytes() == b"stable-icon.png"
    assert (output_root / "ha-addon-rc" / "icon.png").read_bytes() == b"rc-icon.png"
    assert (output_root / "ha-addon-rc" / "logo.png").read_bytes() == b"rc-logo.png"
    assert (output_root / "ha-addon-beta" / "icon.png").read_bytes() == b"beta-icon.png"
    assert (output_root / "ha-addon-beta" / "logo.png").read_bytes() == b"beta-logo.png"
