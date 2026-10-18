"""Tests du calendrier de collecte (open data Grand Besançon)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform as entity_platform_helper
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.besancon_emoi.const import (
    CONF_COMMUNE,
    CONF_NUMERO,
    CONF_RUE,
    DOMAIN,
)
from custom_components.besancon_emoi.opendata import (
    PARITE_IMPAIRES,
    PARITE_PAIRES,
    PARITE_TOUTES,
    CollecteAdresse,
    CollecteHoraire,
    GrandBesanconOpendataClient,
)
from tests.test_init import EMAIL, PASSWORD

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _socket_autorise(socket_enabled):
    """Autorise le serveur HTTP local des tests."""
    yield


def _app() -> web.Application:
    """Serveur de test reproduisant l'open data (sans authentification)."""

    async def page(request: web.Request) -> web.Response:
        return web.Response(
            text=(FIXTURES / "collecte_page.html").read_text(),
            content_type="text/html",
        )

    async def resultat(request: web.Request) -> web.Response:
        return web.Response(
            text=(FIXTURES / "collecte_resultat.html").read_text(),
            content_type="text/html",
        )

    async def rues(request: web.Request) -> web.Response:
        return web.json_response(json.loads((FIXTURES / "rues_collecte.json").read_text()))

    app = web.Application()
    app.router.add_get("/opendata/dataset/joursDeCollecteDechets/", page)
    app.router.add_post("/opendata/dataset/joursDeCollecteDechets/", resultat)
    app.router.add_get("/besanconfr.php", rues)
    return app


@pytest.fixture
async def client():
    server = TestServer(_app())
    test_client = TestClient(server)
    await test_client.start_server()
    yield test_client
    await test_client.close()


def _opendata(client: TestClient) -> GrandBesanconOpendataClient:
    return GrandBesanconOpendataClient(
        client.session,
        dataset_url=str(client.make_url("/opendata/dataset/joursDeCollecteDechets/")),
        streets_url=str(client.make_url("/besanconfr.php")) + "?method=x&insee={insee}",
    )


async def test_communes(client: TestClient) -> None:
    """Les communes sont lues depuis le formulaire public."""
    opendata = _opendata(client)
    communes = await opendata.async_get_communes()
    assert [c.insee for c in communes] == ["25056", "25112", "25999"]
    assert communes[1].nom == "Chalezeule"


async def test_rues(client: TestClient) -> None:
    """Les rues d'une commune sont triées par nom."""
    opendata = _opendata(client)
    rues = await opendata.async_get_streets("25999")
    assert [r.code for r in rues] == ["0001", "0042", "0007"]
    assert rues[1].nom == "Impasse de Test"


async def test_collecte(client: TestClient) -> None:
    """Le formulaire renvoie les horaires des deux formats de parité."""
    opendata = _opendata(client)
    adresses = await opendata.async_get_collecte("25999", "0042", "3")
    assert len(adresses) == 2
    premiere = adresses[0]
    assert premiere.adresse.startswith("3 Impasse de Test")
    assert premiere.residuels == CollecteHoraire(jour="mardi", parite=PARITE_PAIRES)
    assert premiere.recyclables == CollecteHoraire(jour="mardi", parite=PARITE_IMPAIRES)
    assert premiere.calendrier_pdf == (
        "https://datasets.grandbesancon.fr/fichiers/dechets/CALENDRIER-COLLECTE-TEST.pdf"
    )
    # Le format « toutes les semaines » (Besançon) est aussi reconnu.
    assert adresses[1].residuels == CollecteHoraire(jour="jeudi", parite=PARITE_TOUTES)
    assert adresses[1].recyclables == CollecteHoraire(jour="vendredi", parite=PARITE_TOUTES)


async def test_collecte_sans_numero(client: TestClient) -> None:
    """Le numéro est facultatif."""
    opendata = _opendata(client)
    adresses = await opendata.async_get_collecte("25999", "0042")
    assert len(adresses) == 2


def test_horaire_prochaine_et_dates() -> None:
    """La parité des semaines ISO pilote le calcul des dates.

    Vérifié sur les pesées réelles du portail : les collectes
    « semaines paires » tombent bien les semaines ISO paires.
    """
    # Le 18/10/2026 est un dimanche de la semaine ISO 42 (paire).
    horaire = CollecteHoraire(jour="mardi", parite=PARITE_PAIRES)
    assert horaire.prochaine(date(2026, 10, 18)) == date(2026, 10, 27)
    # Semaine impaire : le mardi 20/10 est en semaine 43 (impaire).
    horaire = CollecteHoraire(jour="mardi", parite=PARITE_IMPAIRES)
    assert horaire.prochaine(date(2026, 10, 18)) == date(2026, 10, 20)
    # Toutes les semaines : le jour même compte.
    horaire = CollecteHoraire(jour="dimanche", parite=PARITE_TOUTES)
    assert horaire.prochaine(date(2026, 10, 18)) == date(2026, 10, 18)
    # Sur une période : les mardis des semaines ISO paires d'octobre 2026
    # (13/10 = semaine 42, 27/10 = semaine 44).
    horaire = CollecteHoraire(jour="mardi", parite=PARITE_PAIRES)
    assert horaire.dates_dans(date(2026, 10, 1), date(2026, 10, 31)) == [
        date(2026, 10, 13),
        date(2026, 10, 27),
    ]


async def test_coordinator_collecte(hass: HomeAssistant) -> None:
    """Le calendrier configuré alimente les données du coordinateur."""
    from custom_components.besancon_emoi.coordinator import BesanconEmoiCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options={CONF_COMMUNE: "25999", CONF_RUE: "0042", CONF_NUMERO: "3"},
    )
    entry.add_to_hass(hass)
    server = TestServer(_app())
    test_client = TestClient(server)
    await test_client.start_server()
    try:
        opendata = GrandBesanconOpendataClient(
            test_client.session,
            dataset_url=str(test_client.make_url("/opendata/dataset/joursDeCollecteDechets/")),
            streets_url=str(test_client.make_url("/besanconfr.php")) + "?method=x&insee={insee}",
        )
        with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
            from tests.test_init import _contract_data

            instance = mock_client.return_value
            instance.async_login = AsyncMock()
            instance.async_get_contracts = AsyncMock(return_value=[_contract_data().contract])
            instance.async_get_index_history = AsyncMock(return_value=[])
            instance.async_get_bills = AsyncMock(return_value=[])
            instance.async_get_waste_contracts = AsyncMock(return_value=[])
            coordinator = BesanconEmoiCoordinator(hass, entry, instance, opendata)
            data = await coordinator._async_update_data()
    finally:
        await test_client.close()
    collecte = data["collecte"]
    assert collecte.commune == "25999"
    assert collecte.principale().residuels.parite == PARITE_PAIRES


async def test_coordinator_sans_collecte(hass: HomeAssistant) -> None:
    """Sans open data configuré, la mise à jour du portail reste intacte."""
    from custom_components.besancon_emoi.coordinator import BesanconEmoiCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        from tests.test_init import _contract_data

        instance = mock_client.return_value
        instance.async_login = AsyncMock()
        instance.async_get_contracts = AsyncMock(return_value=[_contract_data().contract])
        instance.async_get_index_history = AsyncMock(return_value=[])
        instance.async_get_bills = AsyncMock(return_value=[])
        instance.async_get_waste_contracts = AsyncMock(return_value=[])
        coordinator = BesanconEmoiCoordinator(hass, entry, instance)
        data = await coordinator._async_update_data()
    assert "collecte" not in data


async def test_capteurs_et_calendrier(hass: HomeAssistant) -> None:
    """Les capteurs et le calendrier de collecte sont exposés."""
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
        from tests.test_init import _contract_data

        instance = mock_client.return_value
        instance.async_login = AsyncMock()
        instance.async_get_contracts = AsyncMock(return_value=[_contract_data().contract])
        instance.async_get_index_history = AsyncMock(return_value=[])
        instance.async_get_bills = AsyncMock(return_value=[])
        instance.async_get_waste_contracts = AsyncMock(return_value=[])
        opendata_instance = mock_opendata.return_value
        opendata_instance.async_get_collecte = AsyncMock(
            return_value=[
                CollecteAdresse(
                    adresse="3 Impasse de Test, 25999 VILLE DE TEST",
                    residuels=CollecteHoraire(jour="mardi", parite=PARITE_PAIRES),
                    recyclables=CollecteHoraire(jour="mardi", parite=PARITE_IMPAIRES),
                )
            ]
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    residuels = hass.states.get("sensor.collecte_dechets_collecte_residuels")
    assert residuels is not None
    assert residuels.state == "mardi"
    assert residuels.attributes["parite"] == "paires"
    assert residuels.attributes["adresse"].startswith("3 Impasse de Test")
    assert "prochaine_collecte" in residuels.attributes
    recyclables = hass.states.get("sensor.collecte_dechets_collecte_recyclables")
    assert recyclables is not None
    assert recyclables.state == "mardi"
    # Calendrier : les collectes paires et impaires alternent en octobre.
    plateformes = entity_platform_helper.async_get_platforms(hass, DOMAIN)
    entites = [e for p in plateformes for e in p.entities.values()]
    calendriers = [e for e in entites if e.__class__.__name__ == "BesanconEmoiCollecteCalendarEntity"]
    assert len(calendriers) == 1
    evenements = await calendriers[0].async_get_events(hass, datetime(2026, 10, 1), datetime(2026, 10, 31))
    resumes = [e.summary for e in evenements]
    assert resumes.count("Collecte résiduels") == 2
    assert resumes.count("Collecte recyclables") == 2
