"""Profiles live in config.json under SYNC_PROFILES."""

from __future__ import annotations

from typing import Any

from sendspin_bridge.config import load_config, update_config
from sendspin_sync.models import Profile, profile_from_dict, to_dict


def _parse(row: Any) -> Profile | None:
    """Tolerate hand-edited config: skip unusable rows, coerce unknown kinds."""
    if not isinstance(row, dict):
        return None
    if not (row.get("id") and row.get("name") and row.get("group_id")):
        return None
    if row.get("kind") not in ("device", "position"):
        row = {**row, "kind": "device"}
    try:
        return profile_from_dict(row)
    except (TypeError, ValueError, AttributeError):
        return None


def _rows(cfg: dict[str, Any]) -> list[Any]:
    rows = cfg.get("SYNC_PROFILES")
    return rows if isinstance(rows, list) else []


def _keeps(row: Any, profile_id: str) -> bool:
    return not (isinstance(row, dict) and row.get("id") == profile_id)


class ConfigProfileRepository:
    def _all(self) -> list[Profile]:
        parsed = (_parse(row) for row in _rows(load_config()))
        return [p for p in parsed if p is not None]

    def list(self, group_id: str) -> list[Profile]:
        return [p for p in self._all() if p.group_id == group_id]

    def get(self, profile_id: str) -> Profile | None:
        return next((p for p in self._all() if p.id == profile_id), None)

    def save(self, profile: Profile) -> None:
        def mutate(cfg: dict[str, Any]) -> None:
            rows = [r for r in _rows(cfg) if _keeps(r, profile.id)]
            rows.append(to_dict(profile))
            cfg["SYNC_PROFILES"] = rows

        update_config(mutate)

    def delete(self, profile_id: str) -> None:
        def mutate(cfg: dict[str, Any]) -> None:
            cfg["SYNC_PROFILES"] = [r for r in _rows(cfg) if _keeps(r, profile_id)]

        update_config(mutate)
