"""Capteurs des contrats d'eau « Besançon et moi »."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
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
from .journal import resumer
from .opendata import CollecteData, CollecteHoraire

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class BesanconEmoiSensorDescription(SensorEntityDescription):
    """Description des capteurs Besançon et moi."""


COLLECTE_SENSORS: dict[str, BesanconEmoiSensorDescription] = {
    "collecte_residuels": BesanconEmoiSensorDescription(
        key="collecte_residuels",
        translation_key="collecte_residuels",
    ),
    "collecte_recyclables": BesanconEmoiSensorDescription(
        key="collecte_recyclables",
        translation_key="collecte_recyclables",
    ),
}


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
    "derniere_facture_ttc": BesanconEmoiSensorDescription(
        key="derniere_facture_ttc",
        translation_key="derniere_facture_ttc",
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
    _LOGGER.debug(
        "async_setup_entry: entrée | hass=%s, entry=%s, async_add_entities=%s",
        resumer(hass),
        resumer(entry),
        resumer(async_add_entities),
    )
    coordinator: BesanconEmoiCoordinator = entry.runtime_data
    entities: list[BesanconEmoiSensorEntity] = []
    for contract_data in coordinator.data.values():
        if isinstance(contract_data, CollecteData):
            for sensor_key in COLLECTE_SENSORS:
                entities.append(BesanconEmoiCollecteSensorEntity(coordinator, contract_data, sensor_key))
        elif isinstance(contract_data, WasteContractData):
            for sensor_key in WASTE_SENSORS:
                entities.append(BesanconEmoiWasteSensorEntity(coordinator, contract_data, sensor_key))
        else:
            for sensor_key in SENSORS:
                entities.append(BesanconEmoiSensorEntity(coordinator, contract_data, sensor_key))
    async_add_entities(entities)
    _LOGGER.debug("async_setup_entry: sortie")


class BesanconEmoiCollecteSensorEntity(CoordinatorEntity[BesanconEmoiCoordinator], SensorEntity):
    """Capteur de jour de collecte (résiduels ou recyclables)."""

    entity_description: BesanconEmoiSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BesanconEmoiCoordinator,
        collecte: CollecteData,
        sensor_key: str,
    ) -> None:
        """Initialise le capteur."""
        _LOGGER.debug(
            "BesanconEmoiCollecteSensorEntity.__init__: entrée | coordinator=%s, collecte=%s, sensor_key=%s",
            resumer(coordinator),
            resumer(collecte),
            resumer(sensor_key),
        )
        super().__init__(coordinator)
        self.entity_description = COLLECTE_SENSORS[sensor_key]
        self._sensor_key = sensor_key
        self._attr_unique_id = f"collecte_{sensor_key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "collecte-dechets")},
            "name": "Collecte déchets",
            "manufacturer": MANUFACTURER,
        }
        _LOGGER.debug("BesanconEmoiCollecteSensorEntity.__init__: sortie")

    def _collecte(self) -> CollecteData | None:
        """Données de collecte depuis le coordinateur."""
        _LOGGER.debug("BesanconEmoiCollecteSensorEntity._collecte: entrée")
        data = self.coordinator.data.get("collecte")
        _LOGGER.debug("BesanconEmoiCollecteSensorEntity._collecte: sortie")
        return data if isinstance(data, CollecteData) else None

    def _horaire(self, collecte: CollecteData) -> CollecteHoraire | None:
        """Horaire affiché par ce capteur."""
        _LOGGER.debug("BesanconEmoiCollecteSensorEntity._horaire: entrée | collecte=%s", resumer(collecte))
        adresse = collecte.principale()
        if adresse is None:
            _LOGGER.debug("BesanconEmoiCollecteSensorEntity._horaire: sortie | None")
            return None
        if self._sensor_key == "collecte_residuels":
            _LOGGER.debug("BesanconEmoiCollecteSensorEntity._horaire: sortie")
            return adresse.residuels
        _LOGGER.debug("BesanconEmoiCollecteSensorEntity._horaire: sortie")
        return adresse.recyclables

    @property
    def native_value(self) -> object:
        """Valeur du capteur : le jour de collecte."""
        _LOGGER.debug("BesanconEmoiCollecteSensorEntity.native_value: entrée")
        collecte = self._collecte()
        if collecte is None:
            _LOGGER.debug("BesanconEmoiCollecteSensorEntity.native_value: sortie | None")
            return None
        horaire = self._horaire(collecte)
        _LOGGER.debug("BesanconEmoiCollecteSensorEntity.native_value: sortie")
        return horaire.jour if horaire else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Attributs complémentaires du capteur."""
        _LOGGER.debug("BesanconEmoiCollecteSensorEntity.extra_state_attributes: entrée")
        attributes: dict[str, object] = {}
        collecte = self._collecte()
        if collecte is None:
            _LOGGER.debug(
                "BesanconEmoiCollecteSensorEntity.extra_state_attributes: sortie | %s", resumer(attributes)
            )
            return attributes
        adresse = collecte.principale()
        if adresse is not None:
            attributes["adresse"] = adresse.adresse
            if adresse.calendrier_pdf:
                attributes["calendrier_pdf"] = adresse.calendrier_pdf
            if len(collecte.adresses) > 1:
                # Certaines rues ont un calendrier par côté ou par
                # numéro : les autres adresses sont listées pour information.
                attributes["autres_adresses"] = [
                    {
                        "adresse": a.adresse,
                        "residuels": a.residuels.jour if a.residuels else None,
                        "recyclables": a.recyclables.jour if a.recyclables else None,
                    }
                    for a in collecte.adresses[1:10]
                ]
        horaire = self._horaire(collecte)
        if horaire is not None:
            attributes["parite"] = horaire.parite
            prochaine = horaire.prochaine(date.today())
            if prochaine is not None:
                attributes["prochaine_collecte"] = prochaine.isoformat()
                attributes["jours_avant_collecte"] = (prochaine - date.today()).days
        _LOGGER.debug(
            "BesanconEmoiCollecteSensorEntity.extra_state_attributes: sortie | %s", resumer(attributes)
        )
        return attributes


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
        _LOGGER.debug(
            "BesanconEmoiWasteSensorEntity.__init__: entrée | coordinator=%s, waste_data=%s, sensor_key=%s",
            resumer(coordinator),
            resumer(waste_data),
            resumer(sensor_key),
        )
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
        _LOGGER.debug("BesanconEmoiWasteSensorEntity.__init__: sortie")

    def _waste_data(self) -> WasteContractData | None:
        """Données du contrat déchets depuis le coordinateur."""
        return self.coordinator.data.get(self.waste_data.contract.idcontrat)

    @property
    def native_value(self) -> object:
        """Valeur du capteur."""
        _LOGGER.debug("BesanconEmoiWasteSensorEntity.native_value: entrée")
        data = self._waste_data()
        if data is None:
            _LOGGER.debug("BesanconEmoiWasteSensorEntity.native_value: sortie | None")
            return None
        jaune = self._sensor_key.startswith("pesee_jaune")
        pesees = data.pesees_jaune if jaune else data.pesees
        if not pesees:
            _LOGGER.debug("BesanconEmoiWasteSensorEntity.native_value: sortie | None")
            return None
        latest = pesees[0]
        if self._sensor_key.endswith("_poids"):
            _LOGGER.debug("BesanconEmoiWasteSensorEntity.native_value: sortie")
            return latest.poids_kg
        if latest.date is None:
            _LOGGER.debug("BesanconEmoiWasteSensorEntity.native_value: sortie | None")
            return None
        # Home Assistant exige un horodatage avec fuseau : l'heure du
        # portail est locale, on lui rattache le fuseau configuré.
        try:
            valeur = datetime.strptime(
                f"{latest.date.isoformat()} {latest.heure or '00h00'}",
                "%Y-%m-%d %Hh%M",
            )
        except ValueError:
            _LOGGER.debug("BesanconEmoiWasteSensorEntity.native_value: sortie | None")
            return None
        fuseau: tzinfo | None = None
        try:
            fuseau = ZoneInfo(str(self.hass.config.time_zone))
        except (ValueError, ZoneInfoNotFoundError):
            _LOGGER.debug(
                "BesanconEmoiWasteSensorEntity.native_value: (ValueError, ZoneInfoNotFoundError) interceptée"
            )
            fuseau = None
        if fuseau is None:
            fuseau = dt_util.UTC
        _LOGGER.debug("BesanconEmoiWasteSensorEntity.native_value: sortie")
        return valeur.replace(tzinfo=fuseau)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Attributs complémentaires du capteur."""
        _LOGGER.debug("BesanconEmoiWasteSensorEntity.extra_state_attributes: entrée")
        data = self._waste_data()
        attributes: dict[str, object] = {
            "contrat": self.waste_data.contract.idcontrat,
        }
        if data is None:
            _LOGGER.debug(
                "BesanconEmoiWasteSensorEntity.extra_state_attributes: sortie | %s", resumer(attributes)
            )
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
        _LOGGER.debug(
            "BesanconEmoiWasteSensorEntity.extra_state_attributes: sortie | %s", resumer(attributes)
        )
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
        _LOGGER.debug(
            "BesanconEmoiSensorEntity.__init__: entrée | coordinator=%s, contract_data=%s, sensor_key=%s",
            resumer(coordinator),
            resumer(contract_data),
            resumer(sensor_key),
        )
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
        _LOGGER.debug("BesanconEmoiSensorEntity.__init__: sortie")

    def _contract_data(self) -> ContractData | None:
        """Données du contrat depuis le coordinateur."""
        return self.coordinator.data.get(self.contract_data.contract.idcontrat)

    @property
    def native_value(self) -> float | None:
        """Valeur du capteur."""
        _LOGGER.debug("BesanconEmoiSensorEntity.native_value: entrée")
        data = self._contract_data()
        if data is None:
            _LOGGER.debug("BesanconEmoiSensorEntity.native_value: sortie | None")
            return None
        if self._sensor_key == "index":
            if not data.index_history:
                _LOGGER.debug("BesanconEmoiSensorEntity.native_value: sortie | None")
                return None
            _LOGGER.debug("BesanconEmoiSensorEntity.native_value: sortie")
            return data.index_history[0].index
        if self._sensor_key == "conso_moyenne":
            if not data.index_history:
                _LOGGER.debug("BesanconEmoiSensorEntity.native_value: sortie | None")
                return None
            _LOGGER.debug("BesanconEmoiSensorEntity.native_value: sortie")
            return data.index_history[0].conso_jour
        if self._sensor_key == "dernier_prelevement":
            if not data.bills:
                _LOGGER.debug("BesanconEmoiSensorEntity.native_value: sortie | None")
                return None
            _LOGGER.debug("BesanconEmoiSensorEntity.native_value: sortie")
            return data.bills[0].montant_preleve
        if self._sensor_key == "derniere_facture_ttc":
            if not data.bills:
                _LOGGER.debug("BesanconEmoiSensorEntity.native_value: sortie | None")
                return None
            # Total lu dans le PDF de la facture : inconnu si le PDF
            # n'a pas pu être téléchargé ou lu lors de la mise à jour.
            _LOGGER.debug("BesanconEmoiSensorEntity.native_value: sortie")
            return data.bills[0].total_ttc
        _LOGGER.debug("BesanconEmoiSensorEntity.native_value: sortie | None")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Attributs complémentaires du capteur."""
        _LOGGER.debug("BesanconEmoiSensorEntity.extra_state_attributes: entrée")
        data = self._contract_data()
        attributes: dict[str, object] = {"contrat": self.contract_data.contract.idcontrat}
        if data is None:
            _LOGGER.debug("BesanconEmoiSensorEntity.extra_state_attributes: sortie | %s", resumer(attributes))
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
                    "total_ttc": b.total_ttc,
                }
                for b in data.bills[:10]
            ]
        if self._sensor_key == "derniere_facture_ttc" and data.bills:
            latest_bill = data.bills[0]
            attributes["facture"] = latest_bill.facture
            attributes["date_facture"] = latest_bill.date.isoformat() if latest_bill.date else None
            attributes["montant_preleve"] = latest_bill.montant_preleve
            # Lien relatif du PDF de la facture sur le portail.
            attributes["lien_facture"] = latest_bill.pdf_path
        _LOGGER.debug("BesanconEmoiSensorEntity.extra_state_attributes: sortie | %s", resumer(attributes))
        return attributes
