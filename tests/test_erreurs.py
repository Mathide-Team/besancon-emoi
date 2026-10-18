"""Tests des chemins d'erreur : coordinateur, installation, capteurs."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryAuthFailed, ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.besancon_emoi.api import BesanconEmoiAuthError, BesanconEmoiError, Contract
from custom_components.besancon_emoi.const import (
    CONF_AUTO_SUBMIT,
    CONF_INDEX_ENTITY,
    CONF_SUBMIT_WEEKDAY,
    DOMAIN,
    WEEKDAYS,
)
from tests.test_init import EMAIL, PASSWORD, _contract_data


async def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    return entry


async def test_authentification_refusee(hass: HomeAssistant) -> None:
    """Des identifiants refusés marquent l'entrée en erreur."""
    entry = await _entry(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        mock_client.return_value.async_login = AsyncMock(side_effect=BesanconEmoiAuthError("refusé"))
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN):
        hass.config_entries.flow.async_abort(flow["flow_id"])
    await hass.config_entries.async_remove(entry.entry_id)


async def test_portail_indisponible(hass: HomeAssistant) -> None:
    """Un portail indisponible retente plus tard."""
    entry = await _entry(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        mock_client.return_value.async_login = AsyncMock(side_effect=BesanconEmoiError("indisponible"))
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_dechargement(hass: HomeAssistant) -> None:
    """L'entrée peut être déchargée puis rechargée."""
    entry = await _entry(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_login = AsyncMock()
        instance.async_get_contracts = AsyncMock(return_value=[_contract_data().contract])
        instance.async_get_index_history = AsyncMock(return_value=_contract_data().index_history)
        instance.async_get_bills = AsyncMock(return_value=_contract_data().bills)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.NOT_LOADED
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED


async def _coordinator_avec(hass: HomeAssistant, instance: AsyncMock, options: dict):
    """Construit un coordinateur sans passer par l'installation complète."""
    from custom_components.besancon_emoi.coordinator import BesanconEmoiCoordinator

    entry = await _entry(hass)
    hass.config_entries.async_update_entry(entry, options=options)
    return BesanconEmoiCoordinator(hass, entry, instance)


async def test_mise_a_jour_auth_expiree_reconnexion(hass: HomeAssistant) -> None:
    """Une session expirée est reconnectée puis la mise à jour réussit."""
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        # Premier appel : session expirée ; puis la reconnexion réussit.
        instance.async_get_contracts = AsyncMock(
            side_effect=[BesanconEmoiAuthError("expirée"), [_contract_data().contract]]
        )
        instance.async_get_index_history = AsyncMock(return_value=_contract_data().index_history)
        instance.async_get_bills = AsyncMock(return_value=_contract_data().bills)
        coordinator = await _coordinator_avec(hass, instance, {})
        data = await coordinator._async_update_data()
        assert data
        instance.async_login.assert_awaited_once()
        assert instance.async_get_contracts.await_count == 2


async def test_mise_a_jour_auth_refusee(hass: HomeAssistant) -> None:
    """Des identifiants refusés à la reconnexion démarrent la ré-authentification."""
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(side_effect=BesanconEmoiAuthError("expirée"))
        instance.async_login = AsyncMock(side_effect=BesanconEmoiAuthError("refusé"))
        coordinator = await _coordinator_avec(hass, instance, {})
        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()
        assert instance.async_login.await_count == 1
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN):
            hass.config_entries.flow.async_abort(flow["flow_id"])


async def test_reconnexion_portail_indisponible(hass: HomeAssistant) -> None:
    """Une reconnexion impossible lève UpdateFailed sans ré-authentification."""
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(side_effect=BesanconEmoiAuthError("expirée"))
        instance.async_login = AsyncMock(side_effect=BesanconEmoiError("indisponible"))
        coordinator = await _coordinator_avec(hass, instance, {})
        with pytest.raises(UpdateFailed, match="Reconnexion impossible"):
            await coordinator._async_update_data()


async def test_mise_a_jour_portail_erreur(hass: HomeAssistant) -> None:
    """Une erreur du portail pendant une mise à jour lève UpdateFailed."""
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(
            return_value=[Contract(idcontrat="111111-02", abo="99999", last_index=322)]
        )
        instance.async_get_index_history = AsyncMock(side_effect=BesanconEmoiError("page illisible"))
        instance.async_get_bills = AsyncMock()
        coordinator = await _coordinator_avec(hass, instance, {})
        with pytest.raises(UpdateFailed, match="illisible"):
            await coordinator._async_update_data()


async def test_aucun_contrat(hass: HomeAssistant) -> None:
    """Un compte sans contrat d'eau est accepté mais sans capteurs."""
    entry = await _entry(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_login = AsyncMock()
        instance.async_get_contracts = AsyncMock(return_value=[])
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_saisie_hebdo_sans_entite(hass: HomeAssistant) -> None:
    """Sans entité source, la saisie hebdomadaire est ignorée avec un avertissement."""
    from datetime import datetime

    aujourdhui = WEEKDAYS[datetime.now().weekday()]
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(return_value=[_contract_data().contract])
        instance.async_get_index_history = AsyncMock(return_value=_contract_data().index_history)
        instance.async_get_bills = AsyncMock(return_value=_contract_data().bills)
        instance.async_submit_index = AsyncMock()
        coordinator = await _coordinator_avec(
            hass,
            instance,
            {CONF_AUTO_SUBMIT: True, CONF_SUBMIT_WEEKDAY: aujourdhui},
        )
        data = await coordinator._async_update_data()
        assert data
        instance.async_submit_index.assert_not_awaited()


async def test_saisie_hebdo_entite_indisponible(hass: HomeAssistant) -> None:
    """Une entité source indisponible bloque la mise à jour sans saisie."""
    from datetime import datetime

    aujourdhui = WEEKDAYS[datetime.now().weekday()]
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(return_value=[_contract_data().contract])
        instance.async_get_index_history = AsyncMock(return_value=_contract_data().index_history)
        instance.async_get_bills = AsyncMock(return_value=_contract_data().bills)
        instance.async_submit_index = AsyncMock()
        hass.states.async_set("sensor.compteur_wmbus_index", "unavailable")
        coordinator = await _coordinator_avec(
            hass,
            instance,
            {
                CONF_AUTO_SUBMIT: True,
                CONF_SUBMIT_WEEKDAY: aujourdhui,
                CONF_INDEX_ENTITY: "sensor.compteur_wmbus_index",
            },
        )
        data = await coordinator._async_update_data()
        assert data
        instance.async_submit_index.assert_not_awaited()


async def test_saisie_hebdo_valeur_non_numerique(hass: HomeAssistant) -> None:
    """Une valeur non numérique sur l'entité source bloque la mise à jour."""
    from datetime import datetime

    aujourdhui = WEEKDAYS[datetime.now().weekday()]
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(return_value=[_contract_data().contract])
        instance.async_get_index_history = AsyncMock(return_value=_contract_data().index_history)
        instance.async_get_bills = AsyncMock(return_value=_contract_data().bills)
        instance.async_submit_index = AsyncMock()
        hass.states.async_set("sensor.compteur_wmbus_index", "abc")
        coordinator = await _coordinator_avec(
            hass,
            instance,
            {
                CONF_AUTO_SUBMIT: True,
                CONF_SUBMIT_WEEKDAY: aujourdhui,
                CONF_INDEX_ENTITY: "sensor.compteur_wmbus_index",
            },
        )
        with pytest.raises(UpdateFailed, match="non numérique"):
            await coordinator._async_update_data()
        instance.async_submit_index.assert_not_awaited()


async def test_saisie_hebdo_erreur_portail(hass: HomeAssistant) -> None:
    """Une erreur du portail pendant la saisie hebdomadaire bloque la mise à jour."""
    from datetime import datetime

    aujourdhui = WEEKDAYS[datetime.now().weekday()]
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(return_value=[_contract_data().contract])
        instance.async_get_index_history = AsyncMock(return_value=_contract_data().index_history)
        instance.async_get_bills = AsyncMock(return_value=_contract_data().bills)
        instance.async_submit_index = AsyncMock(side_effect=BesanconEmoiError("refusé"))
        hass.states.async_set("sensor.compteur_wmbus_index", "325")
        coordinator = await _coordinator_avec(
            hass,
            instance,
            {
                CONF_AUTO_SUBMIT: True,
                CONF_SUBMIT_WEEKDAY: aujourdhui,
                CONF_INDEX_ENTITY: "sensor.compteur_wmbus_index",
            },
        )
        with pytest.raises(UpdateFailed, match="refusé"):
            await coordinator._async_update_data()


async def test_service_saisir_index_sans_contrat(hass: HomeAssistant) -> None:
    """L'action sans intégration configurée lève une erreur utilisateur."""
    with patch("custom_components.besancon_emoi._register_services"):
        from custom_components.besancon_emoi import async_setup_entry  # noqa: F401

    # Enregistre le service sans entrée configurée.
    from custom_components.besancon_emoi import _register_services

    _register_services(hass)
    try:
        await hass.services.async_call(DOMAIN, "saisir_index", {"index": 330}, blocking=True)
    except HomeAssistantError:
        pass
    else:
        msg = "Une HomeAssistantError était attendue sans contrat"
        raise AssertionError(msg)
