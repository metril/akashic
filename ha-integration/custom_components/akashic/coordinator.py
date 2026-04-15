import logging
from datetime import timedelta

import httpx
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_API_URL, CONF_API_KEY, DEFAULT_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class AkashicCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, config: dict) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="akashic",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api_url = config[CONF_API_URL]
        self.api_key = config[CONF_API_KEY]
        self._client = httpx.AsyncClient()

    async def async_shutdown(self) -> None:
        await self._client.aclose()

    async def _async_update_data(self) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        sources_resp = await self._client.get(f"{self.api_url}/api/sources", headers=headers, timeout=10)
        sources_resp.raise_for_status()
        sources = sources_resp.json()

        return {
            "sources": {s["name"]: s for s in sources},
            "total_sources": len(sources),
        }
