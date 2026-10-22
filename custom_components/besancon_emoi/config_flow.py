"""Flux de configuration de l'intégration Besançon et moi."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BesanconEmoiAuthError, BesanconEmoiClient, BesanconEmoiError, ContractData
from .const import (
    CONF_AUTO_SUBMIT,
    CONF_COMMUNE,
    CONF_INDEX_ENTITIES,
    CONF_INDEX_ENTITY,
    CONF_NUMERO,
    CONF_RUE,
    CONF_SUBMIT_WEEKDAY,
    DOMAIN,
    WEEKDAYS,
)
from .journal import resumer
from .opendata import GrandBesanconOpendataClient, OpendataError

_LOGGER = logging.getLogger(__name__)

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
        _LOGGER.debug("BesanconEmoiConfigFlow.async_step_user: entrée | user_input=%s", resumer(user_input))
        errors: dict[str, str] = {}
        if user_input is not None:
            client = BesanconEmoiClient(async_get_clientsession(self.hass))
            try:
                await client.async_login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
            except BesanconEmoiAuthError as err:
                _LOGGER.warning("Connexion refusée : %s", err)
                errors["base"] = "invalid_auth"
            except BesanconEmoiError as err:
                # Aucun échec silencieux : la cause exacte (DNS, TLS,
                # délai dépassé…) est tracée avec sa pile d'appels.
                _LOGGER.exception("Connexion impossible : %s", err)
                errors["base"] = "cannot_connect"
            else:
                _LOGGER.debug("BesanconEmoiConfigFlow.async_step_user: sortie")
                return self.async_create_entry(
                    title=f"Besançon et moi ({user_input[CONF_EMAIL]})",
                    data=user_input,
                )
        _LOGGER.debug("BesanconEmoiConfigFlow.async_step_user: sortie")
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Démarre la ré-authentification après un échec de connexion."""
        _LOGGER.debug("BesanconEmoiConfigFlow.async_step_reauth: entrée | entry_data=%s", resumer(entry_data))
        self._reauth_entry = self._get_reauth_entry()
        _LOGGER.debug("BesanconEmoiConfigFlow.async_step_reauth: sortie")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Demande les nouveaux identifiants et les vérifie."""
        _LOGGER.debug(
            "BesanconEmoiConfigFlow.async_step_reauth_confirm: entrée | user_input=%s", resumer(user_input)
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            client = BesanconEmoiClient(async_get_clientsession(self.hass))
            try:
                await client.async_login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
            except BesanconEmoiAuthError as err:
                _LOGGER.warning("Connexion refusée : %s", err)
                errors["base"] = "invalid_auth"
            except BesanconEmoiError as err:
                _LOGGER.exception("Connexion impossible : %s", err)
                errors["base"] = "cannot_connect"
            else:
                _LOGGER.debug("BesanconEmoiConfigFlow.async_step_reauth_confirm: sortie")
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data={**self._reauth_entry.data, **user_input},
                )
        _LOGGER.debug("BesanconEmoiConfigFlow.async_step_reauth_confirm: sortie")
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
        _LOGGER.debug("BesanconEmoiOptionsFlow.__init__: entrée")
        self._partiel: dict[str, Any] = {}
        _LOGGER.debug("BesanconEmoiOptionsFlow.__init__: sortie")

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Première étape : options de saisie et commune de collecte."""
        _LOGGER.debug("BesanconEmoiOptionsFlow.async_step_init: entrée | user_input=%s", resumer(user_input))
        errors: dict[str, str] = {}
        if user_input is not None:
            # Les champs « index_entity::<contrat> » sont repliés dans
            # l'appariement « index_entities » (contrat → entité).
            self._partiel = {k: v for k, v in user_input.items() if "::" not in k}
            self._partiel[CONF_INDEX_ENTITIES] = self._assembler_index_entities(user_input)
            _LOGGER.debug("BesanconEmoiOptionsFlow.async_step_init: sortie")
            return await self.async_step_collecte()
        current = dict(self.config_entry.options)
        communes = await self._async_get_communes()
        champ_commune: Any = vol.Optional(CONF_COMMUNE, default=current.get(CONF_COMMUNE, ""))
        if communes is None:
            errors["base"] = "cannot_connect"
            validateur_commune: Any = str
        else:
            validateur_commune = vol.In({c.insee: c.nom for c in communes} | {"": "— Aucune —"})
        # Un champ « index_entity::<contrat> » par contrat d'eau connu :
        # l'appariement explicite permet la saisie hebdomadaire sur un
        # compte à plusieurs contrats (chaque compteur a sa source).
        champs_par_contrat: dict[Any, Any] = {}
        courant = dict(current.get(CONF_INDEX_ENTITIES) or {})
        for idcontrat in self._contrats_eau():
            champs_par_contrat[
                vol.Optional(f"{CONF_INDEX_ENTITY}::{idcontrat}", default=courant.get(idcontrat, ""))
            ] = str
        schema = vol.Schema(
            {
                vol.Optional(CONF_AUTO_SUBMIT, default=current.get(CONF_AUTO_SUBMIT, False)): bool,
                vol.Optional(
                    CONF_SUBMIT_WEEKDAY,
                    default=current.get(CONF_SUBMIT_WEEKDAY, "lundi"),
                ): vol.In(WEEKDAYS),
                vol.Optional(CONF_INDEX_ENTITY, default=current.get(CONF_INDEX_ENTITY, "")): str,
                **champs_par_contrat,
                champ_commune: validateur_commune,
            }
        )
        _LOGGER.debug("BesanconEmoiOptionsFlow.async_step_init: sortie")
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    def _assembler_index_entities(self, user_input: dict[str, Any]) -> dict[str, str]:
        """Replie les champs par contrat en appariement contrat → entité."""
        _LOGGER.debug(
            "BesanconEmoiOptionsFlow._assembler_index_entities: entrée | user_input=%s", resumer(user_input)
        )
        prefixe = f"{CONF_INDEX_ENTITY}::"
        _LOGGER.debug("BesanconEmoiOptionsFlow._assembler_index_entities: sortie")
        return {
            key[len(prefixe) :]: value
            for key, value in user_input.items()
            if key.startswith(prefixe) and value
        }

    def _contrats_eau(self) -> list[str]:
        """Contrats d'eau connus du coordinateur (pour l'appariement)."""
        _LOGGER.debug("BesanconEmoiOptionsFlow._contrats_eau: entrée")
        runtime = getattr(self.config_entry, "runtime_data", None)
        data = getattr(runtime, "data", None)
        if not isinstance(data, Mapping):
            _LOGGER.debug("BesanconEmoiOptionsFlow._contrats_eau: sortie")
            return []
        _LOGGER.debug("BesanconEmoiOptionsFlow._contrats_eau: sortie")
        return sorted(key for key, value in data.items() if isinstance(value, ContractData))

    async def async_step_collecte(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Deuxième étape : rue et numéro pour le calendrier de collecte."""
        _LOGGER.debug(
            "BesanconEmoiOptionsFlow.async_step_collecte: entrée | user_input=%s", resumer(user_input)
        )
        errors: dict[str, str] = {}
        if user_input is not None:
            options = {**self._partiel}
            if user_input.get(CONF_RUE):
                options[CONF_RUE] = user_input[CONF_RUE]
                options[CONF_NUMERO] = user_input.get(CONF_NUMERO, "")
            else:
                options[CONF_RUE] = ""
            _LOGGER.debug("BesanconEmoiOptionsFlow.async_step_collecte: sortie")
            return self.async_create_entry(title="", data=options)
        commune = self._partiel.get(CONF_COMMUNE) or self.config_entry.options.get(CONF_COMMUNE, "")
        rues = await self._async_get_rues(commune) if commune else None
        if commune and rues is None:
            errors["base"] = "cannot_connect"
        # Un changement de commune réinitialise rue et numéro, même si le
        # même code de rue existe dans les deux communes : le calendrier
        # dépend du couple commune + rue.
        commune_precedente = self.config_entry.options.get(CONF_COMMUNE, "")
        commune_changee = commune != commune_precedente
        rue_courante = self.config_entry.options.get(CONF_RUE, "")
        if commune_changee or (rues and rue_courante not in {r.code for r in rues}):
            rue_courante = ""
            numero_courant = ""
        else:
            numero_courant = self.config_entry.options.get(CONF_NUMERO, "")
        champ_rue: Any = vol.Optional(CONF_RUE, default=rue_courante)
        if rues:
            validateur_rue: Any = vol.In({r.code: r.nom for r in rues} | {"": "— Aucune —"})
        else:
            validateur_rue = str
        schema = vol.Schema(
            {
                champ_rue: validateur_rue,
                vol.Optional(CONF_NUMERO, default=numero_courant): str,
            }
        )
        _LOGGER.debug("BesanconEmoiOptionsFlow.async_step_collecte: sortie")
        return self.async_show_form(step_id="collecte", data_schema=schema, errors=errors)

    async def _async_get_communes(self) -> list | None:
        """Liste les communes, ou None si l'open data est indisponible."""
        _LOGGER.debug("BesanconEmoiOptionsFlow._async_get_communes: entrée")
        client = GrandBesanconOpendataClient(async_get_clientsession(self.hass))
        try:
            _LOGGER.debug("BesanconEmoiOptionsFlow._async_get_communes: sortie")
            return await client.async_get_communes()
        except OpendataError:
            _LOGGER.debug("BesanconEmoiOptionsFlow._async_get_communes: sortie | None")
            return None

    async def _async_get_rues(self, commune: str) -> list | None:
        """Liste les rues d'une commune, ou None si indisponible."""
        _LOGGER.debug("BesanconEmoiOptionsFlow._async_get_rues: entrée | commune=%s", resumer(commune))
        client = GrandBesanconOpendataClient(async_get_clientsession(self.hass))
        try:
            _LOGGER.debug("BesanconEmoiOptionsFlow._async_get_rues: sortie")
            return await client.async_get_streets(commune)
        except OpendataError:
            _LOGGER.debug("BesanconEmoiOptionsFlow._async_get_rues: sortie | None")
            return None
