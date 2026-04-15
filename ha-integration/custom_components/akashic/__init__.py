from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, CONF_API_URL, CONF_API_KEY
from .coordinator import AkashicCoordinator

PLATFORMS = ["sensor", "binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = AkashicCoordinator(hass, entry.data)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_trigger_scan(call):
        source_name = call.data.get("source_name")
        import httpx
        headers = {"Authorization": f"Bearer {entry.data[CONF_API_KEY]}"}
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{entry.data[CONF_API_URL]}/api/scans/trigger",
                json={"source_name": source_name},
                headers=headers,
                timeout=10,
            )

    hass.services.async_register(DOMAIN, "trigger_scan", handle_trigger_scan)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
