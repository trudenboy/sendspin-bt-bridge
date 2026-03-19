"""Generate channel-specific Home Assistant addon metadata from one source model.

This script can render either a single variant from the checked-in `ha-addon/`
template or a full multi-addon repository tree following the Music Assistant
style of `stable` / `rc` / `beta` addon directories.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import urllib.parse as _up
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_HA_ADDON_DIR = _REPO_ROOT / "ha-addon"
_BASE_CONFIG_PATH = _HA_ADDON_DIR / "config.yaml"
_BASE_README_PATH = _HA_ADDON_DIR / "README.md"
_BASE_DOCS_PATH = _HA_ADDON_DIR / "DOCS.md"
_BASE_CHANGELOG_PATH = _HA_ADDON_DIR / "CHANGELOG.md"
_BASE_DOCKERFILE_PATH = _HA_ADDON_DIR / "Dockerfile"
_BASE_APPARMOR_PATH = _HA_ADDON_DIR / "apparmor.txt"
_BASE_REPOSITORY_PATH = _REPO_ROOT / "repository.yaml"
_VARIANT_ASSETS_DIR = _REPO_ROOT / "ha-addon-assets"

_BASE_NAME = "Sendspin Bluetooth Bridge"
_BASE_DESCRIPTION = "Bridge Music Assistant Sendspin protocol to Bluetooth speakers"
_BASE_SLUG = "sendspin_bt_bridge"
_CHANNEL_LABELS = {
    "stable": "Stable",
    "rc": "RC",
    "beta": "Beta",
}
_CHANNEL_NETWORK_DEFAULTS = {
    "stable": {"ingress_port": 8080, "base_listen_port": 8928},
    "rc": {"ingress_port": 8081, "base_listen_port": 9028},
    "beta": {"ingress_port": 8082, "base_listen_port": 9128},
}
_CHANNEL_BOOT_DEFAULTS = {
    "stable": "auto",
    "rc": "manual",
    "beta": "manual",
}
_CHANNEL_PANEL_ICONS = {
    "stable": "mdi:bluetooth-audio",
    "rc": "mdi:flag-checkered",
    "beta": "mdi:flask-outline",
}
_CHANNEL_NOTICE_STYLES = {
    "rc": {
        "border": "#f2c94c",
        "background": "#fff7d6",
        "text": "#7a5d00",
        "badge_message": "Prerelease",
    },
    "beta": {
        "border": "#ef4444",
        "background": "#fee2e2",
        "text": "#991b1b",
        "badge_message": "Experimental",
    },
}
_CHANNEL_ADDON_DIRS = {
    "stable": "ha-addon",
    "rc": "ha-addon-rc",
    "beta": "ha-addon-beta",
}
_VALID_CHANNELS = frozenset(_CHANNEL_LABELS)
_VALID_STRATEGIES = frozenset({"same_slug", "suffix_slug"})
_BINARY_VARIANT_FILES = ("icon.png", "logo.png")


def _translation_variant_files() -> dict[str, str]:
    translations_dir = _HA_ADDON_DIR / "translations"
    if not translations_dir.exists():
        return {}
    return {
        str(Path("translations") / path.relative_to(translations_dir)): path.read_text()
        for path in sorted(translations_dir.rglob("*.yaml"))
    }


def _replace_first(text: str, old: str, new: str) -> str:
    if old not in text:
        raise ValueError(f"Could not replace expected text: {old!r}")
    return text.replace(old, new, 1)


def _replace_double_quoted_scalar(text: str, key: str, value: str) -> str:
    pattern = rf'^(?P<indent>\s*){re.escape(key)}: ".*"$'

    def _repl(match: re.Match[str]) -> str:
        return f"{match.group('indent')}{key}: {json.dumps(value)}"

    updated, count = re.subn(pattern, _repl, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"Could not replace {key!r} in addon config template")
    return updated


def _replace_unquoted_scalar(text: str, key: str, value: int) -> str:
    pattern = rf"^(?P<indent>\s*){re.escape(key)}:\s+.*$"

    def _repl(match: re.Match[str]) -> str:
        return f"{match.group('indent')}{key}: {value}"

    updated, count = re.subn(pattern, _repl, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"Could not replace {key!r} in addon config template")
    return updated


def _replace_plain_scalar(text: str, key: str, value: str) -> str:
    pattern = rf"^(?P<indent>\s*){re.escape(key)}:\s+.*$"

    def _repl(match: re.Match[str]) -> str:
        return f"{match.group('indent')}{key}: {value}"

    updated, count = re.subn(pattern, _repl, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"Could not replace {key!r} in addon config template")
    return updated


def _set_optional_stage(text: str, stage: str | None) -> str:
    pattern = r"^stage: .*$"
    if stage is None:
        updated, _count = re.subn(pattern, "", text, flags=re.MULTILINE)
        return re.sub(r"\n{3,}", "\n\n", updated)

    stage_line = f"stage: {stage}"
    if re.search(pattern, text, flags=re.MULTILINE):
        return re.sub(pattern, stage_line, text, count=1, flags=re.MULTILINE)

    anchor = "panel_admin: false"
    if anchor not in text:
        raise ValueError("Could not insert stage into addon config template")
    return text.replace(anchor, f"{anchor}\n{stage_line}", 1)


def _channel_notice(variant: HaAddonVariant) -> str:
    if variant.channel == "stable":
        return ""
    label = _CHANNEL_LABELS[variant.channel]
    style = _CHANNEL_NOTICE_STYLES[variant.channel]
    badge_label = _up.quote(f"{label} channel")
    badge_message = _up.quote(style["badge_message"])
    badge_url = (
        f"https://img.shields.io/badge/{badge_label}-{badge_message}-{style['border'].lstrip('#')}"
        f"?style=for-the-badge&labelColor={style['text'].lstrip('#')}&color={style['border'].lstrip('#')}"
    )
    return (
        f"![{label} channel notice]({badge_url})\n\n"
        f"**{label} channel notice:** This Home Assistant addon variant tracks the "
        f"`{variant.channel}` image lane. Install this variant from the store to receive "
        f"{label} builds; the bridge UI only indicates the installed track, while switching "
        "tracks still happens in the Home Assistant store.\n\n"
    )


@dataclass(frozen=True)
class HaAddonVariant:
    channel: str
    version: str
    strategy: str = "same_slug"
    stage: str | None = None

    def __post_init__(self) -> None:
        if self.channel not in _VALID_CHANNELS:
            raise ValueError(f"Unsupported channel: {self.channel}")
        if self.strategy not in _VALID_STRATEGIES:
            raise ValueError(f"Unsupported strategy: {self.strategy}")
        if not self.version.strip():
            raise ValueError("Version must not be empty")

    @property
    def build_tag(self) -> str:
        return self.channel

    @property
    def slug(self) -> str:
        if self.strategy == "same_slug" or self.channel == "stable":
            return _BASE_SLUG
        return f"{_BASE_SLUG}_{self.channel}"

    @property
    def display_name(self) -> str:
        if self.strategy == "same_slug" or self.channel == "stable":
            return _BASE_NAME
        return f"{_BASE_NAME} ({_CHANNEL_LABELS[self.channel]})"

    @property
    def description(self) -> str:
        if self.strategy == "same_slug" or self.channel == "stable":
            return _BASE_DESCRIPTION
        return f"{_BASE_DESCRIPTION} ({_CHANNEL_LABELS[self.channel]} channel)"

    @property
    def addon_dir(self) -> str:
        return _CHANNEL_ADDON_DIRS[self.channel]


def render_config_yaml(variant: HaAddonVariant, base_text: str | None = None) -> str:
    text = _BASE_CONFIG_PATH.read_text() if base_text is None else base_text
    text = _replace_double_quoted_scalar(text, "name", variant.display_name)
    text = _replace_double_quoted_scalar(text, "version", variant.version)
    text = _replace_double_quoted_scalar(text, "slug", variant.slug)
    text = _replace_double_quoted_scalar(text, "description", variant.description)
    text = _replace_plain_scalar(text, "boot", _CHANNEL_BOOT_DEFAULTS[variant.channel])
    text = _replace_unquoted_scalar(text, "ingress_port", _CHANNEL_NETWORK_DEFAULTS[variant.channel]["ingress_port"])
    text = _replace_plain_scalar(text, "panel_icon", _CHANNEL_PANEL_ICONS[variant.channel])
    text = _set_optional_stage(text, variant.stage)
    return text


def render_build_yaml(variant: HaAddonVariant) -> str:
    return "\n".join(
        [
            "build_from:",
            f"  aarch64: ghcr.io/trudenboy/sendspin-bt-bridge:{variant.build_tag}",
            f"  amd64: ghcr.io/trudenboy/sendspin-bt-bridge:{variant.build_tag}",
            f"  armv7: ghcr.io/trudenboy/sendspin-bt-bridge:{variant.build_tag}",
            "",
        ]
    )


def render_readme_md(variant: HaAddonVariant, base_text: str | None = None) -> str:
    text = _BASE_README_PATH.read_text() if base_text is None else base_text
    text = _replace_first(text, "# Sendspin Bluetooth Bridge", f"# {variant.display_name}")
    if variant.channel != "stable":
        text = _replace_first(text, "## About\n\n", f"## About\n\n{_channel_notice(variant)}")
    return text


def render_docs_md(variant: HaAddonVariant, base_text: str | None = None) -> str:
    text = _BASE_DOCS_PATH.read_text() if base_text is None else base_text
    text = _replace_first(text, "# Sendspin Bluetooth Bridge", f"# {variant.display_name}")
    text = _replace_first(
        text,
        "4. Close the dialog—**Sendspin Bluetooth Bridge** now appears in the store.",
        f"4. Close the dialog—**{variant.display_name}** now appears in the store.",
    )
    if variant.channel != "stable":
        text = _replace_first(text, "## About\n\n", f"## About\n\n{_channel_notice(variant)}")
    return text


def render_apparmor_txt(variant: HaAddonVariant, base_text: str | None = None) -> str:
    text = _BASE_APPARMOR_PATH.read_text() if base_text is None else base_text
    pattern = rf"^profile\s+{re.escape(_BASE_SLUG)}\s+"
    updated, count = re.subn(pattern, f"profile {variant.slug} ", text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError("Could not replace apparmor profile name in addon template")
    return updated


def generate_variant_files(variant: HaAddonVariant) -> dict[str, str]:
    files = {
        "ha-addon/config.yaml": render_config_yaml(variant),
        "ha-addon/build.yaml": render_build_yaml(variant),
    }
    for relative_path, content in _translation_variant_files().items():
        files[f"ha-addon/{relative_path}"] = content
    return files


def write_variant_files(output_root: Path, variant: HaAddonVariant) -> None:
    for relative_path, content in generate_variant_files(variant).items():
        target = output_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def _iter_multi_addon_variants(
    *,
    stable_version: str | None,
    rc_version: str | None,
    beta_version: str | None,
    rc_stage: str | None,
    beta_stage: str | None,
) -> tuple[HaAddonVariant, ...]:
    variants: list[HaAddonVariant] = []
    if stable_version:
        variants.append(HaAddonVariant(channel="stable", version=stable_version, strategy="suffix_slug"))
    if rc_version:
        variants.append(HaAddonVariant(channel="rc", version=rc_version, strategy="suffix_slug", stage=rc_stage))
    if beta_version:
        variants.append(HaAddonVariant(channel="beta", version=beta_version, strategy="suffix_slug", stage=beta_stage))
    if not variants:
        raise ValueError("At least one channel version is required to generate a HA addon repository tree")
    return tuple(variants)


def generate_multi_addon_repo_files(
    *,
    stable_version: str | None = None,
    rc_version: str | None = None,
    beta_version: str | None = None,
    rc_stage: str | None = "experimental",
    beta_stage: str | None = "experimental",
) -> dict[str, str]:
    variants = _iter_multi_addon_variants(
        stable_version=stable_version,
        rc_version=rc_version,
        beta_version=beta_version,
        rc_stage=rc_stage,
        beta_stage=beta_stage,
    )
    files = {"repository.yaml": _BASE_REPOSITORY_PATH.read_text()}
    for variant in variants:
        addon_dir = variant.addon_dir
        files[f"{addon_dir}/config.yaml"] = render_config_yaml(variant)
        files[f"{addon_dir}/build.yaml"] = render_build_yaml(variant)
        files[f"{addon_dir}/README.md"] = render_readme_md(variant)
        files[f"{addon_dir}/DOCS.md"] = render_docs_md(variant)
        files[f"{addon_dir}/CHANGELOG.md"] = _BASE_CHANGELOG_PATH.read_text()
        files[f"{addon_dir}/Dockerfile"] = _BASE_DOCKERFILE_PATH.read_text()
        files[f"{addon_dir}/apparmor.txt"] = render_apparmor_txt(variant)
        for relative_path, content in _translation_variant_files().items():
            files[f"{addon_dir}/{relative_path}"] = content
    return files


def _binary_asset_source(channel: str, filename: str) -> Path:
    if channel != "stable":
        variant_asset = _VARIANT_ASSETS_DIR / channel / filename
        if variant_asset.exists():
            return variant_asset
    return _HA_ADDON_DIR / filename


def write_multi_addon_repo(
    output_root: Path,
    *,
    stable_version: str | None = None,
    rc_version: str | None = None,
    beta_version: str | None = None,
    rc_stage: str | None = "experimental",
    beta_stage: str | None = "experimental",
) -> None:
    files = generate_multi_addon_repo_files(
        stable_version=stable_version,
        rc_version=rc_version,
        beta_version=beta_version,
        rc_stage=rc_stage,
        beta_stage=beta_stage,
    )
    for relative_path, content in files.items():
        target = output_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    variants = _iter_multi_addon_variants(
        stable_version=stable_version,
        rc_version=rc_version,
        beta_version=beta_version,
        rc_stage=rc_stage,
        beta_stage=beta_stage,
    )
    for addon_dir in [variant.addon_dir for variant in variants]:
        channel = next(variant.channel for variant in variants if variant.addon_dir == addon_dir)
        for filename in _BINARY_VARIANT_FILES:
            source = _binary_asset_source(channel, filename)
            target = output_root / addon_dir / filename
            if source.resolve() == target.resolve():
                continue
            shutil.copy2(source, target)


def sync_multi_addon_repo(
    output_root: Path,
    *,
    stable_version: str | None = None,
    rc_version: str | None = None,
    beta_version: str | None = None,
    rc_stage: str | None = "experimental",
    beta_stage: str | None = "experimental",
) -> None:
    variants = _iter_multi_addon_variants(
        stable_version=stable_version,
        rc_version=rc_version,
        beta_version=beta_version,
        rc_stage=rc_stage,
        beta_stage=beta_stage,
    )
    for channel, addon_dir in _CHANNEL_ADDON_DIRS.items():
        if addon_dir == "ha-addon":
            continue
        if channel not in {variant.channel for variant in variants} and (output_root / addon_dir).exists():
            shutil.rmtree(output_root / addon_dir)
    write_multi_addon_repo(
        output_root,
        stable_version=stable_version,
        rc_version=rc_version,
        beta_version=beta_version,
        rc_stage=rc_stage,
        beta_stage=beta_stage,
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    variant_parser = subparsers.add_parser("variant", help="Render a single channel variant")
    variant_parser.add_argument("--channel", choices=sorted(_VALID_CHANNELS), required=True)
    variant_parser.add_argument("--version", required=True)
    variant_parser.add_argument("--strategy", choices=sorted(_VALID_STRATEGIES), default="same_slug")
    variant_parser.add_argument("--stage", default=None)
    variant_parser.add_argument("--output-dir", type=Path, required=True)

    repo_parser = subparsers.add_parser("multi-addon-repo", help="Render a multi-addon HA repository tree")
    repo_parser.add_argument("--stable-version")
    repo_parser.add_argument("--rc-version")
    repo_parser.add_argument("--beta-version")
    repo_parser.add_argument("--rc-stage", default="experimental")
    repo_parser.add_argument("--beta-stage", default="experimental")
    repo_parser.add_argument("--output-dir", type=Path, required=True)

    sync_parser = subparsers.add_parser("sync-current-repo", help="Sync multi-addon directories in the current repo")
    sync_parser.add_argument("--stable-version")
    sync_parser.add_argument("--rc-version")
    sync_parser.add_argument("--beta-version")
    sync_parser.add_argument("--rc-stage", default="experimental")
    sync_parser.add_argument("--beta-stage", default="experimental")
    sync_parser.add_argument("--output-dir", type=Path, default=_REPO_ROOT)
    return parser


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()
    if args.command == "variant":
        variant = HaAddonVariant(
            channel=args.channel,
            version=args.version,
            strategy=args.strategy,
            stage=args.stage,
        )
        write_variant_files(args.output_dir, variant)
        return

    if args.command == "multi-addon-repo":
        write_multi_addon_repo(
            args.output_dir,
            stable_version=args.stable_version,
            rc_version=args.rc_version,
            beta_version=args.beta_version,
            rc_stage=args.rc_stage,
            beta_stage=args.beta_stage,
        )
        return

    sync_multi_addon_repo(
        args.output_dir,
        stable_version=args.stable_version,
        rc_version=args.rc_version,
        beta_version=args.beta_version,
        rc_stage=args.rc_stage,
        beta_stage=args.beta_stage,
    )


if __name__ == "__main__":
    main()
