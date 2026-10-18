"""Intégration « Besançon et moi » : contrats, factures et relevés d'eau."""

from __future__ import annotations

from datetime import date, datetime

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import (
    BesanconEmoiAuthError,
    BesanconEmoiClient,
    BesanconEmoiError,
    ContractData,
)
from .const import DOMAIN, LOGGER
from .coordinator import BesanconEmoiCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

type BesanconEmoiConfigEntry = ConfigEntry[dict[str, ContractData]]


async def async_setup_entry(hass: HomeAssistant, entry: BesanconEmoiConfigEntry) -> bool:
    """Configure l'intégration depuis une entrée de configuration.

    Une session HTTP dédiée est créée par compte : le cookie de session
    du portail n'est pas partagé entre plusieurs comptes.
    """
    session = async_create_clientsession(hass)
    client = BesanconEmoiClient(session)
    try:
        await client.async_login(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    except BesanconEmoiAuthError as err:
        await client.close()
        msg = "Identifiants refusés par le portail"
        raise ConfigEntryAuthFailed(msg) from err
    except BesanconEmoiError as err:
        await client.close()
        msg = f"Portail inaccessible : {err}"
        raise ConfigEntryNotReady(msg) from err
    coordinator = BesanconEmoiCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await client.close()
        raise
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BesanconEmoiConfigEntry) -> bool:
    """Décharge une entrée de configuration et ferme sa session."""
    coordinator: BesanconEmoiCoordinator | None = getattr(entry, "runtime_data", None)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and coordinator is not None:
        await coordinator.client.close()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: BesanconEmoiConfigEntry) -> None:
    """Recharge l'entrée quand les options changent."""
    await hass.config_entries.async_reload(entry.entry_id)


def _register_services(hass: HomeAssistant) -> None:
    """Enregistre l'action de saisie manuelle du relevé."""

    async def async_saisir_index(call: ServiceCall) -> None:
        """Transmet un relevé d'index au portail.

        Le contrat visé est le champ « contrat » s'il est fourni ; sinon
        le seul contrat configuré ; sinon une erreur explicite (on n'envoie
        jamais le même index à plusieurs compteurs).
        """
        index = int(call.data["index"])
        date_releve = date.today()
        raw_date = call.data.get("date_releve")
        if raw_date:
            if isinstance(raw_date, datetime):
                date_releve = raw_date.date()
            else:
                date_releve = date.fromisoformat(str(raw_date))
        cible = call.data.get("contrat")
        cibles: list[tuple[BesanconEmoiCoordinator, object]] = []
        for entry in hass.config_entries.async_entries(DOMAIN):
            coordinator: BesanconEmoiCoordinator | None = getattr(entry, "runtime_data", None)
            if coordinator is None:
                continue
            for contract_data in coordinator.data.values():
                contract = contract_data.contract
                if cible is None or contract.idcontrat == cible:
                    cibles.append((coordinator, contract))
        if not cibles:
            msg = (
                f"Aucun contrat ne correspond à « {cible} »"
                if cible
                else "Aucun contrat Besançon et moi actif"
            )
            raise HomeAssistantError(msg)
        if len(cibles) > 1:
            contrats = ", ".join(c.idcontrat for _, c in cibles)
            msg = (
                "Plusieurs contrats sont configurés "
                f"({contrats}) : précisez celui à viser avec le champ « contrat »"
            )
            raise HomeAssistantError(msg)
        coordinator, contract = cibles[0]
        try:
            await coordinator.client.async_submit_index(
                contract.idcontrat,
                contract.abo,
                index,
                date_releve=date_releve,
                last_index=contract.last_index,
            )
        except BesanconEmoiError as err:
            msg = f"Contrat {contract.idcontrat} : saisie refusée ({err})"
            raise HomeAssistantError(msg) from err
        await coordinator.async_request_refresh()
        LOGGER.info("Relevé %s transmis pour le contrat %s", index, contract.idcontrat)

    if not hass.services.has_service(DOMAIN, "saisir_index"):
        hass.services.async_register(
            DOMAIN,
            "saisir_index",
            async_saisir_index,
        )
