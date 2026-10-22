"""Coordinateur de mise à jour de l'intégration Besançon et moi."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, State
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
    CONF_INDEX_ENTITIES,
    CONF_INDEX_ENTITY,
    CONF_NUMERO,
    CONF_RUE,
    CONF_SUBMIT_WEEKDAY,
    DEFAULT_UPDATE_INTERVAL_MIN,
    DOMAIN,
    WEEKDAYS,
)
from .facture import total_ttc_du_pdf
from .journal import resumer
from .opendata import CollecteData, GrandBesanconOpendataClient, OpendataError

_LOGGER = logging.getLogger(__name__)

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
        _LOGGER.debug(
            "BesanconEmoiCoordinator.__init__: entrée | hass=%s, entry=%s, client=%s, opendata=%s",
            resumer(hass),
            resumer(entry),
            resumer(client),
            resumer(opendata),
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(minutes=DEFAULT_UPDATE_INTERVAL_MIN),
        )
        self.entry = entry
        self.client = client
        self.opendata = opendata
        # Date du dernier relevé transmis automatiquement (persistée).
        self._last_auto_submit: date | None = _parse_iso(entry.options.get("last_auto_submit"))
        # Totaux TTC lus dans les PDF de factures, par « idcontrat:facture » :
        # une facture n'est téléchargée qu'une fois par session (le cache
        # est perdu au redémarrage, un nouveau téléchargement a alors lieu).
        self._totaux_ttc: dict[str, float] = {}
        _LOGGER.debug("BesanconEmoiCoordinator.__init__: sortie")

    async def _async_update_data(self) -> dict[str, ContractData]:
        """Met à jour les données de tous les contrats.

        Une session expirée déclenche une reconnexion puis une nouvelle
        tentative ; des identifiants réellement refusés démarrent le flux
        de ré-authentification.
        """
        _LOGGER.debug("BesanconEmoiCoordinator._async_update_data: entrée")
        try:
            _LOGGER.debug("BesanconEmoiCoordinator._async_update_data: sortie")
            return await self._async_fetch_data()
        except BesanconEmoiAuthError:
            _LOGGER.info("Session expirée, nouvelle tentative de connexion")
            try:
                await self.client.async_login(self.entry.data[CONF_EMAIL], self.entry.data[CONF_PASSWORD])
            except BesanconEmoiAuthError as login_err:
                msg = "Identifiants refusés par le portail"
                self.entry.async_start_reauth(self.hass)
                _LOGGER.debug(
                    "BesanconEmoiCoordinator._async_update_data: sortie par ConfigEntryAuthFailed | cause=%s",
                    login_err,
                )
                raise ConfigEntryAuthFailed(msg) from login_err
            except BesanconEmoiError as login_err:
                msg = f"Reconnexion impossible : {login_err}"
                _LOGGER.debug(
                    "BesanconEmoiCoordinator._async_update_data: sortie par UpdateFailed | cause=%s",
                    login_err,
                )
                raise UpdateFailed(msg) from login_err
            try:
                _LOGGER.debug("BesanconEmoiCoordinator._async_update_data: sortie")
                return await self._async_fetch_data()
            except BesanconEmoiAuthError as retry_err:
                msg = "Session toujours refusée après reconnexion"
                _LOGGER.debug(
                    "BesanconEmoiCoordinator._async_update_data: sortie par UpdateFailed | cause=%s",
                    retry_err,
                )
                raise UpdateFailed(msg) from retry_err

    async def _async_fetch_data(self) -> _Donnees:
        """Interroge le portail pour tous les contrats."""
        _LOGGER.debug("BesanconEmoiCoordinator._async_fetch_data: entrée")
        try:
            contracts = await self.client.async_get_contracts()
        except BesanconEmoiAuthError as err:
            _LOGGER.debug("BesanconEmoiCoordinator._async_fetch_data: sortie par err")
            raise err
        except BesanconEmoiError as err:
            msg = f"Portail inaccessible : {err}"
            _LOGGER.debug(
                "BesanconEmoiCoordinator._async_fetch_data: sortie par UpdateFailed | cause=%s", err
            )
            raise UpdateFailed(msg) from err
        data: _Donnees = {}
        for contract in contracts:
            key = contract.idcontrat
            try:
                history = await self.client.async_get_index_history(contract.abo)
                bills = await self.client.async_get_bills(contract.abo)
            except BesanconEmoiAuthError as err:
                _LOGGER.debug("BesanconEmoiCoordinator._async_fetch_data: sortie par err")
                raise err
            except BesanconEmoiError as err:
                msg = f"Contrat {key} : {err}"
                _LOGGER.debug(
                    "BesanconEmoiCoordinator._async_fetch_data: sortie par UpdateFailed | cause=%s", err
                )
                raise UpdateFailed(msg) from err
            data[key] = ContractData(contract=contract, index_history=history, bills=bills)
            await self._async_lire_total_facture(key, data[key])
        # Les déchets sont complémentaires : une erreur côté pesées ne
        # doit pas empêcher la mise à jour de l'eau.
        try:
            waste_contracts = await self.client.async_get_waste_contracts()
        except BesanconEmoiAuthError as err:
            _LOGGER.debug("BesanconEmoiCoordinator._async_fetch_data: sortie par err")
            raise err
        except BesanconEmoiError as err:
            _LOGGER.warning("Contrats déchets indisponibles : %s", err)
            waste_contracts = []
        for waste_contract in waste_contracts:
            try:
                pesees = await self.client.async_get_weighings(waste_contract.idcontrat)
                pesees_jaune = await self.client.async_get_weighings(waste_contract.idcontrat, jaune=True)
            except BesanconEmoiAuthError as err:
                _LOGGER.debug("BesanconEmoiCoordinator._async_fetch_data: sortie par err")
                raise err
            except BesanconEmoiError as err:
                _LOGGER.warning(
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
        _LOGGER.debug("BesanconEmoiCoordinator._async_fetch_data: sortie | %s", resumer(data))
        return data

    async def _async_lire_total_facture(self, idcontrat: str, contract_data: ContractData) -> None:
        """Lit le total TTC de la dernière facture dans son PDF.

        La liste du portail ne publie que le montant prélevé (mensualité
        ou ajustement annuel) ; le total est extrait du PDF, une seule fois
        par facture et par session. Un échec (PDF inaccessible, illisible
        ou sans total reconnaissable) n'interrompt pas la mise à jour : le
        capteur « Dernière facture (TTC) » reste inconnu et l'erreur est
        journalisée.
        """
        _LOGGER.debug(
            "BesanconEmoiCoordinator._async_lire_total_facture: entrée | idcontrat=%s, contract_data=%s",
            resumer(idcontrat),
            resumer(contract_data),
        )
        if not contract_data.bills:
            _LOGGER.debug("BesanconEmoiCoordinator._async_lire_total_facture: sortie")
            return
        latest = contract_data.bills[0]
        if latest.facture in ("", None) or not latest.pdf_path:
            _LOGGER.debug("BesanconEmoiCoordinator._async_lire_total_facture: sortie")
            return
        cache_key = f"{idcontrat}:{latest.facture}"
        total = self._totaux_ttc.get(cache_key)
        if total is None:
            try:
                pdf = await self.client.async_download_bill_pdf(latest.pdf_path)
            except BesanconEmoiAuthError:
                _LOGGER.debug("BesanconEmoiCoordinator._async_lire_total_facture: sortie par relance")
                raise
            except BesanconEmoiError as err:
                _LOGGER.warning("PDF de la facture %s inaccessible : %s", latest.facture, err)
                return
            try:
                total = total_ttc_du_pdf(pdf)
            except ValueError as err:
                _LOGGER.warning("Facture %s illisible : %s", latest.facture, err)
                return
            if total is None:
                _LOGGER.warning("Total TTC introuvable dans la facture %s", latest.facture)
                return
            self._totaux_ttc[cache_key] = total
        latest.total_ttc = total
        _LOGGER.debug("BesanconEmoiCoordinator._async_lire_total_facture: sortie")

    async def _async_maybe_fetch_collecte(self, data: _Donnees) -> None:
        """Récupère le calendrier de collecte si une rue est configurée."""
        _LOGGER.debug("BesanconEmoiCoordinator._async_maybe_fetch_collecte: entrée | data=%s", resumer(data))
        commune = self.entry.options.get(CONF_COMMUNE)
        rue = self.entry.options.get(CONF_RUE)
        if not commune or not rue or self.opendata is None:
            _LOGGER.debug("BesanconEmoiCoordinator._async_maybe_fetch_collecte: sortie")
            return
        try:
            adresses = await self.opendata.async_get_collecte(
                commune, rue, self.entry.options.get(CONF_NUMERO) or None
            )
        except OpendataError as err:
            _LOGGER.warning("Calendrier de collecte indisponible : %s", err)
            return
        if not adresses:
            _LOGGER.warning(
                "Calendrier de collecte vide pour la commune %s et la rue %s",
                commune,
                rue,
            )
            return
        data["collecte"] = CollecteData(commune=commune, rue=rue, adresses=adresses)
        _LOGGER.debug("BesanconEmoiCoordinator._async_maybe_fetch_collecte: sortie")

    async def _async_maybe_auto_submit(self, data: _Donnees) -> None:
        """Transmet le relevé une fois par semaine si l'option est active.

        Un compte à plusieurs contrats d'eau est pris en charge par
        appariement explicite contrat → entité source (option
        « index_entities ») : chaque compteur reçoit son propre index. Sans
        appariement, seul un compte à contrat unique utilise l'entité
        source unique — on n'envoie jamais le même index à plusieurs
        compteurs, et les contrats déchets ne comptent pas.
        """
        _LOGGER.debug("BesanconEmoiCoordinator._async_maybe_auto_submit: entrée | data=%s", resumer(data))
        if not self.entry.options.get(CONF_AUTO_SUBMIT, False):
            _LOGGER.debug("BesanconEmoiCoordinator._async_maybe_auto_submit: sortie")
            return
        water_data = {key: value for key, value in data.items() if isinstance(value, ContractData)}
        if not water_data:
            _LOGGER.debug("BesanconEmoiCoordinator._async_maybe_auto_submit: sortie")
            return
        correspondances = self._entites_par_contrat(water_data)
        if not correspondances:
            if len(water_data) > 1:
                _LOGGER.warning(
                    "Saisie hebdomadaire ignorée : %d contrats d'eau sur ce compte "
                    "sans appariement contrat → entité (option « index_entities »)",
                    len(water_data),
                )
            else:
                _LOGGER.warning("Saisie hebdomadaire active sans entité source d'index")
            _LOGGER.debug("BesanconEmoiCoordinator._async_maybe_auto_submit: sortie")
            return
        today = date.today()
        weekday = _WEEKDAY_NUMBERS.get(self.entry.options.get(CONF_SUBMIT_WEEKDAY, "lundi"), 0)
        if today.weekday() != weekday:
            _LOGGER.debug("BesanconEmoiCoordinator._async_maybe_auto_submit: sortie")
            return
        if self._last_auto_submit is not None and (today - self._last_auto_submit).days < 6:
            _LOGGER.debug("BesanconEmoiCoordinator._async_maybe_auto_submit: sortie")
            return
        unique = len(water_data) == 1
        envois = 0
        for idcontrat, entity_id in correspondances.items():
            contract = water_data[idcontrat].contract
            state = self.hass.states.get(entity_id)
            if state is None or state.state in ("unknown", "unavailable"):
                _LOGGER.warning("Entité source d'index indisponible : %s", entity_id)
                continue
            try:
                index = _index_valide(entity_id, state)
            except UpdateFailed as err:
                if unique:
                    _LOGGER.debug("BesanconEmoiCoordinator._async_maybe_auto_submit: sortie par relance")
                    raise
                # Plusieurs contrats : un relevé non exploitable ne doit pas
                # empêcher l'envoi des autres.
                _LOGGER.warning("Saisie hebdomadaire du contrat %s ignorée : %s", idcontrat, err)
                continue
            try:
                await self.client.async_submit_index(
                    idcontrat,
                    contract.abo,
                    index,
                    date_releve=today,
                    last_index=contract.last_index,
                )
            except BesanconEmoiError as err:
                if unique:
                    msg = f"Saisie hebdomadaire impossible : {err}"
                    _LOGGER.debug(
                        "BesanconEmoiCoordinator._async_maybe_auto_submit: "
                        "sortie par UpdateFailed | cause=%s",
                        err,
                    )
                    raise UpdateFailed(msg) from err
                _LOGGER.warning("Saisie hebdomadaire du contrat %s impossible : %s", idcontrat, err)
                continue
            envois += 1
        if envois:
            self._last_auto_submit = today
            self.hass.config_entries.async_update_entry(
                self.entry,
                options={**self.entry.options, "last_auto_submit": today.isoformat()},
            )
        _LOGGER.debug("BesanconEmoiCoordinator._async_maybe_auto_submit: sortie")

    def _entites_par_contrat(self, water_data: dict[str, ContractData]) -> dict[str, str]:
        """Appariement contrat d'eau → entité source d'index.

        L'appariement explicite (option « index_entities ») prime ; sans
        appariement, l'entité unique sert de repli pour un compte à contrat
        unique seulement.
        """
        _LOGGER.debug(
            "BesanconEmoiCoordinator._entites_par_contrat: entrée | water_data=%s", resumer(water_data)
        )
        declarees = {
            key: value
            for key, value in (self.entry.options.get(CONF_INDEX_ENTITIES) or {}).items()
            if value and key in water_data
        }
        if declarees:
            _LOGGER.debug("BesanconEmoiCoordinator._entites_par_contrat: sortie | %s", resumer(declarees))
            return declarees
        if len(water_data) == 1:
            entity_id = self.entry.options.get(CONF_INDEX_ENTITY)
            if entity_id:
                _LOGGER.debug("BesanconEmoiCoordinator._entites_par_contrat: sortie")
                return {next(iter(water_data)): entity_id}
        _LOGGER.debug("BesanconEmoiCoordinator._entites_par_contrat: sortie")
        return {}


def _index_valide(entity_id: str, state: State) -> int:
    """Valide l'index absolu publié par une entité, ou lève UpdateFailed.

    L'entité doit publier l'index absolu en m³ : des litres seraient
    transmis comme un index m³ et fausseraient la facturation.
    """
    _LOGGER.debug("_index_valide: entrée | entity_id=%s, state=%s", resumer(entity_id), resumer(state))
    unite = state.attributes.get("unit_of_measurement")
    if unite and unite.strip().lower() in ("l", "litre", "litres"):
        msg = (
            f"Entité {entity_id} en litres : l'index absolu en m³ est requis (aucune conversion automatique)"
        )
        _LOGGER.debug("_index_valide: sortie par UpdateFailed")
        raise UpdateFailed(msg)
    try:
        index = int(float(state.state))
    except ValueError as err:
        msg = f"Valeur non numérique sur {entity_id} : {state.state}"
        _LOGGER.debug("_index_valide: sortie par UpdateFailed | cause=%s", err)
        raise UpdateFailed(msg) from err
    if index < 0:
        msg = f"Index négatif refusé sur {entity_id} : {state.state}"
        _LOGGER.debug("_index_valide: sortie par UpdateFailed")
        raise UpdateFailed(msg)
    _LOGGER.debug("_index_valide: sortie | %s", resumer(index))
    return index


def _parse_iso(value: str | None) -> date | None:
    """Analyse une date ISO 8601 (aaaa-mm-jj)."""
    _LOGGER.debug("_parse_iso: entrée | value=%s", resumer(value))
    if not value:
        _LOGGER.debug("_parse_iso: sortie | None")
        return None
    try:
        _LOGGER.debug("_parse_iso: sortie")
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        _LOGGER.debug("_parse_iso: sortie | None")
        return None
