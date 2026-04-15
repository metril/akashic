from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AkashicCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    coordinator: AkashicCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []

    for name in coordinator.data.get("sources", {}):
        entities.append(AkashicSourceAvailableSensor(coordinator, entry, name))

    async_add_entities(entities)


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
