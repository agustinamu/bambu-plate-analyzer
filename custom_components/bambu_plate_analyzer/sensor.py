"""Sensor platform for Bambu Plate Analyzer."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from PIL import Image

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_SERIAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


def compute_bounding_boxes(image_bytes: bytes) -> dict[str, Any]:
    """Compute bounding boxes for each object in the pick image.

    Replicates ha-bambulab's color→identify_id conversion (BGR ordering)
    and additionally tracks min/max x/y per object.

    Returns dict with image_width, image_height, and objects mapping.
    """
    image = Image.open(BytesIO(image_bytes))
    image_width, image_height = image.size
    pixels = image.load()

    # Track bounding boxes: identify_id → [min_x, min_y, max_x, max_y]
    bboxes: dict[str, list[int]] = {}
    seen_colors: dict[tuple, str] = {}

    for y in range(image_height):
        for x in range(image_width):
            current_color = pixels[x, y]
            r, g, b, a = current_color

            # Skip transparent pixels
            if a == 0:
                continue

            # Check if we already mapped this color
            if current_color in seen_colors:
                identify_id = seen_colors[current_color]
            else:
                # BGR ordering, same as ha-bambulab
                identify_id = str(int(f"0x{b:02X}{g:02X}{r:02X}", 16))
                seen_colors[current_color] = identify_id

            # Update bounding box
            if identify_id in bboxes:
                bbox = bboxes[identify_id]
                if x < bbox[0]:
                    bbox[0] = x
                if y < bbox[1]:
                    bbox[1] = y
                if x > bbox[2]:
                    bbox[2] = x
                if y > bbox[3]:
                    bbox[3] = y
            else:
                bboxes[identify_id] = [x, y, x, y]

    return {
        "image_width": image_width,
        "image_height": image_height,
        "bboxes": bboxes,
    }


def convert_to_jpeg(image_bytes: bytes, quality: int = 80) -> bytes:
    """Convert image bytes to JPEG format."""
    image = Image.open(BytesIO(image_bytes))
    if image.mode == "RGBA":
        # JPEG doesn't support alpha; composite onto black background
        bg = Image.new("RGB", image.size, (0, 0, 0))
        bg.paste(image, mask=image.split()[3])
        image = bg
    elif image.mode != "RGB":
        image = image.convert("RGB")
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""
    serial = entry.data[CONF_SERIAL]
    async_add_entities([BambuPlateAnalyzerSensor(hass, entry, serial)])


class BambuPlateAnalyzerSensor(SensorEntity):
    """Sensor that analyzes the Bambu pick image and exposes bounding boxes."""

    _attr_has_entity_name = True
    _attr_translation_key = "plate_analyzer"
    _attr_icon = "mdi:cube-scan"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        serial: str,
    ) -> None:
        """Initialize the sensor."""
        self._serial = serial
        self._entry = entry
        self._attr_unique_id = f"{serial}_plate_analyzer"
        self._attr_device_info = None

        # Will be resolved in async_added_to_hass
        self._printable_objects_entity_id: str | None = None
        self._pick_image_entity_id: str | None = None

        # Unsub for the global state_changed listener (startup race)
        self._unsub_any_state: callback | None = None

        # State
        self._object_count: int = 0
        self._objects: dict[str, Any] = {}
        self._image_width: int = 0
        self._image_height: int = 0

    @property
    def native_value(self) -> int:
        """Return the number of detected objects."""
        return self._object_count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return bounding box data as attributes."""
        return {
            "image_width": self._image_width,
            "image_height": self._image_height,
            "objects": self._objects,
            "bbox_data": self._bbox_data_serialized,
        }

    @property
    def _bbox_data_serialized(self) -> str:
        """Serialize bbox data for ESPHome consumption.

        Format: ID:name:min_x,min_y,max_x,max_y|...
        """
        if not self._objects:
            return ""
        parts = []
        for identify_id, obj_data in self._objects.items():
            name = obj_data.get("name", "")
            bbox = obj_data.get("bbox")
            if bbox:
                parts.append(
                    f"{identify_id}:{name}:{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
                )
            else:
                parts.append(f"{identify_id}:{name}:")
        return "|".join(parts)

    async def async_added_to_hass(self) -> None:
        """Resolve entity IDs and subscribe to changes."""
        if not self._resolve_entities():
            _LOGGER.warning(
                "Bambu Lab entities not found yet for serial %s, "
                "will retry on state changes",
                self._serial,
            )
            # Listen for any state change to retry resolution
            self._unsub_any_state = self.hass.bus.async_listen(
                "state_changed", self._on_any_state_changed
            )
            self.async_on_remove(self._cancel_any_state_listener)
            return

        self._subscribe_to_printable_objects()

    def _resolve_entities(self) -> bool:
        """Look up entity IDs from the entity registry by unique_id suffix."""
        ent_reg = er.async_get(self.hass)

        for entry in ent_reg.entities.values():
            if entry.unique_id == f"{self._serial}_printable_objects":
                self._printable_objects_entity_id = entry.entity_id
            elif entry.unique_id == f"{self._serial}_pick_image":
                self._pick_image_entity_id = entry.entity_id

        resolved = (
            self._printable_objects_entity_id is not None
            and self._pick_image_entity_id is not None
        )
        if resolved:
            _LOGGER.debug(
                "Resolved entities: printable_objects=%s, pick_image=%s",
                self._printable_objects_entity_id,
                self._pick_image_entity_id,
            )
        return resolved

    @callback
    def _subscribe_to_printable_objects(self) -> None:
        """Subscribe to state changes of the printable_objects sensor."""
        assert self._printable_objects_entity_id is not None
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self._printable_objects_entity_id],
                self._on_printable_objects_changed,
            )
        )
        _LOGGER.debug(
            "Subscribed to %s state changes", self._printable_objects_entity_id
        )

        # Process current state if available
        state = self.hass.states.get(self._printable_objects_entity_id)
        if state is not None and state.attributes.get("objects"):
            self.hass.async_create_task(self._process_plate_data(state))

    @callback
    def _cancel_any_state_listener(self) -> None:
        """Cancel the global state change listener."""
        if self._unsub_any_state is not None:
            self._unsub_any_state()
            self._unsub_any_state = None

    @callback
    def _on_any_state_changed(self, event: Event) -> None:
        """Retry entity resolution when any state changes (startup race)."""
        if self._resolve_entities():
            _LOGGER.info("Bambu Lab entities resolved after startup delay")
            self._cancel_any_state_listener()
            self._subscribe_to_printable_objects()

    async def _on_printable_objects_changed(self, event: Event) -> None:
        """Handle printable_objects state change."""
        new_state = event.data.get("new_state")
        if new_state is None:
            return
        await self._process_plate_data(new_state)

    async def _async_get_pick_image(self) -> bytes | None:
        """Get pick image bytes from the image entity via EntityComponent."""
        entity_comp = self.hass.data.get("entity_components", {}).get("image")
        if entity_comp is None:
            _LOGGER.warning("Image entity component not available")
            return None

        entity = entity_comp.get_entity(self._pick_image_entity_id)
        if entity is None:
            _LOGGER.warning(
                "Pick image entity %s not found in component",
                self._pick_image_entity_id,
            )
            return None

        try:
            return await entity.async_image()
        except Exception:
            _LOGGER.exception(
                "Failed to get image from %s", self._pick_image_entity_id
            )
            return None

    async def _process_plate_data(self, printable_objects_state) -> None:
        """Fetch pick image, compute bounding boxes, update state."""
        objects_attr = printable_objects_state.attributes.get("objects", {})

        if not objects_attr:
            self._object_count = 0
            self._objects = {}
            self._image_width = 0
            self._image_height = 0
            self.async_write_ha_state()
            return

        # Fetch the pick image bytes via EntityComponent
        assert self._pick_image_entity_id is not None
        image_bytes = await self._async_get_pick_image()

        if image_bytes is None:
            return

        # Process in executor (Pillow is blocking)
        try:
            result = await self.hass.async_add_executor_job(
                compute_bounding_boxes, image_bytes
            )
        except Exception:
            _LOGGER.exception("Error processing pick image")
            return

        bboxes = result["bboxes"]

        # Merge bbox data with object names from printable_objects
        merged: dict[str, dict[str, Any]] = {}
        for identify_id, name in objects_attr.items():
            obj_data: dict[str, Any] = {"name": name}
            if identify_id in bboxes:
                obj_data["bbox"] = bboxes[identify_id]
            merged[identify_id] = obj_data

        self._object_count = len(merged)
        self._objects = merged
        self._image_width = result["image_width"]
        self._image_height = result["image_height"]

        # Convert pick image to JPEG and store for the image entity
        try:
            jpeg_bytes = await self.hass.async_add_executor_job(
                convert_to_jpeg, image_bytes
            )
            entry_data = self.hass.data.get(DOMAIN, {}).get(self._entry.entry_id, {})
            entry_data["jpeg_bytes"] = jpeg_bytes
            entry_data["jpeg_updated"] = datetime.now(timezone.utc)
        except Exception:
            _LOGGER.exception("Error converting pick image to JPEG")

        _LOGGER.debug(
            "Plate analysis complete: %d objects, image %dx%d",
            self._object_count,
            self._image_width,
            self._image_height,
        )
        self.async_write_ha_state()
