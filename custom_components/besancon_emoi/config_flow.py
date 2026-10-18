"""Flux de configuration de l'intégration Besançon et moi."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BesanconEmoiAuthError, BesanconEmoiClient, BesanconEmoiError
from .const import CONF_AUTO_SUBMIT, CONF_INDEX_ENTITY, CONF_SUBMIT_WEEKDAY, DOMAIN, WEEKDAYS

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class BesanconEmoiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Flux de configuration."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Étape initiale : identifiants du portail."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client = BesanconEmoiClient(async_get_clientsession(self.hass))
            try:
                await client.async_login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
            except BesanconEmoiAuthError:
                errors["base"] = "invalid_auth"
            except BesanconEmoiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=f"Besançon et moi ({user_input[CONF_EMAIL]})",
                    data=user_input,
                )
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Démarre la ré-authentification après un échec de connexion."""
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Demande les nouveaux identifiants et les vérifie."""
        errors: dict[str, str] = {}
        if user_input is not None:
            client = BesanconEmoiClient(async_get_clientsession(self.hass))
            try:
                await client.async_login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
            except BesanconEmoiAuthError:
                errors["base"] = "invalid_auth"
            except BesanconEmoiError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data={**self._reauth_entry.data, **user_input},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
        )

    @classmethod
    @callback
    def async_get_options_flow(cls, config_entry: ConfigEntry) -> BesanconEmoiOptionsFlow:
        """Retourne le flux d'options."""
        return BesanconEmoiOptionsFlow()


class BesanconEmoiOptionsFlow(OptionsFlow):
    """Options : saisie hebdomadaire automatique du relevé."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Gère les options de l'intégration."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = dict(self.config_entry.options)
        schema = vol.Schema(
            {
                vol.Optional(CONF_AUTO_SUBMIT, default=current.get(CONF_AUTO_SUBMIT, False)): bool,
                vol.Optional(
                    CONF_SUBMIT_WEEKDAY,
                    default=current.get(CONF_SUBMIT_WEEKDAY, "lundi"),
                ): vol.In(WEEKDAYS),
                vol.Optional(CONF_INDEX_ENTITY, default=current.get(CONF_INDEX_ENTITY, "")): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
