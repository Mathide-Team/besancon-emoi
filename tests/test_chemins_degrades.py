"""Chemins dégradés : un service partiel ne doit ni casser la mise à jour,
ni produire un état faux (#23).

Couvre ce que les autres fichiers de tests n'atteignaient pas : erreurs
côté déchets et facture PDF, calendrier de collecte indisponible ou vide,
saisie hebdomadaire à plusieurs contrats, capteurs et calendrier sans
données, et erreurs réseau du client open data.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.besancon_emoi.api import (
    BesanconEmoiAuthError,
    BesanconEmoiError,
    Bill,
    Contract,
    WasteContract,
    WasteContractData,
    Weighing,
)
from custom_components.besancon_emoi.calendar import BesanconEmoiCollecteCalendarEntity
from custom_components.besancon_emoi.const import (
    CONF_AUTO_SUBMIT,
    CONF_COMMUNE,
    CONF_INDEX_ENTITIES,
    CONF_RUE,
    CONF_SUBMIT_WEEKDAY,
    DOMAIN,
    WEEKDAYS,
)
from custom_components.besancon_emoi.coordinator import BesanconEmoiCoordinator
from custom_components.besancon_emoi.opendata import (
    PARITE_TOUTES,
    CollecteAdresse,
    CollecteData,
    CollecteHoraire,
    Commune,
    GrandBesanconOpendataClient,
    OpendataError,
    _parse_collecte_page,
)
from custom_components.besancon_emoi.sensor import (
    BesanconEmoiCollecteSensorEntity,
    BesanconEmoiWasteSensorEntity,
)
from tests.test_init import EMAIL, IDCONTRAT, PASSWORD, _contract_data

CONTRAT_B = "222222-03"
DECHETS = WasteContract(idcontrat="D-1", abo="5")


def _entree(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}, options=options or {}
    )
    entry.add_to_hass(hass)
    return entry


def _client_eau(bills: list[Bill] | None = None) -> AsyncMock:
    """Client portail : un contrat d'eau, sans déchets par défaut."""
    client = AsyncMock()
    donnees = _contract_data()
    client.async_get_contracts.return_value = [donnees.contract]
    client.async_get_index_history.return_value = donnees.index_history
    client.async_get_bills.return_value = donnees.bills if bills is None else bills
    client.async_get_waste_contracts.return_value = []
    client.async_download_bill_pdf.return_value = b"%PDF"
    return client


# --- Coordinateur : déchets ----------------------------------------------------


async def test_contrats_dechets_session_expiree_propagee(hass: HomeAssistant) -> None:
    """Une session expirée côté déchets n'est pas avalée : elle remonte
    pour déclencher la reconnexion, comme pour l'eau."""
    client = _client_eau()
    client.async_get_waste_contracts.side_effect = BesanconEmoiAuthError("expirée")
    coordinator = BesanconEmoiCoordinator(hass, _entree(hass), client)
    with (
        patch("custom_components.besancon_emoi.coordinator.total_ttc_du_pdf", return_value=12.5),
        pytest.raises(BesanconEmoiAuthError),
    ):
        await coordinator._async_fetch_data()


async def test_pesees_session_expiree_propagee(hass: HomeAssistant) -> None:
    """Même règle pour les pesées d'un contrat déchets."""
    client = _client_eau()
    client.async_get_waste_contracts.return_value = [DECHETS]
    client.async_get_weighings.side_effect = BesanconEmoiAuthError("expirée")
    coordinator = BesanconEmoiCoordinator(hass, _entree(hass), client)
    with (
        patch("custom_components.besancon_emoi.coordinator.total_ttc_du_pdf", return_value=12.5),
        pytest.raises(BesanconEmoiAuthError),
    ):
        await coordinator._async_fetch_data()


