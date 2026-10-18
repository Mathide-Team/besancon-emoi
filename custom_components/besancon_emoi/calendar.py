"""Calendrier de collecte des déchets (open data Grand Besançon)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BesanconEmoiConfigEntry
from .const import DOMAIN, MANUFACTURER
from .coordinator import BesanconEmoiCoordinator
from .opendata import CollecteData


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BesanconEmoiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Ajoute le calendrier de collecte."""
    coordinator: BesanconEmoiCoordinator = entry.runtime_data
    async_add_entities([BesanconEmoiCollecteCalendarEntity(coordinator)])


class BesanconEmoiCollecteCalendarEntity(CoordinatorEntity[BesanconEmoiCoordinator], CalendarEntity):
    """Calendrier des collectes de résiduels et de recyclables."""

    _attr_has_entity_name = True
    _attr_unique_id = "collecte_calendrier"

    def __init__(self, coordinator: BesanconEmoiCoordinator) -> None:
        """Initialise le calendrier."""
        super().__init__(coordinator)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "collecte-dechets")},
            "name": "Collecte déchets",
            "manufacturer": MANUFACTURER,
        }

    def _collecte(self) -> CollecteData | None:
        """Données de collecte depuis le coordinateur."""
        data = self.coordinator.data.get("collecte")
        return data if isinstance(data, CollecteData) else None

    @property
    def event(self) -> CalendarEvent | None:
        """Prochaine collecte (résiduels ou recyclables, la plus proche)."""
        collecte = self._collecte()
        if collecte is None:
            return None
        aujourdhui = date.today()
        candidates: list[tuple[date, str]] = []
        adresse = collecte.principale()
        if adresse is None:
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
            return None
        prochaine, libelle = min(candidates)
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
        collecte = self._collecte()
        if collecte is None:
            return []
        adresse = collecte.principale()
        if adresse is None:
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
        return evenements
