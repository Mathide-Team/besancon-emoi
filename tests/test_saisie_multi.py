"""Tests de la saisie hebdomadaire avec plusieurs contrats d'eau (#4).

L'appariement explicite « index_entities » (contrat → entité source)
permet l'automatisme sur un compte à plusieurs contrats ; sans
appariement, le comportement historique est conservé (contrat unique
seulement, jamais le même index à plusieurs compteurs).
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.besancon_emoi.api import Contract, ContractData
from custom_components.besancon_emoi.const import (
    CONF_AUTO_SUBMIT,
    CONF_INDEX_ENTITIES,
    CONF_INDEX_ENTITY,
    CONF_SUBMIT_WEEKDAY,
    DOMAIN,
    WEEKDAYS,
)
from custom_components.besancon_emoi.coordinator import BesanconEmoiCoordinator
from tests.test_init import EMAIL, IDCONTRAT, IDCONTRAT_SLUG, PASSWORD, _contract_data

CONTRAT_B = "222222-03"
ABO_B = "88888"


def _contrat_b() -> ContractData:
    return ContractData(
        contract=Contract(idcontrat=CONTRAT_B, abo=ABO_B, last_index=100),
        index_history=[],
        bills=[],
    )


async def test_saisie_hebdo_appariement_deux_contrats(hass: HomeAssistant) -> None:
    """Deux contrats appariés : chaque compteur reçoit son propre index."""
    aujourdhui = WEEKDAYS[datetime.now().weekday()]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options={
            CONF_AUTO_SUBMIT: True,
            CONF_SUBMIT_WEEKDAY: aujourdhui,
            CONF_INDEX_ENTITIES: {
                IDCONTRAT: "sensor.compteur_a_index",
                CONTRAT_B: "sensor.compteur_b_index",
            },
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(
            return_value=[_contract_data().contract, _contrat_b().contract]
        )
        instance.async_get_index_history = AsyncMock(return_value=[])
        instance.async_get_bills = AsyncMock(return_value=[])
        instance.async_submit_index = AsyncMock()
        hass.states.async_set("sensor.compteur_a_index", "325")
        hass.states.async_set("sensor.compteur_b_index", "102")
        coordinator = BesanconEmoiCoordinator(hass, entry, instance)
        data = await coordinator._async_update_data()
        assert len(data) == 2
        assert instance.async_submit_index.await_count == 2
        soumissions = {(call.args[0], call.args[2]) for call in instance.async_submit_index.await_args_list}
        assert soumissions == {(IDCONTRAT, 325), (CONTRAT_B, 102)}
        # Le verrou hebdomadaire est posé pour la session entière.
        assert entry.options["last_auto_submit"] == datetime.now().date().isoformat()


async def test_saisie_hebdo_appariement_partiel(hass: HomeAssistant) -> None:
    """Un seul contrat apparié : seul celui-là est transmis."""
    aujourdhui = WEEKDAYS[datetime.now().weekday()]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options={
            CONF_AUTO_SUBMIT: True,
            CONF_SUBMIT_WEEKDAY: aujourdhui,
            CONF_INDEX_ENTITIES: {CONTRAT_B: "sensor.compteur_b_index"},
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(
            return_value=[_contract_data().contract, _contrat_b().contract]
        )
        instance.async_get_index_history = AsyncMock(return_value=[])
        instance.async_get_bills = AsyncMock(return_value=[])
        instance.async_submit_index = AsyncMock()
        hass.states.async_set("sensor.compteur_b_index", "102")
        coordinator = BesanconEmoiCoordinator(hass, entry, instance)
        data = await coordinator._async_update_data()
        assert len(data) == 2
        instance.async_submit_index.assert_awaited_once()
        call = instance.async_submit_index.await_args
        assert call.args[0] == CONTRAT_B
        assert call.args[2] == 102


async def test_saisie_hebdo_appariement_entite_indisponible(hass: HomeAssistant) -> None:
    """Une entité indisponible n'empêche pas l'envoi de l'autre contrat."""
    aujourdhui = WEEKDAYS[datetime.now().weekday()]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options={
            CONF_AUTO_SUBMIT: True,
            CONF_SUBMIT_WEEKDAY: aujourdhui,
            CONF_INDEX_ENTITIES: {
                IDCONTRAT: "sensor.compteur_a_index",
                CONTRAT_B: "sensor.compteur_b_index",
            },
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(
            return_value=[_contract_data().contract, _contrat_b().contract]
        )
        instance.async_get_index_history = AsyncMock(return_value=[])
        instance.async_get_bills = AsyncMock(return_value=[])
        instance.async_submit_index = AsyncMock()
        # Seule l'entité du contrat B existe : le contrat A est ignoré.
        hass.states.async_set("sensor.compteur_b_index", "102")
        coordinator = BesanconEmoiCoordinator(hass, entry, instance)
        data = await coordinator._async_update_data()
        assert data
        instance.async_submit_index.assert_awaited_once()
        assert instance.async_submit_index.await_args.args[0] == CONTRAT_B


async def test_saisie_hebdo_appariement_litres(hass: HomeAssistant) -> None:
    """Une entité en litres est ignorée, l'autre contrat part bien."""
    aujourdhui = WEEKDAYS[datetime.now().weekday()]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options={
            CONF_AUTO_SUBMIT: True,
            CONF_SUBMIT_WEEKDAY: aujourdhui,
            CONF_INDEX_ENTITIES: {
                IDCONTRAT: "sensor.compteur_a_litres",
                CONTRAT_B: "sensor.compteur_b_index",
            },
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(
            return_value=[_contract_data().contract, _contrat_b().contract]
        )
        instance.async_get_index_history = AsyncMock(return_value=[])
        instance.async_get_bills = AsyncMock(return_value=[])
        instance.async_submit_index = AsyncMock()
        hass.states.async_set("sensor.compteur_a_litres", "325000", {"unit_of_measurement": "L"})
        hass.states.async_set("sensor.compteur_b_index", "102")
        coordinator = BesanconEmoiCoordinator(hass, entry, instance)
        data = await coordinator._async_update_data()
        assert data
        instance.async_submit_index.assert_awaited_once()
        assert instance.async_submit_index.await_args.args[0] == CONTRAT_B


async def test_options_champ_par_contrat(hass: HomeAssistant) -> None:
    """Le flux d'options propose un champ entité par contrat d'eau connu."""
    from custom_components.besancon_emoi.config_flow import BesanconEmoiOptionsFlow
    from custom_components.besancon_emoi.opendata import OpendataError

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD})
    entry.add_to_hass(hass)
    # Le flux lit les contrats connus dans le coordinateur (runtime_data).
    entry.runtime_data = type(
        "Coordonnateur",
        (),
        {"data": {IDCONTRAT: _contract_data(), CONTRAT_B: _contrat_b()}},
    )()
    flow = BesanconEmoiOptionsFlow()
    flow.hass = hass
    # OptionsFlow résout son entrée via handler → id d'entrée.
    flow.handler = entry.entry_id

    with patch(
        "custom_components.besancon_emoi.config_flow.GrandBesanconOpendataClient.async_get_communes",
        side_effect=OpendataError("indisponible"),
    ):
        resultat = await flow.async_step_init(None)
    champs = set(resultat["data_schema"].schema)
    assert f"{CONF_INDEX_ENTITY}::{IDCONTRAT}" in champs
    assert f"{CONF_INDEX_ENTITY}::{CONTRAT_B}" in champs

    # La soumission replie les champs en appariement contrat → entité.
    with patch(
        "custom_components.besancon_emoi.config_flow.GrandBesanconOpendataClient.async_get_streets",
        side_effect=OpendataError("indisponible"),
    ):
        resultat = await flow.async_step_init(
            {
                CONF_AUTO_SUBMIT: True,
                CONF_SUBMIT_WEEKDAY: "lundi",
                CONF_INDEX_ENTITY: "",
                f"{CONF_INDEX_ENTITY}::{IDCONTRAT}": "sensor.compteur_a_index",
                f"{CONF_INDEX_ENTITY}::{CONTRAT_B}": "",
                "collecte_commune": "",
            }
        )
        resultat = await flow.async_step_collecte({"collecte_rue": ""})
    assert resultat["type"] == "create_entry"
    assert resultat["data"][CONF_INDEX_ENTITIES] == {IDCONTRAT: "sensor.compteur_a_index"}


