"""Le tableau de bord d'exemple (exemples/tableau-de-bord.yaml) se rend sans erreur (issue #12)."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.besancon_emoi.const import CONF_COMMUNE, CONF_NUMERO, CONF_RUE, DOMAIN
from custom_components.besancon_emoi.opendata import (
    PARITE_IMPAIRES,
    PARITE_PAIRES,
    CollecteAdresse,
    CollecteHoraire,
)
from tests.test_init import EMAIL, IDCONTRAT, PASSWORD, _configurer, _contract_data

VUE = yaml.safe_load((Path(__file__).parents[1] / "exemples" / "tableau-de-bord.yaml").read_text())
MARKDOWN = {c.get("title", ""): c["content"] for c in VUE["cards"] if c["type"] == "markdown"}


def _rendre(hass: HomeAssistant, gabarit: str) -> str:
    return Template(gabarit, hass).async_render(parse_result=False)


async def _configurer_avec_collecte(hass: HomeAssistant) -> None:
    """Compte d'eau factice et collecte d'une adresse (open data simulé)."""
    hass.config.language = "fr"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options={CONF_COMMUNE: "25999", CONF_RUE: "0042", CONF_NUMERO: "3"},
    )
    entry.add_to_hass(hass)
    with (
        patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client,
        patch("custom_components.besancon_emoi.GrandBesanconOpendataClient") as mock_opendata,
    ):
        instance = mock_client.return_value
        instance.async_login = AsyncMock()
        instance.async_get_contracts = AsyncMock(return_value=[_contract_data().contract])
        instance.async_get_index_history = AsyncMock(return_value=_contract_data().index_history)
        instance.async_get_bills = AsyncMock(return_value=[])
        instance.async_get_waste_contracts = AsyncMock(return_value=[])
        mock_opendata.return_value.async_get_collecte = AsyncMock(
            return_value=[
                CollecteAdresse(
                    adresse="3 Impasse de Test, 25999 VILLE DE TEST",
                    residuels=CollecteHoraire(jour="mardi", parite=PARITE_PAIRES),
                    recyclables=CollecteHoraire(jour="vendredi", parite=PARITE_IMPAIRES),
                )
            ]
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_contrat_eau(hass: HomeAssistant) -> None:
    await _configurer(hass)
    contrats = _rendre(hass, MARKDOWN["Mes contrats"])
    assert f"### Compteur d'eau {IDCONTRAT}" in contrats
    lignes = [ligne for ligne in contrats.splitlines() if ligne.startswith("|")]
    # En-tête, séparateur, puis une ligne par capteur du contrat (4).
    assert len(lignes) == 6
    assert all(ligne.count("|") == 3 for ligne in lignes)
    assert any("Index du compteur" in ligne and "322" in ligne and "m³" in ligne for ligne in lignes)
    assert "Collecte non configurée" in _rendre(hass, MARKDOWN["Prochaines collectes"])


async def test_collecte(hass: HomeAssistant) -> None:
    await _configurer_avec_collecte(hass)
    collectes = _rendre(hass, MARKDOWN["Prochaines collectes"])
    lignes = [ligne for ligne in collectes.splitlines() if "mdi:" in ligne]
    # Une ligne par flux, avec le jour et l'échéance en clair.
    assert len(lignes) == 2
    assert any("mdi:trash-can-outline" in ligne and "mardi" in ligne for ligne in lignes)
    assert any("mdi:recycle" in ligne and "vendredi" in ligne for ligne in lignes)
    assert all(re.search(r"aujourd'hui|demain|dans \d+ jours", ligne) for ligne in lignes)
    assert "Adresse : 3 Impasse de Test" in collectes
    # La collecte n'apparaît pas comme un contrat ; le compteur d'eau, oui.
    contrats = _rendre(hass, MARKDOWN["Mes contrats"])
    assert "Collecte déchets" not in contrats
    assert "### Compteur d'eau" in contrats


def test_cartes_integrees_uniquement() -> None:
    assert all(not c["type"].startswith("custom:") for c in VUE["cards"])
