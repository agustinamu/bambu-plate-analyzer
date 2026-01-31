"""Config flow for Bambu Plate Analyzer."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import entity_registry as er

from .const import CONF_SERIAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class BambuPlateAnalyzerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bambu Plate Analyzer."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            serial = user_input[CONF_SERIAL].strip()

            # Check not already configured
            await self.async_set_unique_id(serial)
            self._abort_if_unique_id_configured()

            # Validate: look for the printable_objects entity in the registry
            ent_reg = er.async_get(self.hass)
            printable_entry = None
            pick_image_entry = None

            for entry in ent_reg.entities.values():
                if entry.unique_id == f"{serial}_printable_objects":
                    printable_entry = entry
                elif entry.unique_id == f"{serial}_pick_image":
                    pick_image_entry = entry

            if printable_entry is None or pick_image_entry is None:
                errors["base"] = "entities_not_found"
            else:
                return self.async_create_entry(
                    title=f"Bambu Plate {serial[-6:]}",
                    data={CONF_SERIAL: serial},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SERIAL): str,
                }
            ),
            errors=errors,
        )
