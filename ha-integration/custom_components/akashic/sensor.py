from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import AkashicCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    coordinator: AkashicCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [AkashicTotalSourcesSensor(coordinator, entry)]

    for name in coordinator.data.get("sources", {}):
        entities.append(AkashicSourceStatusSensor(coordinator, entry, name))

    async_add_entities(entities)


class AkashicTotalSourcesSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_total_sources"
        self._attr_name = "Akashic Total Sources"

    @property
    def native_value(self):
        return self.coordinator.data.get("total_sources", 0)


class AkashicSourceStatusSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry, source_name):
        super().__init__(coordinator)
        self._source_name = source_name
        self._attr_unique_id = f"{entry.entry_id}_{source_name}_status"
        self._attr_name = f"Akashic {source_name} Status"

    @property
    def native_value(self):
        sources = self.coordinator.data.get("sources", {})
        source = sources.get(self._source_name, {})
        return source.get("status", "unknown")
