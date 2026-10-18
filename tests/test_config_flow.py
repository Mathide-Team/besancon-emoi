"""Tests du flux de configuration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import voluptuous as vol
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
from custom_components.besancon_emoi.opendata import Commune, OpendataError, Rue

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
    """Les options de saisie hebdomadaire sont enregistrées.

    L'open data est simulé indisponible : les champs commune et rue
    restent des textes libres (repli documenté).
    """
    entry = hass.config_entries.async_entries(DOMAIN)
    assert entry == []
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    mock_entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.config_flow.GrandBesanconOpendataClient") as mock_opendata:
        instance = mock_opendata.return_value
        instance.async_get_communes = AsyncMock(side_effect=OpendataError("open data indisponible"))
        instance.async_get_streets = AsyncMock(side_effect=OpendataError("open data indisponible"))
        result = await hass.config_entries.options.async_init(mock_entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        # Première étape : options de saisie + commune en texte libre.
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


def _valeur_par_defaut(schema: vol.Schema) -> object:
    """Valeur par défaut du premier champ d'un schéma de formulaire."""
    champ = next(iter(schema.schema))
    valeur = champ.default
    if callable(valeur):  # Home Assistant enveloppe les défauts
        valeur = valeur()
    return valeur


async def test_options_collecte_activation(hass: HomeAssistant) -> None:
    """Le parcours nominal configure le calendrier de collecte."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    mock_entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.config_flow.GrandBesanconOpendataClient") as mock_opendata:
        instance = mock_opendata.return_value
        instance.async_get_communes = AsyncMock(
            return_value=[
                Commune(insee="25056", nom="Besançon"),
                Commune(insee="25999", nom="Ville de Test"),
            ]
        )
        instance.async_get_streets = AsyncMock(
            return_value=[
                Rue(code="0001", nom="Grande Rue"),
                Rue(code="0042", nom="Impasse de Test"),
            ]
        )
        result = await hass.config_entries.options.async_init(mock_entry.entry_id)
        # Liste déroulante des communes.
        assert result["type"] is FlowResultType.FORM
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_AUTO_SUBMIT: False,
                CONF_SUBMIT_WEEKDAY: "lundi",
                CONF_INDEX_ENTITY: "",
                "collecte_commune": "25999",
            },
        )
        await hass.async_block_till_done()
        # Liste déroulante des rues de la commune choisie.
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "collecte"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"collecte_rue": "0042", "collecte_numero": "3"},
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_entry.options["collecte_commune"] == "25999"
    assert mock_entry.options["collecte_rue"] == "0042"
    assert mock_entry.options["collecte_numero"] == "3"


async def test_options_collecte_changement_commune(hass: HomeAssistant) -> None:
    """Changer de commune réinitialise rue et numéro."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options={
            "collecte_commune": "25056",
            "collecte_rue": "0001",
            "collecte_numero": "7",
        },
    )
    mock_entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.config_flow.GrandBesanconOpendataClient") as mock_opendata:
        instance = mock_opendata.return_value
        instance.async_get_communes = AsyncMock(
            return_value=[
                Commune(insee="25056", nom="Besançon"),
                Commune(insee="25999", nom="Ville de Test"),
            ]
        )
        # La nouvelle commune propose le même code de rue : il faut
        # pourtant repartir d'une saisie vierge.
        instance.async_get_streets = AsyncMock(return_value=[Rue(code="0001", nom="Grande Rue")])
        result = await hass.config_entries.options.async_init(mock_entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_AUTO_SUBMIT: False,
                CONF_SUBMIT_WEEKDAY: "lundi",
                CONF_INDEX_ENTITY: "",
                "collecte_commune": "25999",
            },
        )
        await hass.async_block_till_done()
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "collecte"
        champ_rue = next(iter(result["data_schema"].schema))
        assert _valeur_par_defaut(result["data_schema"]) == ""
        assert champ_rue is not None
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"collecte_rue": "0001"},
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_entry.options["collecte_commune"] == "25999"
    assert mock_entry.options["collecte_rue"] == "0001"
    # Le numéro de l'ancienne commune n'a pas été conservé.
    assert mock_entry.options.get("collecte_numero", "") == ""


async def test_options_collecte_desactivation(hass: HomeAssistant) -> None:
    """Une rue vide désactive le calendrier de collecte."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    mock_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options={
            "collecte_commune": "25999",
            "collecte_rue": "0042",
            "collecte_numero": "3",
        },
    )
    mock_entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.config_flow.GrandBesanconOpendataClient") as mock_opendata:
        instance = mock_opendata.return_value
        instance.async_get_communes = AsyncMock(return_value=[Commune(insee="25999", nom="Ville de Test")])
        instance.async_get_streets = AsyncMock(return_value=[Rue(code="0042", nom="Impasse de Test")])
        result = await hass.config_entries.options.async_init(mock_entry.entry_id)
        # Même commune : la rue enregistrée est présélectionnée.
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_AUTO_SUBMIT: False,
                CONF_SUBMIT_WEEKDAY: "lundi",
                CONF_INDEX_ENTITY: "",
                "collecte_commune": "25999",
            },
        )
        await hass.async_block_till_done()
        assert result["type"] is FlowResultType.FORM
        assert _valeur_par_defaut(result["data_schema"]) == "0042"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"collecte_rue": ""},
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_entry.options["collecte_rue"] == ""
