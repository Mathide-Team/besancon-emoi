"""Calendrier de collecte des déchets (open data Grand Besançon)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BesanconEmoiConfigEntry
from .const import DOMAIN, MANUFACTURER
from .coordinator import BesanconEmoiCoordinator
from .journal import resumer
from .opendata import CollecteData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BesanconEmoiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Ajoute le calendrier de collecte."""
    _LOGGER.debug(
        "async_setup_entry: entrée | hass=%s, entry=%s, async_add_entities=%s",
        resumer(hass),
        resumer(entry),
        resumer(async_add_entities),
    )
    coordinator: BesanconEmoiCoordinator = entry.runtime_data
    async_add_entities([BesanconEmoiCollecteCalendarEntity(coordinator)])
    _LOGGER.debug("async_setup_entry: sortie")


class BesanconEmoiCollecteCalendarEntity(CoordinatorEntity[BesanconEmoiCoordinator], CalendarEntity):
    """Calendrier des collectes de résiduels et de recyclables."""

    _attr_has_entity_name = True
    _attr_unique_id = "collecte_calendrier"

    def __init__(self, coordinator: BesanconEmoiCoordinator) -> None:
        """Initialise le calendrier."""
        _LOGGER.debug(
            "BesanconEmoiCollecteCalendarEntity.__init__: entrée | coordinator=%s", resumer(coordinator)
        )
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "collecte-dechets")},
            "name": "Collecte déchets",
            "manufacturer": MANUFACTURER,
        }
        _LOGGER.debug("BesanconEmoiCollecteCalendarEntity.__init__: sortie")

    def _collecte(self) -> CollecteData | None:
        """Données de collecte depuis le coordinateur."""
        _LOGGER.debug("BesanconEmoiCollecteCalendarEntity._collecte: entrée")
        data = self.coordinator.data.get("collecte")
        _LOGGER.debug("BesanconEmoiCollecteCalendarEntity._collecte: sortie")
        return data if isinstance(data, CollecteData) else None

    @property
    def event(self) -> CalendarEvent | None:
        """Prochaine collecte (résiduels ou recyclables, la plus proche)."""
        _LOGGER.debug("BesanconEmoiCollecteCalendarEntity.event: entrée")
        collecte = self._collecte()
        if collecte is None:
            _LOGGER.debug("BesanconEmoiCollecteCalendarEntity.event: sortie | None")
            return None
        aujourdhui = date.today()
        candidates: list[tuple[date, str]] = []
        adresse = collecte.principale()
        if adresse is None:
            _LOGGER.debug("BesanconEmoiCollecteCalendarEntity.event: sortie | None")
            return None
        for libelle, horaire in (
            ("Collecte résiduels", adresse.residuels),
            ("Collecte recyclables", adresse.recyclables),
        ):
            if horaire is None:
                continue
            prochaine = horaire.prochaine(aujourdhui)
            if prochaine is not None:
                candidates.append((prochaine, libelle))
        if not candidates:
            _LOGGER.debug("BesanconEmoiCollecteCalendarEntity.event: sortie | None")
            return None
        prochaine, libelle = min(candidates)
        _LOGGER.debug("BesanconEmoiCollecteCalendarEntity.event: sortie")
        return CalendarEvent(
            summary=libelle,
            start=prochaine,
            end=prochaine + timedelta(days=1),
        )

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Liste les collectes entre deux dates."""
        _LOGGER.debug(
            "BesanconEmoiCollecteCalendarEntity.async_get_events: "
            "entrée | hass=%s, start_date=%s, end_date=%s",
            resumer(hass),
            resumer(start_date),
            resumer(end_date),
        )
        collecte = self._collecte()
        if collecte is None:
            _LOGGER.debug("BesanconEmoiCollecteCalendarEntity.async_get_events: sortie")
            return []
        adresse = collecte.principale()
        if adresse is None:
            _LOGGER.debug("BesanconEmoiCollecteCalendarEntity.async_get_events: sortie")
            return []
        evenements: list[CalendarEvent] = []
        for libelle, horaire in (
            ("Collecte résiduels", adresse.residuels),
            ("Collecte recyclables", adresse.recyclables),
        ):
            if horaire is None:
                continue
            for jour in horaire.dates_dans(start_date.date(), end_date.date()):
                evenements.append(
                    CalendarEvent(
                        summary=libelle,
                        start=jour,
                        end=jour + timedelta(days=1),
                    )
                )
        evenements.sort(key=lambda evenement: evenement.start)
        _LOGGER.debug("BesanconEmoiCollecteCalendarEntity.async_get_events: sortie | %s", resumer(evenements))
        return evenements
