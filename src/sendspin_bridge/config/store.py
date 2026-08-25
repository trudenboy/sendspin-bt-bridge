"""One owner for reading, migrating and writing the bridge's config.

The read held the shared lock, but the migration and the write that followed
it happened outside — and four separate implementations of the atomic write
had grown up around the file, with the route layer opening it directly ten
times over.  A store makes the file something you go *through* rather than
something you happen to know the path of, which also makes it substitutable
in tests instead of monkey-patched module constants.

It closes a data-loss path on the way.  A file whose JSON fails to parse is
backed up before the bridge falls back to defaults, but a file that parsed
into something that is not an object — ``[1, 2, 3]``, ``"text"``, ``null`` —
took a different branch that logged a warning and took no backup.  The
operator's settings were then replaced by defaults on the next write with
nothing to recover from.  Both are corruption; both are backed up.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import time
from typing import TYPE_CHECKING, Any

from sendspin_bridge.config import DEFAULT_CONFIG, config_lock, migrate_config_payload
from sendspin_bridge.config.migration import _normalize_loaded_config

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["ConfigStore"]


class ConfigStore:
    """The config file, as an object rather than a path everyone knows."""

    def __init__(self, path: Path):
        self.path = path

    # -- reading -------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """Defaults merged with what is on disk, normalised and migrated.

        A corrupt file is backed up and the defaults stand in.  A file that
        cannot be *read* — a permissions problem, a vanished mount — is a
        different thing entirely and is raised: silently serving defaults
        there would invite the caller to write them back over settings that
        are perfectly intact.
        """
        config = copy.deepcopy(DEFAULT_CONFIG)
        with config_lock:
            raw = self._read_raw()
            if raw is not None:
                stored = self._parse(raw)
                if stored is None:
                    self._backup_corrupt()
                else:
                    migrated = migrate_config_payload(stored)
                    config.update(migrated.normalized_config)
                    for issue in migrated.warnings:
                        logger.info("%s", issue.message)
        _normalize_loaded_config(config, defaults=DEFAULT_CONFIG)
        return config

    def read_stored(self, *, backup_corrupt: bool = False) -> dict[str, Any] | None:
        """Exactly what is on disk, or ``None`` when there is no file.

        Raises ``ValueError`` when the file exists but is not a JSON object —
        callers that are about to overwrite it need to know the difference
        between "nothing there yet" and "something there I could not read".
        A caller that is about to refuse a save can ask for the unusable file
        to be backed up first, so the operator still has something to recover
        their settings from.
        """
        with config_lock:
            raw = self._read_raw()
            if raw is None:
                return None
            stored = self._parse(raw)
            if stored is None:
                if backup_corrupt:
                    self._backup_corrupt()
                raise ValueError(f"{self.path} does not contain a JSON object")
            return stored

    # -- writing -------------------------------------------------------

    def mutate(self, mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        """Read, modify and write as one operation under the lock."""
        with config_lock:
            raw = self._read_raw()
            config: dict[str, Any] = {}
            if raw is not None:
                parsed = self._parse(raw)
                if parsed is not None:
                    config = parsed
            mutator(config)
            self._write(config)
            return config

    def replace(
        self,
        new_config: dict[str, Any],
        *,
        preserve: Iterable[str],
        owned: Iterable[str] = (),
        backup_corrupt: bool = True,
    ) -> dict[str, Any]:
        """Write *new_config*, carrying *preserve* forward from what is there.

        Returns the previous contents, so a caller can diff against them
        without a second read.

        The preserved keys are the ones no form ever submits — the password
        hash, the session secret, the stored tokens.  If the existing file
        cannot be read, this raises rather than writing: a save that quietly
        dropped those keys would take the operator's credentials with it.

        *owned* is the stronger claim: those keys belong to the file, so an
        incoming value for one of them is never trusted — it is replaced by
        what is stored, or dropped when nothing is.  That is what an uploaded
        config needs, where a password hash in the upload is not the
        uploader's to set.
        """
        with config_lock:
            raw = self._read_raw()
            previous: dict[str, Any] = {}
            if raw is not None:
                parsed = self._parse(raw)
                if parsed is None:
                    if backup_corrupt:
                        self._backup_corrupt()
                    raise ValueError(f"{self.path} does not contain a JSON object; refusing to overwrite it")
                previous = parsed

            merged = dict(new_config)
            for key in preserve:
                if key in previous and key not in merged:
                    merged[key] = copy.deepcopy(previous[key])
            for key in owned:
                if key in previous:
                    merged[key] = copy.deepcopy(previous[key])
                else:
                    merged.pop(key, None)
            self._write(merged)
            return previous

    # -- internals -----------------------------------------------------

    def _read_raw(self) -> str | None:
        """The file's text, or ``None`` when it does not exist."""
        if not self.path.exists():
            return None
        return self.path.read_text()

    def _parse(self, raw: str) -> dict[str, Any] | None:
        """Parse *raw* into a config dict, or ``None`` when it is unusable."""
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("Config file %s is corrupted (%s)", self.path, exc)
            return None
        if not isinstance(parsed, dict):
            logger.error(
                "Config file %s contains %s, not a JSON object",
                self.path,
                type(parsed).__name__,
            )
            return None
        return parsed

    def _backup_corrupt(self) -> Path | None:
        """Keep a copy of an unusable config before the defaults take over."""
        backup = self.path.with_name(f"{self.path.name}.corrupt-{int(time.time() * 1000)}")
        try:
            backup.write_bytes(self.path.read_bytes())
        except OSError as exc:
            logger.error("Could not back up %s: %s", self.path, exc)
            return None
        logger.error("Backed up the unusable config to %s; using defaults", backup)
        return backup

    def _write(self, config: dict[str, Any]) -> None:
        """Replace the file atomically, so a crash cannot leave half of one."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115 — closed by hand below
            dir=str(self.path.parent), delete=False, mode="w", suffix=".tmp"
        )
        try:
            json.dump(config, tmp, indent=2)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, str(self.path))
        except BaseException:
            tmp.close()
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise
