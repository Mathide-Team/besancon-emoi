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
from .const import (
    CONF_AUTO_SUBMIT,
    CONF_COMMUNE,
    CONF_INDEX_ENTITY,
    CONF_NUMERO,
    CONF_RUE,
    CONF_SUBMIT_WEEKDAY,
    DOMAIN,
    WEEKDAYS,
)
from .opendata import GrandBesanconOpendataClient

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
    """Options : saisie hebdomadaire automatique et calendrier de collecte."""

    def __init__(self) -> None:
        """Initialise le flux d'options."""
        self._partiel: dict[str, Any] = {}

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Première étape : options de saisie et commune de collecte."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._partiel = dict(user_input)
            return await self.async_step_collecte()
        current = dict(self.config_entry.options)
        communes = await self._async_get_communes()
        champ_commune: Any = vol.Optional(CONF_COMMUNE, default=current.get(CONF_COMMUNE, ""))
        if communes is None:
            errors["base"] = "cannot_connect"
            validateur_commune: Any = str
        else:
            validateur_commune = vol.In({c.insee: c.nom for c in communes} | {"": "— Aucune —"})
        schema = vol.Schema(
            {
                vol.Optional(CONF_AUTO_SUBMIT, default=current.get(CONF_AUTO_SUBMIT, False)): bool,
                vol.Optional(
                    CONF_SUBMIT_WEEKDAY,
                    default=current.get(CONF_SUBMIT_WEEKDAY, "lundi"),
                ): vol.In(WEEKDAYS),
                vol.Optional(CONF_INDEX_ENTITY, default=current.get(CONF_INDEX_ENTITY, "")): str,
                champ_commune: validateur_commune,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    async def async_step_collecte(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Deuxième étape : rue et numéro pour le calendrier de collecte."""
        errors: dict[str, str] = {}
        if user_input is not None:
            options = {**self._partiel}
            if user_input.get(CONF_RUE):
                options[CONF_RUE] = user_input[CONF_RUE]
                options[CONF_NUMERO] = user_input.get(CONF_NUMERO, "")
            else:
                options[CONF_RUE] = ""
            return self.async_create_entry(title="", data=options)
        commune = self._partiel.get(CONF_COMMUNE) or self.config_entry.options.get(CONF_COMMUNE, "")
        rues = await self._async_get_rues(commune) if commune else None
        if commune and rues is None:
            errors["base"] = "cannot_connect"
        rue_courante = self.config_entry.options.get(CONF_RUE, "")
        champ_rue: Any = vol.Optional(CONF_RUE, default=rue_courante)
        if rues:
            validateur_rue: Any = vol.In({r.code: r.nom for r in rues} | {"": "— Aucune —"})
        else:
            validateur_rue = str
        schema = vol.Schema(
            {
                champ_rue: validateur_rue,
                vol.Optional(
                    CONF_NUMERO,
                    default=self.config_entry.options.get(CONF_NUMERO, ""),
                ): str,
            }
        )
        return self.async_show_form(step_id="collecte", data_schema=schema, errors=errors)

    async def _async_get_communes(self) -> list | None:
        """Liste les communes, ou None si l'open data est indisponible."""
        client = GrandBesanconOpendataClient(async_get_clientsession(self.hass))
        try:
            return await client.async_get_communes()
        except Exception:
            return None

    async def _async_get_rues(self, commune: str) -> list | None:
        """Liste les rues d'une commune, ou None si indisponible."""
        client = GrandBesanconOpendataClient(async_get_clientsession(self.hass))
        try:
            return await client.async_get_streets(commune)
        except Exception:
            return None
