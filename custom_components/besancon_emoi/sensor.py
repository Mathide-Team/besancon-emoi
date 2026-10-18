"""Capteurs des contrats d'eau « Besançon et moi »."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CURRENCY_EURO, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BesanconEmoiConfigEntry
from .api import ContractData
from .const import DOMAIN, MANUFACTURER
from .coordinator import BesanconEmoiCoordinator


@dataclass(frozen=True, kw_only=True)
class BesanconEmoiSensorDescription(SensorEntityDescription):
    """Description des capteurs Besançon et moi."""


SENSORS: dict[str, BesanconEmoiSensorDescription] = {
    "index": BesanconEmoiSensorDescription(
        key="index",
        translation_key="index",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
    ),
    "conso_moyenne": BesanconEmoiSensorDescription(
        key="conso_moyenne",
        translation_key="conso_moyenne",
        native_unit_of_measurement="m³/j",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
    ),
    "dernier_prelevement": BesanconEmoiSensorDescription(
        key="dernier_prelevement",
        translation_key="dernier_prelevement",
        native_unit_of_measurement=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
    ),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BesanconEmoiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Ajoute les capteurs de tous les contrats."""
    coordinator: BesanconEmoiCoordinator = entry.runtime_data
    entities: list[BesanconEmoiSensorEntity] = []
    for contract_data in coordinator.data.values():
        for sensor_key in SENSORS:
            entities.append(BesanconEmoiSensorEntity(coordinator, contract_data, sensor_key))
    async_add_entities(entities)


class BesanconEmoiSensorEntity(CoordinatorEntity[BesanconEmoiCoordinator], SensorEntity):
    """Capteur rattaché à un contrat d'eau."""

    entity_description: BesanconEmoiSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BesanconEmoiCoordinator,
        contract_data: ContractData,
        sensor_key: str,
    ) -> None:
        """Initialise le capteur."""
        super().__init__(coordinator)
        self.entity_description = SENSORS[sensor_key]
        self.contract_data = contract_data
        self._sensor_key = sensor_key
        contract = contract_data.contract
        self._attr_unique_id = f"{contract.idcontrat}_{sensor_key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, contract.idcontrat)},
            "name": f"Compteur d'eau {contract.idcontrat}",
            "manufacturer": MANUFACTURER,
        }

    def _contract_data(self) -> ContractData | None:
        """Données du contrat depuis le coordinateur."""
        return self.coordinator.data.get(self.contract_data.contract.idcontrat)

    @property
    def native_value(self) -> float | None:
        """Valeur du capteur."""
        data = self._contract_data()
        if data is None:
            return None
        if self._sensor_key == "index":
            if not data.index_history:
                return None
            return data.index_history[0].index
        if self._sensor_key == "conso_moyenne":
            if not data.index_history:
                return None
            return data.index_history[0].conso_jour
        if self._sensor_key == "dernier_prelevement":
            if not data.bills:
                return None
            return data.bills[0].montant_preleve
        return None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Attributs complémentaires du capteur."""
        data = self._contract_data()
        attributes: dict[str, object] = {"contrat": self.contract_data.contract.idcontrat}
        if data is None:
            return attributes
        if self._sensor_key == "index" and data.index_history:
            latest = data.index_history[0]
            attributes["date_releve"] = latest.date.isoformat()
            attributes["type_releve"] = latest.releve
            attributes["historique"] = [
                {"date": r.date.isoformat(), "index": r.index} for r in data.index_history[:10]
            ]
        if self._sensor_key == "dernier_prelevement" and data.bills:
            latest_bill = data.bills[0]
            attributes["date_prelevement"] = latest_bill.date.isoformat() if latest_bill.date else None
            attributes["numero_facture"] = latest_bill.facture
            # Lien relatif du PDF de la facture sur le portail.
            attributes["lien_facture"] = latest_bill.pdf_path
            attributes["prelevements"] = [
                {
                    "facture": b.facture,
                    "date": b.date.isoformat() if b.date else None,
                    "montant_preleve": b.montant_preleve,
                }
                for b in data.bills[:10]
            ]
        return attributes
