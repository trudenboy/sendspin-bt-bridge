"""Music Assistant bootstrap helpers for bridge startup."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ResolvedMaIntegration:
    ma_api_url: str
    ma_api_token: str
    name_map: dict[str, dict[str, Any]] | None
    all_groups: list[dict[str, Any]] | None
    groups_loaded: bool
    ma_monitor_task: asyncio.Task[None] | None


class BridgeMaIntegrationService:
    """Resolve MA credentials, group inventory, and monitor startup."""

    async def initialize(
        self,
        config: dict[str, Any],
        clients: list[Any],
        *,
        server_host: str,
    ) -> ResolvedMaIntegration:
        ma_api_url = str(config.get("MA_API_URL", "")).strip()
        ma_api_token = str(config.get("MA_API_TOKEN", "")).strip()

        supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
        if supervisor_token:
            if not ma_api_url:
                if server_host and server_host.lower() not in ("auto", "discover", ""):
                    ma_api_url = f"http://{server_host}:8095"
                else:
                    ma_api_url = "http://localhost:8095"
                logger.info("MA API URL auto-detected (addon mode): %s", ma_api_url)
            if not ma_api_token:
                logger.warning(
                    "MA API: running in HA addon mode but no 'ma_api_token' configured. "
                    "Create a long-lived token in MA → Settings → API Tokens and set ma_api_token in bridge config."
                )

        name_map: dict[str, dict[str, Any]] | None = None
        all_groups: list[dict[str, Any]] | None = None
        groups_loaded = False
        if ma_api_url and ma_api_token:
            try:
                from services.ma_client import discover_ma_groups

                player_info = [{"player_id": client.player_id, "player_name": client.player_name} for client in clients]
                name_map, all_groups = await discover_ma_groups(ma_api_url, ma_api_token, player_info)
                groups_loaded = True
            except Exception as ma_exc:
                logger.warning("MA API group discovery error: %s", ma_exc)

        ma_monitor_task: asyncio.Task[None] | None = None
        if ma_api_url and ma_api_token and config.get("MA_WEBSOCKET_MONITOR", True):
            from services.ma_monitor import start_monitor

            monitor = start_monitor(ma_api_url, ma_api_token)
            ma_monitor_task = asyncio.create_task(monitor.run())

        return ResolvedMaIntegration(
            ma_api_url=ma_api_url,
            ma_api_token=ma_api_token,
            name_map=name_map,
            all_groups=all_groups,
            groups_loaded=groups_loaded,
            ma_monitor_task=ma_monitor_task,
        )
