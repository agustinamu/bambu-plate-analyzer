"""Image platform for Bambu Plate Analyzer."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_SERIAL, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up image platform."""
    serial = entry.data[CONF_SERIAL]
    async_add_entities([BambuPlateAnalyzerImage(hass, entry, serial)])


class BambuPlateAnalyzerImage(ImageEntity):
    """Image entity that serves the plate pick image as JPEG."""

    _attr_has_entity_name = True
    _attr_translation_key = "plate_image"
    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        serial: str,
    ) -> None:
        """Initialize the image entity."""
        super().__init__(hass)
        self._serial = serial
        self._entry = entry
        self._attr_unique_id = f"{serial}_plate_analyzer_image"
        self._last_updated: datetime | None = None

    @property
    def image_last_updated(self) -> datetime | None:
        """Return timestamp of when the image was last updated."""
        return self._last_updated

    async def async_image(self) -> bytes | None:
        """Return JPEG bytes from hass.data store."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        return entry_data.get("jpeg_bytes")

    async def async_added_to_hass(self) -> None:
        """Subscribe to sensor state changes to know when image updates."""
        # Find the plate_analyzer sensor entity_id for this entry
        sensor_entity_id = None
        for state in self.hass.states.async_all("sensor"):
            if state.entity_id.endswith("_plate_analyzer"):
                # Verify it belongs to our serial by checking unique_id via registry
                from homeassistant.helpers import entity_registry as er

                ent_reg = er.async_get(self.hass)
                entry = ent_reg.async_get(state.entity_id)
                if entry and entry.unique_id == f"{self._serial}_plate_analyzer":
                    sensor_entity_id = state.entity_id
                    break

        if sensor_entity_id:
            self.async_on_remove(
                async_track_state_change_event(
                    self.hass,
                    [sensor_entity_id],
                    self._on_sensor_changed,
                )
            )
            # Check if JPEG is already available
            self._check_jpeg_update()

    @callback
    def _on_sensor_changed(self, event) -> None:
        """Handle sensor state change — JPEG might have been updated."""
        self._check_jpeg_update()

    @callback
    def _check_jpeg_update(self) -> None:
        """Check if JPEG data was updated and refresh image_last_updated."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
        updated = entry_data.get("jpeg_updated")
        if updated and updated != self._last_updated:
            self._last_updated = updated
            self.async_write_ha_state()