async def test_pesees_indisponibles_eau_preservee(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Des pesées en erreur écartent ce contrat déchets, pas l'eau."""
    client = _client_eau()
    client.async_get_waste_contracts.return_value = [DECHETS]
    client.async_get_weighings.side_effect = BesanconEmoiError("HTTP 500")
    coordinator = BesanconEmoiCoordinator(hass, _entree(hass), client)
    with patch("custom_components.besancon_emoi.coordinator.total_ttc_du_pdf", return_value=12.5):
        data = await coordinator._async_fetch_data()
    assert IDCONTRAT in data
    assert DECHETS.idcontrat not in data
    assert "Pesées indisponibles pour le contrat déchets D-1" in caplog.text


# --- Coordinateur : total TTC de la facture ------------------------------------


async def test_facture_sans_pdf_pas_de_telechargement(hass: HomeAssistant) -> None:
    """Sans lien PDF, rien n'est téléchargé et le total reste inconnu."""
    facture = Bill(
        facture="70001", date=date(2026, 9, 15), contrat=IDCONTRAT, montant_preleve=-0.99, pdf_path=None
    )
    client = _client_eau(bills=[facture])
    coordinator = BesanconEmoiCoordinator(hass, _entree(hass), client)
    data = await coordinator._async_fetch_data()
    client.async_download_bill_pdf.assert_not_awaited()
    assert data[IDCONTRAT].bills[0].total_ttc is None


async def test_pdf_facture_session_expiree_propagee(hass: HomeAssistant) -> None:
    """Le téléchargement du PDF peut aussi révéler une session expirée."""
    client = _client_eau()
    client.async_download_bill_pdf.side_effect = BesanconEmoiAuthError("expirée")
    coordinator = BesanconEmoiCoordinator(hass, _entree(hass), client)
    with pytest.raises(BesanconEmoiAuthError):
        await coordinator._async_fetch_data()


async def test_pdf_facture_illisible(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    """Un PDF illisible laisse le total inconnu, sans bloquer la mise à jour,
    et n'est pas mis en cache : il sera relu au prochain rafraîchissement."""
    client = _client_eau()
    coordinator = BesanconEmoiCoordinator(hass, _entree(hass), client)
    with patch(
        "custom_components.besancon_emoi.coordinator.total_ttc_du_pdf",
        side_effect=ValueError("flux corrompu"),
    ):
        data = await coordinator._async_fetch_data()
    assert data[IDCONTRAT].bills[0].total_ttc is None
    assert "Facture 70001 illisible : flux corrompu" in caplog.text
    assert coordinator._totaux_ttc == {}


async def test_pdf_facture_sans_total(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    """Un PDF lisible mais sans total reconnaissable laisse le capteur inconnu."""
    client = _client_eau()
    coordinator = BesanconEmoiCoordinator(hass, _entree(hass), client)
    with patch("custom_components.besancon_emoi.coordinator.total_ttc_du_pdf", return_value=None):
        data = await coordinator._async_fetch_data()
    assert data[IDCONTRAT].bills[0].total_ttc is None
    assert "Total TTC introuvable dans la facture 70001" in caplog.text
    assert coordinator._totaux_ttc == {}


# --- Coordinateur : calendrier de collecte -------------------------------------


async def test_collecte_open_data_indisponible(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    """L'open data en panne n'empêche pas la mise à jour de l'eau."""
    opendata = AsyncMock()
    opendata.async_get_collecte.side_effect = OpendataError("HTTP 503")
    entry = _entree(hass, {CONF_COMMUNE: "25056", CONF_RUE: "0001"})
    coordinator = BesanconEmoiCoordinator(hass, entry, _client_eau(), opendata)
    with patch("custom_components.besancon_emoi.coordinator.total_ttc_du_pdf", return_value=12.5):
        data = await coordinator._async_fetch_data()
    assert "collecte" not in data
    assert IDCONTRAT in data
    assert "Calendrier de collecte indisponible : HTTP 503" in caplog.text


async def test_collecte_vide_signalee(hass: HomeAssistant, caplog: pytest.LogCaptureFixture) -> None:
    """Une rue sans calendrier est signalée, sans données de collecte vides."""
    opendata = AsyncMock()
    opendata.async_get_collecte.return_value = []
    entry = _entree(hass, {CONF_COMMUNE: "25056", CONF_RUE: "0001"})
    coordinator = BesanconEmoiCoordinator(hass, entry, _client_eau(), opendata)
    with patch("custom_components.besancon_emoi.coordinator.total_ttc_du_pdf", return_value=12.5):
        data = await coordinator._async_fetch_data()
    assert "collecte" not in data
    assert "Calendrier de collecte vide pour la commune 25056 et la rue 0001" in caplog.text


# --- Coordinateur : saisie hebdomadaire à plusieurs contrats -------------------


def _deux_contrats(client: AsyncMock) -> None:
    client.async_get_contracts.return_value = [
        _contract_data().contract,
        Contract(idcontrat=CONTRAT_B, abo="88888", last_index=100),
    ]
    client.async_get_bills.return_value = []


async def test_saisie_hebdo_plusieurs_contrats_sans_appariement(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Sans appariement, rien n'est envoyé : jamais le même index à deux compteurs."""
    client = _client_eau()
    _deux_contrats(client)
    entry = _entree(hass, {CONF_AUTO_SUBMIT: True, CONF_SUBMIT_WEEKDAY: WEEKDAYS[datetime.now().weekday()]})
    coordinator = BesanconEmoiCoordinator(hass, entry, client)
    await coordinator._async_fetch_data()
    client.async_submit_index.assert_not_awaited()
    assert "2 contrats d'eau sur ce compte sans appariement" in caplog.text


async def test_saisie_hebdo_erreur_un_contrat_sur_deux(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Le refus du portail pour un contrat n'empêche pas l'envoi de l'autre."""
    client = _client_eau()
    _deux_contrats(client)
    client.async_submit_index.side_effect = [BesanconEmoiError("refusé"), None]
    entry = _entree(
        hass,
        {
            CONF_AUTO_SUBMIT: True,
            CONF_SUBMIT_WEEKDAY: WEEKDAYS[datetime.now().weekday()],
            CONF_INDEX_ENTITIES: {IDCONTRAT: "sensor.compteur_a", CONTRAT_B: "sensor.compteur_b"},
        },
    )
    hass.states.async_set("sensor.compteur_a", "325")
    hass.states.async_set("sensor.compteur_b", "102")
    coordinator = BesanconEmoiCoordinator(hass, entry, client)
    await coordinator._async_fetch_data()
    assert client.async_submit_index.await_count == 2
    assert f"Saisie hebdomadaire du contrat {IDCONTRAT} impossible : refusé" in caplog.text
    # Un envoi a réussi : le verrou hebdomadaire est posé.
    assert entry.options["last_auto_submit"] == date.today().isoformat()


async def test_saisie_hebdo_verrou_moins_de_six_jours(hass: HomeAssistant) -> None:
    """Un relevé déjà transmis il y a moins de six jours n'est pas renvoyé,
    même si le jour configuré revient (redémarrage, rafraîchissement)."""
    client = _client_eau()
    entry = _entree(
        hass,
        {
            CONF_AUTO_SUBMIT: True,
            CONF_SUBMIT_WEEKDAY: WEEKDAYS[datetime.now().weekday()],
            CONF_INDEX_ENTITIES: {IDCONTRAT: "sensor.compteur_a"},
            "last_auto_submit": (date.today() - timedelta(days=2)).isoformat(),
        },
    )
    hass.states.async_set("sensor.compteur_a", "325")
    coordinator = BesanconEmoiCoordinator(hass, entry, client)
    with patch("custom_components.besancon_emoi.coordinator.total_ttc_du_pdf", return_value=12.5):
        await coordinator._async_fetch_data()
    client.async_submit_index.assert_not_awaited()


# --- Capteurs et calendrier sans données -----------------------------------


def _coordinateur(data: dict) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


def _horaire_inconnu() -> CollecteHoraire:
    """Horaire dont le jour n'existe pas : aucune date ne correspond."""
    return CollecteHoraire(jour="jourdi", parite=PARITE_TOUTES)


def test_capteur_collecte_sans_donnees() -> None:
    """Sans calendrier, ni valeur ni attribut (et pas d'exception)."""
    collecte = CollecteData(commune="25056", rue="0001", adresses=[])
    capteur = BesanconEmoiCollecteSensorEntity(_coordinateur({}), collecte, "collecte_residuels")
    assert capteur.native_value is None
    assert capteur.extra_state_attributes == {}
    # Calendrier présent mais sans adresse : pas d'horaire.
    capteur = BesanconEmoiCollecteSensorEntity(
        _coordinateur({"collecte": collecte}), collecte, "collecte_residuels"
    )
    assert capteur.native_value is None
    assert capteur.extra_state_attributes == {}


def test_capteur_collecte_plusieurs_adresses_et_pdf() -> None:
    """Les autres adresses de la rue et le PDF sont exposés en attributs."""
    lundi = CollecteHoraire(jour="lundi", parite=PARITE_TOUTES)
    collecte = CollecteData(
        commune="25056",
        rue="0001",
        adresses=[
            CollecteAdresse(adresse="1 Grande Rue", residuels=lundi, calendrier_pdf="/cal.pdf"),
            CollecteAdresse(adresse="2 Grande Rue", recyclables=lundi),
        ],
    )
    capteur = BesanconEmoiCollecteSensorEntity(
        _coordinateur({"collecte": collecte}), collecte, "collecte_recyclables"
    )
    attributs = capteur.extra_state_attributes
    assert capteur.native_value is None  # l'adresse principale n'a pas de recyclables
    assert attributs["calendrier_pdf"] == "/cal.pdf"
    assert attributs["autres_adresses"] == [
        {"adresse": "2 Grande Rue", "residuels": None, "recyclables": "lundi"}
    ]
    assert "parite" not in attributs


def test_capteur_collecte_jour_inconnu_sans_prochaine() -> None:
    """Un jour non reconnu n'invente pas de prochaine collecte."""
    collecte = CollecteData(
        commune="25056", rue="0001", adresses=[CollecteAdresse(adresse="1 rue", residuels=_horaire_inconnu())]
    )
    capteur = BesanconEmoiCollecteSensorEntity(
        _coordinateur({"collecte": collecte}), collecte, "collecte_residuels"
    )
    attributs = capteur.extra_state_attributes
    assert attributs["parite"] == PARITE_TOUTES
    assert "prochaine_collecte" not in attributs


def _capteur_dechets(
    hass: HomeAssistant, data: dict, cle: str = "pesee_derniere"
) -> BesanconEmoiWasteSensorEntity:
    capteur = BesanconEmoiWasteSensorEntity(_coordinateur(data), WasteContractData(contract=DECHETS), cle)
    capteur.hass = hass
    return capteur


def _pesee(**valeurs) -> Weighing:
    base = {"date": date(2026, 10, 1), "heure": "06h30", "poids_kg": 12.0, "type_bac": "OM"}
    return Weighing(**(base | valeurs))


async def test_capteur_dechets_donnees_absentes(hass: HomeAssistant) -> None:
    """Contrat disparu ou sans pesée : valeur inconnue, attributs minimaux."""
    capteur = _capteur_dechets(hass, {})
    assert capteur.native_value is None
    assert capteur.extra_state_attributes == {"contrat": "D-1"}
    vide = WasteContractData(contract=DECHETS)
    capteur = _capteur_dechets(hass, {"D-1": vide})
    assert capteur.native_value is None
    assert capteur.extra_state_attributes == {"contrat": "D-1"}


async def test_capteur_dechets_date_ou_heure_invalide(hass: HomeAssistant) -> None:
    """Pesée sans date, ou heure illisible : pas d'horodatage inventé."""
    sans_date = WasteContractData(contract=DECHETS, pesees=[_pesee(date=None)])
    assert _capteur_dechets(hass, {"D-1": sans_date}).native_value is None
    heure_illisible = WasteContractData(contract=DECHETS, pesees=[_pesee(heure="25h99")])
    assert _capteur_dechets(hass, {"D-1": heure_illisible}).native_value is None


async def test_capteur_dechets_fuseau_invalide_repli_utc(hass: HomeAssistant) -> None:
    """Un fuseau HA inconnu se replie sur UTC au lieu d'échouer."""
    donnees = WasteContractData(contract=DECHETS, pesees=[_pesee()])
    capteur = _capteur_dechets(hass, {"D-1": donnees})
    with patch.object(hass.config, "time_zone", "Fuseau/Inexistant"):
        valeur = capteur.native_value
    assert valeur == datetime(2026, 10, 1, 6, 30, tzinfo=valeur.tzinfo)
    assert valeur.utcoffset() == timedelta(0)


async def test_calendrier_sans_donnees(hass: HomeAssistant) -> None:
    """Pas de calendrier, pas d'adresse, ou aucun jour exploitable : rien."""
    debut, fin = datetime(2026, 10, 1), datetime(2026, 10, 31)
    calendrier = BesanconEmoiCollecteCalendarEntity(_coordinateur({}))
    assert calendrier.event is None
    assert await calendrier.async_get_events(hass, debut, fin) == []
    sans_adresse = CollecteData(commune="25056", rue="0001", adresses=[])
    calendrier = BesanconEmoiCollecteCalendarEntity(_coordinateur({"collecte": sans_adresse}))
    assert calendrier.event is None
    assert await calendrier.async_get_events(hass, debut, fin) == []
    inexploitable = CollecteData(
        commune="25056", rue="0001", adresses=[CollecteAdresse(adresse="1 rue", residuels=_horaire_inconnu())]
    )
    calendrier = BesanconEmoiCollecteCalendarEntity(_coordinateur({"collecte": inexploitable}))
    assert calendrier.event is None
    assert await calendrier.async_get_events(hass, debut, fin) == []


# --- Client open data : erreurs réseau et page inattendue ----------------------


@pytest.fixture(autouse=True)
def _socket_autorise(socket_enabled):
    """Autorise le serveur HTTP local des tests."""
    yield


def _app_en_panne() -> web.Application:
    async def erreur(_request: web.Request) -> web.Response:
        return web.Response(status=503)

    async def sans_formulaire(_request: web.Request) -> web.Response:
        return web.Response(text="<html><body>Maintenance</body></html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/panne", erreur)
    app.router.add_post("/panne", erreur)
    app.router.add_get("/rues/{insee}", erreur)
    app.router.add_get("/maintenance", sans_formulaire)
    return app


@pytest.fixture
async def serveur_en_panne():
    client = TestClient(TestServer(_app_en_panne()))
    await client.start_server()
    yield client
    await client.close()


async def test_open_data_erreurs_reseau(serveur_en_panne: TestClient) -> None:
    """Chaque appel en erreur HTTP lève OpendataError (jamais ClientError)."""
    base = str(serveur_en_panne.make_url(""))
    opendata = GrandBesanconOpendataClient(
        serveur_en_panne.session, dataset_url=f"{base}/panne", streets_url=f"{base}/rues/{{insee}}"
    )
    with pytest.raises(OpendataError, match="Open data inaccessible"):
        await opendata.async_get_communes()
    with pytest.raises(OpendataError, match="liste des rues"):
        await opendata.async_get_streets("25056")
    with pytest.raises(OpendataError, match="Open data inaccessible"):
        await opendata.async_get_collecte("25056", "0001")


async def test_open_data_formulaire_absent(serveur_en_panne: TestClient) -> None:
    """Une page sans formulaire (maintenance) est une erreur explicite."""
    base = str(serveur_en_panne.make_url(""))
    opendata = GrandBesanconOpendataClient(serveur_en_panne.session, dataset_url=f"{base}/maintenance")
    with pytest.raises(OpendataError, match="Formulaire de collecte introuvable"):
        await opendata.async_get_communes()


def test_horaire_jour_inconnu() -> None:
    """Un jour non reconnu ne produit pas d'horaire, et la recherche de la
    prochaine date s'arrête au bout de huit semaines."""
    page = "<table><tr><td>1 rue</td><td>Résiduels : jourdi toutes les semaines</td></tr></table>"
    (adresse,) = _parse_collecte_page(page)
    assert adresse.residuels is None
    assert _horaire_inconnu().prochaine(date(2026, 10, 1)) is None


# --- Options : open data indisponible à l'étape des rues -----------------------


async def test_options_rues_indisponibles(hass: HomeAssistant) -> None:
    """Rues injoignables : l'erreur est affichée et la rue reste saisissable."""
    entry = _entree(hass)
    with patch("custom_components.besancon_emoi.config_flow.GrandBesanconOpendataClient") as mock_opendata:
        instance = mock_opendata.return_value
        instance.async_get_communes = AsyncMock(return_value=[Commune(insee="25056", nom="Besançon")])
        instance.async_get_streets = AsyncMock(side_effect=OpendataError("HTTP 503"))
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_AUTO_SUBMIT: False,
                CONF_SUBMIT_WEEKDAY: "lundi",
                "index_entity": "",
                CONF_COMMUNE: "25056",
            },
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "collecte"
    assert result["errors"] == {"base": "cannot_connect"}