async def test_capteurs_multiples_contrats(hass: HomeAssistant) -> None:
    """Deux contrats d'eau produisent chacun leurs capteurs (smoke test)."""
    from custom_components.besancon_emoi.api import IndexReading

    hass.config.language = "fr"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options={CONF_AUTO_SUBMIT: False},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_login = AsyncMock()
        instance.async_get_contracts = AsyncMock(
            return_value=[_contract_data().contract, _contrat_b().contract]
        )
        # Chaque contrat a son propre historique d'index (le capteur index
        # publie index_history[0]).
        instance.async_get_index_history = AsyncMock(
            side_effect=lambda abo: {
                "99999": _contract_data().index_history,
                ABO_B: [
                    IndexReading(date=datetime(2026, 9, 1).date(), index=100, releve="Relevé", conso_jour=0.1)
                ],
            }[abo]
        )
        instance.async_get_bills = AsyncMock(return_value=[])
        instance.async_submit_index = AsyncMock()
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    index_a = hass.states.get(f"sensor.compteur_d_eau_{IDCONTRAT_SLUG}_index_du_compteur")
    assert index_a is not None and index_a.state == "322"
    index_b = hass.states.get("sensor.compteur_d_eau_222222_03_index_du_compteur")
    assert index_b is not None and index_b.state == "100"
