import logging

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AkashicCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    coordinator: AkashicCoordinator = hass.data[DOMAIN][entry.entry_id]
    known_sources: set[str] = set()

    @callback
    def _async_add_new_sources():
        current_sources = set(coordinator.data.get("sources", {}).keys())
        new_sources = current_sources - known_sources
        if new_sources:
            entities = []
            for name in new_sources:
                entities.append(AkashicSourceAvailableSensor(coordinator, entry, name))
            async_add_entities(entities)
            known_sources.update(new_sources)

    # Add initial entities
    entities = []
    for name in coordinator.data.get("sources", {}):
        entities.append(AkashicSourceAvailableSensor(coordinator, entry, name))
        known_sources.add(name)
    async_add_entities(entities)

    # Listen for future updates
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_sources))


class AkashicSourceAvailableSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, entry, source_name):
        super().__init__(coordinator)
        self._source_name = source_name
        self._attr_unique_id = f"{entry.entry_id}_{source_name}_available"
        self._attr_name = f"Akashic {source_name} Available"

    @property
    def is_on(self):
        sources = self.coordinator.data.get("sources", {})
        source = sources.get(self._source_name, {})
        return source.get("status") == "online"
