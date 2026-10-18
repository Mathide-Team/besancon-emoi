"""Coordinateur de mise à jour de l'intégration Besançon et moi."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    BesanconEmoiAuthError,
    BesanconEmoiClient,
    BesanconEmoiError,
    ContractData,
    WasteContractData,
)
from .const import (
    CONF_AUTO_SUBMIT,
    CONF_COMMUNE,
    CONF_INDEX_ENTITY,
    CONF_NUMERO,
    CONF_RUE,
    CONF_SUBMIT_WEEKDAY,
    DEFAULT_UPDATE_INTERVAL_MIN,
    DOMAIN,
    LOGGER,
    WEEKDAYS,
)
from .opendata import CollecteData, GrandBesanconOpendataClient, OpendataError

_WEEKDAY_NUMBERS = {name: number for number, name in enumerate(WEEKDAYS)}

_Donnees = dict[str, ContractData | WasteContractData | CollecteData]


class BesanconEmoiCoordinator(DataUpdateCoordinator[_Donnees]):
    """Récupère les contrats d'eau, l'historique d'index, les prélèvements,
    les pesées de bacs (déchets) et le calendrier de collecte."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: BesanconEmoiClient,
        opendata: GrandBesanconOpendataClient | None = None,
    ) -> None:
        """Initialise le coordinateur."""
        super().__init__(
            hass,
            LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MIN),
        )
        self.entry = entry
        self.client = client
        self.opendata = opendata
        # Date du dernier relevé transmis automatiquement (persistée).
        self._last_auto_submit: date | None = _parse_iso(entry.options.get("last_auto_submit"))

    async def _async_update_data(self) -> dict[str, ContractData]:
        """Met à jour les données de tous les contrats.

        Une session expirée déclenche une reconnexion puis une nouvelle
        tentative ; des identifiants réellement refusés démarrent le flux
        de ré-authentification.
        """
        try:
            return await self._async_fetch_data()
        except BesanconEmoiAuthError:
            LOGGER.info("Session expirée, nouvelle tentative de connexion")
            try:
                await self.client.async_login(self.entry.data[CONF_EMAIL], self.entry.data[CONF_PASSWORD])
            except BesanconEmoiAuthError as login_err:
                msg = "Identifiants refusés par le portail"
                self.entry.async_start_reauth(self.hass)
                raise ConfigEntryAuthFailed(msg) from login_err
            except BesanconEmoiError as login_err:
                msg = f"Reconnexion impossible : {login_err}"
                raise UpdateFailed(msg) from login_err
            try:
                return await self._async_fetch_data()
            except BesanconEmoiAuthError as retry_err:
                msg = "Session toujours refusée après reconnexion"
                raise UpdateFailed(msg) from retry_err

    async def _async_fetch_data(self) -> _Donnees:
        """Interroge le portail pour tous les contrats."""
        try:
            contracts = await self.client.async_get_contracts()
        except BesanconEmoiAuthError as err:
            raise err
        except BesanconEmoiError as err:
            msg = f"Portail inaccessible : {err}"
            raise UpdateFailed(msg) from err
        data: _Donnees = {}
        for contract in contracts:
            key = contract.idcontrat
            try:
                history = await self.client.async_get_index_history(contract.abo)
                bills = await self.client.async_get_bills(contract.abo)
            except BesanconEmoiAuthError as err:
                raise err
            except BesanconEmoiError as err:
                msg = f"Contrat {key} : {err}"
                raise UpdateFailed(msg) from err
            data[key] = ContractData(contract=contract, index_history=history, bills=bills)
        # Les déchets sont complémentaires : une erreur côté pesées ne
        # doit pas empêcher la mise à jour de l'eau.
        try:
            waste_contracts = await self.client.async_get_waste_contracts()
        except BesanconEmoiAuthError as err:
            raise err
        except BesanconEmoiError as err:
            LOGGER.warning("Contrats déchets indisponibles : %s", err)
            waste_contracts = []
        for waste_contract in waste_contracts:
            try:
                pesees = await self.client.async_get_weighings(waste_contract.idcontrat)
                pesees_jaune = await self.client.async_get_weighings(waste_contract.idcontrat, jaune=True)
            except BesanconEmoiAuthError as err:
                raise err
            except BesanconEmoiError as err:
                LOGGER.warning(
                    "Pesées indisponibles pour le contrat déchets %s : %s",
                    waste_contract.idcontrat,
                    err,
                )
                continue
            data[waste_contract.idcontrat] = WasteContractData(
                contract=waste_contract, pesees=pesees, pesees_jaune=pesees_jaune
            )
        # Calendrier de collecte (open data, sans rapport avec la session
        # du portail) : une erreur ici ne doit pas casser le reste.
        await self._async_maybe_fetch_collecte(data)
        await self._async_maybe_auto_submit(data)
        return data

    async def _async_maybe_fetch_collecte(self, data: _Donnees) -> None:
        """Récupère le calendrier de collecte si une rue est configurée."""
        commune = self.entry.options.get(CONF_COMMUNE)
        rue = self.entry.options.get(CONF_RUE)
        if not commune or not rue or self.opendata is None:
            return
        try:
            adresses = await self.opendata.async_get_collecte(
                commune, rue, self.entry.options.get(CONF_NUMERO) or None
            )
        except OpendataError as err:
            LOGGER.warning("Calendrier de collecte indisponible : %s", err)
            return
        if not adresses:
            LOGGER.warning(
                "Calendrier de collecte vide pour la commune %s et la rue %s",
                commune,
                rue,
            )
            return
        data["collecte"] = CollecteData(commune=commune, rue=rue, adresses=adresses)

    async def _async_maybe_auto_submit(self, data: _Donnees) -> None:
        """Transmet le relevé une fois par semaine si l'option est active.

        La saisie ne cible qu'un compte à contrat d'eau unique : on
        n'envoie jamais le même index à plusieurs compteurs, et les
        contrats déchets ne comptent pas.
        """
        if not self.entry.options.get(CONF_AUTO_SUBMIT, False):
            return
        water_data = {key: value for key, value in data.items() if isinstance(value, ContractData)}
        if len(water_data) != 1:
            if water_data:
                LOGGER.warning(
                    "Saisie hebdomadaire ignorée : %d contrats d'eau sur ce compte "
                    "(un seul contrat est pris en charge pour l'automatisme)",
                    len(water_data),
                )
            return
        today = date.today()
        weekday = _WEEKDAY_NUMBERS.get(self.entry.options.get(CONF_SUBMIT_WEEKDAY, "lundi"), 0)
        if today.weekday() != weekday:
            return
        if self._last_auto_submit is not None and (today - self._last_auto_submit).days < 6:
            return
        entity_id = self.entry.options.get(CONF_INDEX_ENTITY)
        if not entity_id:
            LOGGER.warning("Saisie hebdomadaire active sans entité source d'index")
            return
        state = self.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            LOGGER.warning("Entité source d'index indisponible : %s", entity_id)
            return
        # L'entité doit publier l'index absolu en m³ : des litres seraient
        # transmis comme un index m³ et fausseraient la facturation.
        unite = state.attributes.get("unit_of_measurement")
        if unite and unite.strip().lower() in ("l", "litre", "litres"):
            msg = (
                f"Entité {entity_id} en litres : l'index absolu en m³ est requis "
                "(aucune conversion automatique)"
            )
            raise UpdateFailed(msg)
        try:
            index = int(float(state.state))
        except ValueError as err:
            msg = f"Valeur non numérique sur {entity_id} : {state.state}"
            raise UpdateFailed(msg) from err
        if index < 0:
            msg = f"Index négatif refusé sur {entity_id} : {state.state}"
            raise UpdateFailed(msg)
        contract_data = next(iter(water_data.values()))
        contract = contract_data.contract
        try:
            await self.client.async_submit_index(
                contract.idcontrat,
                contract.abo,
                index,
                date_releve=today,
                last_index=contract.last_index,
            )
        except BesanconEmoiError as err:
            msg = f"Saisie hebdomadaire impossible : {err}"
            raise UpdateFailed(msg) from err
        self._last_auto_submit = today
        self.hass.config_entries.async_update_entry(
            self.entry,
            options={**self.entry.options, "last_auto_submit": today.isoformat()},
        )


def _parse_iso(value: str | None) -> date | None:
    """Analyse une date ISO 8601 (aaaa-mm-jj)."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None
