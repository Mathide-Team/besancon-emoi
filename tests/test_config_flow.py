"""Tests du flux de configuration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.besancon_emoi.api import BesanconEmoiAuthError, BesanconEmoiError
from custom_components.besancon_emoi.const import (
    CONF_AUTO_SUBMIT,
    CONF_INDEX_ENTITY,
    CONF_SUBMIT_WEEKDAY,
    DOMAIN,
)

EMAIL = "exemple@fr.fr"
PASSWORD = "secret"


async def test_flux_configuration_succes(hass: HomeAssistant) -> None:
    """Le flux crée une entrée après connexion réussie."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    with patch(
        "custom_components.besancon_emoi.config_flow.BesanconEmoiClient", autospec=True
    ) as mock_client:
        mock_client.return_value.async_login = AsyncMock()
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert EMAIL in result["title"]


async def test_flux_identifiants_invalides(hass: HomeAssistant) -> None:
    """Des identifiants refusés affichent une erreur."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch(
        "custom_components.besancon_emoi.config_flow.BesanconEmoiClient", autospec=True
    ) as mock_client:
        mock_client.return_value.async_login = AsyncMock(side_effect=BesanconEmoiAuthError("refusé"))
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: "faux"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "invalid_auth"}


async def test_flux_portail_indisponible(hass: HomeAssistant) -> None:
    """Un portail inaccessible affiche une erreur."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch(
        "custom_components.besancon_emoi.config_flow.BesanconEmoiClient", autospec=True
    ) as mock_client:
        mock_client.return_value.async_login = AsyncMock(side_effect=BesanconEmoiError("indisponible"))
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}


async def test_options(hass: HomeAssistant) -> None:
    """Les options de saisie hebdomadaire sont enregistrées."""
    entry = hass.config_entries.async_entries(DOMAIN)
    assert entry == []
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    mock_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    # Première étape : options de saisie + commune (liste indisponible
    # dans les tests : le champ reste un texte libre).
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_AUTO_SUBMIT: True,
            CONF_SUBMIT_WEEKDAY: "lundi",
            CONF_INDEX_ENTITY: "sensor.compteur_index",
        },
    )
    await hass.async_block_till_done()
    # Deuxième étape : rue de collecte — vide, pas de calendrier.
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"collecte_rue": ""},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_entry.options[CONF_AUTO_SUBMIT] is True
    assert mock_entry.options["collecte_rue"] == ""
