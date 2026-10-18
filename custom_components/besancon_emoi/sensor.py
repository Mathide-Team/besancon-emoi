"""Capteurs des contrats d'eau « Besançon et moi »."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CURRENCY_EURO, UnitOfMass, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import BesanconEmoiConfigEntry
from .api import ContractData, WasteContractData
from .const import DOMAIN, MANUFACTURER
from .coordinator import BesanconEmoiCoordinator


@dataclass(frozen=True, kw_only=True)
class BesanconEmoiSensorDescription(SensorEntityDescription):
    """Description des capteurs Besançon et moi."""


WASTE_SENSORS: dict[str, BesanconEmoiSensorDescription] = {
    "pesee_derniere": BesanconEmoiSensorDescription(
        key="pesee_derniere",
        translation_key="pesee_derniere",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    "pesee_derniere_poids": BesanconEmoiSensorDescription(
        key="pesee_derniere_poids",
        translation_key="pesee_derniere_poids",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    "pesee_jaune_derniere": BesanconEmoiSensorDescription(
        key="pesee_jaune_derniere",
        translation_key="pesee_jaune_derniere",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
    "pesee_jaune_derniere_poids": BesanconEmoiSensorDescription(
        key="pesee_jaune_derniere_poids",
        translation_key="pesee_jaune_derniere_poids",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
}


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
    """Ajoute les capteurs de tous les contrats (eau et déchets)."""
    coordinator: BesanconEmoiCoordinator = entry.runtime_data
    entities: list[BesanconEmoiSensorEntity] = []
    for contract_data in coordinator.data.values():
        if isinstance(contract_data, WasteContractData):
            for sensor_key in WASTE_SENSORS:
                entities.append(BesanconEmoiWasteSensorEntity(coordinator, contract_data, sensor_key))
        else:
            for sensor_key in SENSORS:
                entities.append(BesanconEmoiSensorEntity(coordinator, contract_data, sensor_key))
    async_add_entities(entities)


class BesanconEmoiWasteSensorEntity(CoordinatorEntity[BesanconEmoiCoordinator], SensorEntity):
    """Capteur rattaché à un contrat déchets."""

    entity_description: BesanconEmoiSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BesanconEmoiCoordinator,
        waste_data: WasteContractData,
        sensor_key: str,
    ) -> None:
        """Initialise le capteur."""
        super().__init__(coordinator)
        self.entity_description = WASTE_SENSORS[sensor_key]
        self.waste_data = waste_data
        self._sensor_key = sensor_key
        contract = waste_data.contract
        self._attr_unique_id = f"dechets_{contract.idcontrat}_{sensor_key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"dechets-{contract.idcontrat}")},
            "name": f"Déchets {contract.idcontrat}",
            "manufacturer": MANUFACTURER,
        }

    def _waste_data(self) -> WasteContractData | None:
        """Données du contrat déchets depuis le coordinateur."""
        return self.coordinator.data.get(self.waste_data.contract.idcontrat)

    @property
    def native_value(self) -> object:
        """Valeur du capteur."""
        data = self._waste_data()
        if data is None:
            return None
        jaune = self._sensor_key.startswith("pesee_jaune")
        pesees = data.pesees_jaune if jaune else data.pesees
        if not pesees:
            return None
        latest = pesees[0]
        if self._sensor_key.endswith("_poids"):
            return latest.poids_kg
        if latest.date is None:
            return None
        # Home Assistant exige un horodatage avec fuseau : l'heure du
        # portail est locale, on lui rattache le fuseau configuré.
        try:
            valeur = datetime.strptime(
                f"{latest.date.isoformat()} {latest.heure or '00h00'}",
                "%Y-%m-%d %Hh%M",
            )
        except ValueError:
            return None
        fuseau: tzinfo | None = None
        try:
            fuseau = ZoneInfo(str(self.hass.config.time_zone))
        except (ValueError, ZoneInfoNotFoundError):
            fuseau = None
        if fuseau is None:
            fuseau = dt_util.UTC
        return valeur.replace(tzinfo=fuseau)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Attributs complémentaires du capteur."""
        data = self._waste_data()
        attributes: dict[str, object] = {
            "contrat": self.waste_data.contract.idcontrat,
        }
        if data is None:
            return attributes
        jaune = self._sensor_key.startswith("pesee_jaune")
        pesees = data.pesees_jaune if jaune else data.pesees
        if pesees:
            latest = pesees[0]
            attributes["date_pesee"] = latest.date.isoformat() if latest.date else None
            attributes["heure_pesee"] = latest.heure
            attributes["type_de_bac"] = latest.type_bac
            attributes["volume_l"] = latest.volume_l
            attributes["vidages"] = latest.vidages
            attributes["no_bac"] = latest.no_bac
            attributes["no_puce"] = latest.no_puce
            attributes["pesees"] = [
                {
                    "date": p.date.isoformat() if p.date else None,
                    "heure": p.heure,
                    "poids_kg": p.poids_kg,
                    "type_de_bac": p.type_bac,
                    "volume_l": p.volume_l,
                    "vidages": p.vidages,
                    "no_bac": p.no_bac,
                }
                for p in pesees[:20]
            ]
        return attributes


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
