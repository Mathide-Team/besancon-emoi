"""Tests de l'installation : capteurs, action de saisie, saisie hebdomadaire."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.besancon_emoi.api import Bill, Contract, ContractData, IndexReading
from custom_components.besancon_emoi.const import (
    CONF_AUTO_SUBMIT,
    CONF_INDEX_ENTITY,
    CONF_SUBMIT_WEEKDAY,
    DOMAIN,
    WEEKDAYS,
)

EMAIL = "exemple@fr.fr"
PASSWORD = "secret"
IDCONTRAT = "111111-02"
IDCONTRAT_SLUG = "111111_02"
ABO = "99999"


def _contract_data() -> ContractData:
    """Données synthétiques d'un contrat."""
    return ContractData(
        contract=Contract(idcontrat=IDCONTRAT, abo=ABO, last_index=322),
        index_history=[
            IndexReading(
                date=date(2026, 8, 18),
                index=322,
                releve="Relevé par télé-relève",
                conso_jour=0.241,
            ),
            IndexReading(
                date=date(2026, 2, 24),
                index=281,
                releve="Relevé par télé-relève",
                conso_jour=0.275,
            ),
        ],
        bills=[
            Bill(
                facture="70001",
                date=date(2026, 9, 15),
                contrat=IDCONTRAT,
                montant_preleve=-0.99,
                pdf_path="/facture.pdf",
            )
        ],
    )


async def _configurer(hass: HomeAssistant, options: dict | None = None) -> tuple[MockConfigEntry, AsyncMock]:
    """Installe l'intégration avec un client factice."""
    hass.config.language = "fr"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options=options or {},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_login = AsyncMock()
        instance.async_get_contracts = AsyncMock(return_value=[_contract_data().contract])
        instance.async_get_index_history = AsyncMock(return_value=_contract_data().index_history)
        instance.async_get_bills = AsyncMock(return_value=_contract_data().bills)
        instance.async_submit_index = AsyncMock()
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry, instance


async def test_capteurs(hass: HomeAssistant) -> None:
    """Les capteurs d'index, de consommation et de facture sont créés."""
    entry, _ = await _configurer(hass)
    state = hass.states.get(f"sensor.compteur_d_eau_{IDCONTRAT_SLUG}_index_du_compteur")
    assert state is not None
    assert state.state == "322"
    conso = hass.states.get(f"sensor.compteur_d_eau_{IDCONTRAT_SLUG}_consommation_moyenne_journaliere")
    assert conso is not None
    facture = hass.states.get(f"sensor.compteur_d_eau_{IDCONTRAT_SLUG}_dernier_prelevement")
    assert facture.state == "-0.99"
    assert entry.state is ConfigEntryState.LOADED


async def test_service_saisir_index(hass: HomeAssistant) -> None:
    """L'action saisir_index transmet l'index au portail."""
    _, instance = await _configurer(hass)
    await hass.services.async_call(
        DOMAIN,
        "saisir_index",
        {"index": 330, "date_releve": "2026-10-18"},
        blocking=True,
    )
    instance.async_submit_index.assert_awaited_once_with(
        IDCONTRAT, ABO, 330, date_releve=date(2026, 10, 18), last_index=322
    )


async def test_saisie_hebdomadaire_automatique(hass: HomeAssistant) -> None:
    """La saisie hebdomadaire lit l'index sur l'entité source et transmet."""
    aujourdhui = WEEKDAYS[datetime.now().weekday()]
    hass.states.async_set("sensor.compteur_wmbus_index", "325")
    entry, instance = await _configurer(
        hass,
        options={
            CONF_AUTO_SUBMIT: True,
            CONF_SUBMIT_WEEKDAY: aujourdhui,
            CONF_INDEX_ENTITY: "sensor.compteur_wmbus_index",
        },
    )
    coordinator = entry.runtime_data
    await coordinator.async_request_refresh()
    await hass.async_block_till_done()
    instance.async_submit_index.assert_awaited_once()
    args = instance.async_submit_index.await_args
    assert args.args[0] == IDCONTRAT
    assert args.args[2] == 325
    # La date de dernière transmission est mémorisée : pas de doublon.
    await coordinator.async_request_refresh()
    await hass.async_block_till_done()
    assert instance.async_submit_index.await_count == 1


async def test_saisie_hebdomadaire_desactivee(hass: HomeAssistant) -> None:
    """Sans l'option, aucune saisie automatique n'est effectuée."""
    hass.states.async_set("sensor.compteur_wmbus_index", "325")
    entry, instance = await _configurer(hass)
    coordinator = entry.runtime_data
    await coordinator.async_request_refresh()
    await hass.async_block_till_done()
    instance.async_submit_index.assert_not_awaited()
