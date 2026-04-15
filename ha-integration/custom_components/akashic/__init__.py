import logging

import httpx
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_API_URL, CONF_API_KEY
from .coordinator import AkashicCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = AkashicCoordinator(hass, entry.data)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(coordinator.async_shutdown)

    async def handle_trigger_scan(call):
        source_name = call.data.get("source_name")
        if not source_name:
            _LOGGER.error("trigger_scan called without source_name")
            return
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{entry.data[CONF_API_URL]}/api/scans/trigger",
                    json={"source_name": source_name},
                    headers={"Authorization": f"Bearer {entry.data[CONF_API_KEY]}"},
                    timeout=10,
                )
                resp.raise_for_status()
                _LOGGER.info("Scan triggered for source: %s", source_name)
        except Exception as exc:
            _LOGGER.error("Failed to trigger scan for %s: %s", source_name, exc)

    hass.services.async_register(DOMAIN, "trigger_scan", handle_trigger_scan)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
