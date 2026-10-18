"""Tests complémentaires : erreurs réseau, cas limites, ré-authentification."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.besancon_emoi.api import (
    BesanconEmoiAuthError,
    BesanconEmoiClient,
    BesanconEmoiError,
    Contract,
)
from custom_components.besancon_emoi.const import CONF_AUTO_SUBMIT, CONF_SUBMIT_WEEKDAY, DOMAIN, WEEKDAYS
from tests.test_api import _build_app
from tests.test_init import EMAIL, IDCONTRAT, IDCONTRAT_SLUG, PASSWORD, _configurer


@pytest.fixture(autouse=True)
def _socket_autorise(socket_enabled):
    """Autorise le serveur HTTP local des tests."""
    yield


async def test_erreur_reseau() -> None:
    """Une erreur réseau devient une BesanconEmoiError."""
    app = _build_app()
    app.router.add_get("/reseau", web.HTTPInternalServerError)
    client = TestClient(TestServer(app))
    await client.start_server()
    api = BesanconEmoiClient(client.session, base_url=str(client.make_url("")))
    try:
        try:
            await api._get_text("/reseau")
        except BesanconEmoiError as err:
            assert "indisponible" in str(err)
        else:
            msg = "Une BesanconEmoiError était attendue (statut 5xx)"
            raise AssertionError(msg)
    finally:
        await client.close()


async def test_acces_refuse() -> None:
    """Un refus d'accès devient une BesanconEmoiAuthError."""
    app = web.Application()

    async def interdit(request: web.Request) -> web.Response:
        return web.Response(status=403, text="Interdit")

    app.router.add_get("/interdit", interdit)
    client = TestClient(TestServer(app))
    await client.start_server()
    api = BesanconEmoiClient(client.session, base_url=str(client.make_url("")))
    try:
        try:
            await api._get_text("/interdit")
        except BesanconEmoiAuthError:
            pass
        else:
            msg = "Une BesanconEmoiAuthError était attendue (statut 403)"
            raise AssertionError(msg)
    finally:
        await client.close()


async def test_connexion_impossible() -> None:
    """Un serveur injoignable lève une BesanconEmoiError."""
    app = _build_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    api = BesanconEmoiClient(client.session, base_url=str(client.make_url(""))[:-1] + "x")
    try:
        try:
            await api._get_text("/prestations/eau/contrats")
        except BesanconEmoiError:
            pass
        else:
            msg = "Une BesanconEmoiError était attendue (connexion impossible)"
            raise AssertionError(msg)
    finally:
        await client.close()


async def test_formulaire_saisie_introuvable() -> None:
    """Un contrat sans formulaire de saisie reste utilisable sans dernier index."""
    app = web.Application()

    async def saisie_vide(request: web.Request) -> web.Response:
        return web.Response(
            text="<html><body><h1>Saisie</h1></body></html>",
            content_type="text/html",
        )

    app.router.add_get("/prestations/eau/saisie/idcontrat/{rest:.*}", saisie_vide)
    client = TestClient(TestServer(app))
    await client.start_server()
    api = BesanconEmoiClient(client.session, base_url=str(client.make_url("")))
    try:
        contract = await api._resolve_contract("111111-02")
        assert contract.last_index is None
    finally:
        await client.close()


async def test_aucun_contrat_portail() -> None:
    """Une page contrats sans lien de saisie renvoie une liste vide."""
    app = web.Application()

    async def vide(request: web.Request) -> web.Response:
        return web.Response(
            text="<html><body><h1>Mes prestations</h1></body></html>",
            content_type="text/html",
        )

    app.router.add_get("/prestations/eau/contrats", vide)
    client = TestClient(TestServer(app))
    await client.start_server()
    api = BesanconEmoiClient(client.session, base_url=str(client.make_url("")))
    try:
        assert await api.async_get_contracts() == []
    finally:
        await client.close()


async def test_saisie_sans_date_ni_dernier_index() -> None:
    """Sans date ni dernier index, le portail est interrogé pour compléter."""
    app = _build_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    api = BesanconEmoiClient(client.session, base_url=str(client.make_url("")))
    try:
        await api.async_login("exemple@fr.fr", "secret")
        await api.async_submit_index("111111-02", "99999", 330)
        submitted = client.app["submitted"]
        assert submitted["lastIndex"] == "322"
        assert submitted["dateReleve"] == date.today().isoformat()
    finally:
        await client.close()


async def test_saisie_refusee_statut() -> None:
    """Une réponse autre que 302 est refusée."""
    app = web.Application()

    async def sans_redirection(request: web.Request) -> web.Response:
        return web.Response(text="<html><body>Erreur</body></html>", content_type="text/html")

    app.router.add_post("/prestations/eau/saisie/idcontrat/{rest:.*}", sans_redirection)
    client = TestClient(TestServer(app))
    await client.start_server()
    api = BesanconEmoiClient(client.session, base_url=str(client.make_url("")))
    try:
        try:
            await api.async_submit_index(
                "111111-02",
                "99999",
                330,
                date_releve=date(2026, 10, 18),
                last_index=322,
            )
        except BesanconEmoiError as err:
            assert "refusée" in str(err)
        else:
            msg = "Une BesanconEmoiError était attendue (statut non 302)"
            raise AssertionError(msg)
    finally:
        await client.close()


async def test_parse_iso_invalide() -> None:
    """Les dates invalides renvoient None sans lever."""
    from custom_components.besancon_emoi.coordinator import _parse_iso

    assert _parse_iso(None) is None
    assert _parse_iso("") is None
    assert _parse_iso("pas-une-date") is None
    assert _parse_iso("2026-10-18") == date(2026, 10, 18)


async def test_reauth(hass: HomeAssistant) -> None:
    """La ré-authentification met à jour les identifiants et recharge."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: "ancien"},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.besancon_emoi.config_flow.BesanconEmoiClient", autospec=True
    ) as mock_client:
        instance = mock_client.return_value
        instance.async_login = AsyncMock()
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": entry.entry_id},
            data=entry.data,
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reauth_confirm"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: "nouveau"}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "nouveau"


async def test_reauth_identifiants_invalides(hass: HomeAssistant) -> None:
    """La ré-authentification affiche une erreur si le portail refuse."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: "ancien"},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.besancon_emoi.config_flow.BesanconEmoiClient", autospec=True
    ) as mock_client:
        mock_client.return_value.async_login = AsyncMock(side_effect=BesanconEmoiAuthError("refusé"))
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: "toujours-faux"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_service_saisir_index_erreur_portail(hass: HomeAssistant) -> None:
    """Une erreur du portail pendant la saisie lève une erreur utilisateur."""
    _, instance = await _configurer(hass)
    instance.async_submit_index = AsyncMock(side_effect=BesanconEmoiError("refusé"))
    try:
        await hass.services.async_call(DOMAIN, "saisir_index", {"index": 330}, blocking=True)
    except HomeAssistantError as err:
        assert "refusé" in str(err)
    else:
        msg = "Une HomeAssistantError était attendue"
        raise AssertionError(msg)


async def test_service_saisir_index_entree_non_chargee(hass: HomeAssistant) -> None:
    """Une entrée non chargée est ignorée par l'action de saisie."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    from custom_components.besancon_emoi import _register_services

    _register_services(hass)
    # L'entrée existe mais n'est pas chargée : coordinator None -> ignorée.
    try:
        await hass.services.async_call(DOMAIN, "saisir_index", {"index": 330}, blocking=True)
    except HomeAssistantError:
        pass
    else:
        msg = "Une HomeAssistantError était attendue (aucun contrat actif)"
        raise AssertionError(msg)


async def test_rechargement_sur_options(hass: HomeAssistant) -> None:
    """Changer les options recharge l'entrée."""
    entry, _ = await _configurer(hass)
    assert entry.state is ConfigEntryState.LOADED
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_login = AsyncMock()
        instance.async_get_contracts = AsyncMock(return_value=[])
        hass.config_entries.async_update_entry(
            entry, options={CONF_AUTO_SUBMIT: True, CONF_SUBMIT_WEEKDAY: "lundi"}
        )
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_jour_de_semaine_different(hass: HomeAssistant) -> None:
    """La saisie hebdomadaire ne s'exécute pas les autres jours."""
    from custom_components.besancon_emoi.coordinator import BesanconEmoiCoordinator

    demain = WEEKDAYS[(datetime.now().weekday() + 1) % 7]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options={CONF_AUTO_SUBMIT: True, CONF_SUBMIT_WEEKDAY: demain},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        from tests.test_init import _contract_data

        instance.async_get_contracts = AsyncMock(return_value=[_contract_data().contract])
        instance.async_get_index_history = AsyncMock(return_value=[])
        instance.async_get_bills = AsyncMock(return_value=[])
        instance.async_submit_index = AsyncMock()
        coordinator = BesanconEmoiCoordinator(hass, entry, instance)
        data = await coordinator._async_update_data()
        assert data
        # Le jour choisi n'est pas aujourd'hui : aucune saisie.
        instance.async_submit_index.assert_not_awaited()


async def test_capteurs_sans_donnees(hass: HomeAssistant) -> None:
    """Des capteurs sans données restent indisponibles sans lever d'erreur."""
    hass.config.language = "fr"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_login = AsyncMock()
        instance.async_get_contracts = AsyncMock(
            return_value=[
                type(
                    "C",
                    (),
                    {"idcontrat": IDCONTRAT, "abo": "99999", "last_index": None},
                )()
            ]
        )
        instance.async_get_index_history = AsyncMock(return_value=[])
        instance.async_get_bills = AsyncMock(return_value=[])
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    for suffix in (
        "index_du_compteur",
        "consommation_moyenne_journaliere",
        "dernier_prelevement",
    ):
        state = hass.states.get(f"sensor.compteur_d_eau_{IDCONTRAT_SLUG}_{suffix}")
        assert state is not None
        assert state.state == "unknown"


async def test_reauth_portail_indisponible(hass: HomeAssistant) -> None:
    """La ré-authentification signale un portail indisponible."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: "ancien"},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.besancon_emoi.config_flow.BesanconEmoiClient", autospec=True
    ) as mock_client:
        mock_client.return_value.async_login = AsyncMock(side_effect=BesanconEmoiError("indisponible"))
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: "motdepasse"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_mise_a_jour_contrats_inaccessibles(hass: HomeAssistant) -> None:
    """Une erreur du portail à la liste des contrats lève UpdateFailed."""
    from custom_components.besancon_emoi.coordinator import BesanconEmoiCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(side_effect=BesanconEmoiError("indisponible"))
        coordinator = BesanconEmoiCoordinator(hass, entry, instance)
        with pytest.raises(UpdateFailed, match="indisponible"):
            await coordinator._async_update_data()


async def test_service_date_datetime(hass: HomeAssistant) -> None:
    """L'action accepte une date fournie en objet datetime."""
    from datetime import datetime

    _, instance = await _configurer(hass)
    await hass.services.async_call(
        DOMAIN,
        "saisir_index",
        {"index": 330, "date_releve": datetime(2026, 10, 17, 8, 30)},
        blocking=True,
    )
    assert instance.async_submit_index.await_args.kwargs["date_releve"] == date(2026, 10, 17)


async def test_capteurs_donnees_disparues(hass: HomeAssistant) -> None:
    """Un contrat disparu du coordinateur rend les capteurs sans valeur."""
    entry, _ = await _configurer(hass)
    coordinator = entry.runtime_data
    coordinator.data = {}
    component = hass.data["entity_components"]["sensor"]
    platform = component._platforms[entry.entry_id]
    entities = list(platform.entities.values())
    assert entities
    for entity in entities:
        # Le contrat n'est plus dans les données : aucune valeur, attributs réduits.
        if entity.entity_description.key in ("index", "conso_moyenne", "dernier_prelevement"):
            assert entity.native_value is None
        attributes = entity.extra_state_attributes
        assert attributes == {"contrat": IDCONTRAT}


async def test_capteur_cle_inconnue(hass: HomeAssistant) -> None:
    """Une clé de capteur inconnue renvoie None sans lever."""
    from custom_components.besancon_emoi.coordinator import BesanconEmoiCoordinator
    from custom_components.besancon_emoi.sensor import BesanconEmoiSensorEntity

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(return_value=[])
        coordinator = BesanconEmoiCoordinator(hass, entry, instance)
        coordinator.data = {}
        from tests.test_init import _contract_data

        contract_data = _contract_data()
        coordinator.data = {IDCONTRAT: contract_data}
        entity = BesanconEmoiSensorEntity(coordinator, contract_data, "index")
        entity._sensor_key = "inconnu"
        assert entity.native_value is None


async def test_service_plusieurs_contrats(hass: HomeAssistant) -> None:
    """L'action refuse d'envoyer le même index à plusieurs contrats."""
    from custom_components.besancon_emoi.api import ContractData
    from tests.test_init import IDCONTRAT, _contract_data

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_login = AsyncMock()
        instance.async_submit_index = AsyncMock()
        contrat_a = _contract_data()
        contrat_b = ContractData(
            contract=Contract(idcontrat="222222-03", abo="88888", last_index=100),
            index_history=[],
            bills=[],
        )
        instance.async_get_contracts = AsyncMock(return_value=[contrat_a.contract, contrat_b.contract])
        instance.async_get_index_history = AsyncMock(return_value=[])
        instance.async_get_bills = AsyncMock(return_value=[])
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    # Sans préciser le contrat : refus explicite, rien n'est envoyé.
    try:
        await hass.services.async_call(DOMAIN, "saisir_index", {"index": 330}, blocking=True)
    except HomeAssistantError as err:
        assert "Plusieurs contrats" in str(err)
    else:
        msg = "Une HomeAssistantError était attendue (contrats multiples)"
        raise AssertionError(msg)
    instance.async_submit_index.assert_not_awaited()
    # Avec le champ contrat : seule la cible est servie.
    await hass.services.async_call(
        DOMAIN,
        "saisir_index",
        {"index": 330, "contrat": IDCONTRAT},
        blocking=True,
    )
    args = instance.async_submit_index.await_args
    assert args.args[0] == IDCONTRAT


async def test_service_contrat_inconnu(hass: HomeAssistant) -> None:
    """Un contrat ciblé inexistant lève une erreur explicite."""
    _entry, instance = await _configurer(hass)
    try:
        await hass.services.async_call(
            DOMAIN,
            "saisir_index",
            {"index": 330, "contrat": "000000-00"},
            blocking=True,
        )
    except HomeAssistantError as err:
        assert "000000-00" in str(err)
    else:
        msg = "Une HomeAssistantError était attendue (contrat inconnu)"
        raise AssertionError(msg)
    instance.async_submit_index.assert_not_awaited()


async def test_premier_rafraichissement_en_echec(hass: HomeAssistant) -> None:
    """Un échec au premier rafraîchissement ferme la session."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_login = AsyncMock()
        instance.async_get_contracts = AsyncMock(side_effect=BesanconEmoiError("indisponible"))
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    instance.close.assert_awaited_once()


async def test_close_session_deja_fermee() -> None:
    """Fermer deux fois une session ne lève pas."""
    app = web.Application()
    client = TestClient(TestServer(app))
    await client.start_server()
    api = BesanconEmoiClient(client.session, base_url=str(client.make_url("")))
    try:
        await api.close()
        await api.close()
    finally:
        await client.close()


async def test_saisie_session_expiree_pendant_post() -> None:
    """Une session expirée pendant la saisie est signalée."""
    app = web.Application()

    async def submit(request: web.Request) -> web.Response:
        return web.HTTPFound("/")

    app.router.add_post("/prestations/eau/saisie/idcontrat/{rest:.*}", submit)
    client = TestClient(TestServer(app))
    await client.start_server()
    api = BesanconEmoiClient(client.session, base_url=str(client.make_url("")))
    try:
        try:
            await api.async_submit_index(
                "111111-02",
                "99999",
                330,
                date_releve=date(2026, 10, 18),
                last_index=322,
            )
        except BesanconEmoiAuthError as err:
            assert "non enregistré" in str(err)
        else:
            msg = "Une BesanconEmoiAuthError était attendue (session expirée)"
            raise AssertionError(msg)
    finally:
        await client.close()


async def test_saisie_redirection_inattendue() -> None:
    """Une redirection ailleurs que la page formulaires est refusée."""
    app = web.Application()

    async def submit(request: web.Request) -> web.Response:
        return web.HTTPFound("/prestations/eau/autre")

    app.router.add_post("/prestations/eau/saisie/idcontrat/{rest:.*}", submit)
    client = TestClient(TestServer(app))
    await client.start_server()
    api = BesanconEmoiClient(client.session, base_url=str(client.make_url("")))
    try:
        try:
            await api.async_submit_index(
                "111111-02",
                "99999",
                330,
                date_releve=date(2026, 10, 18),
                last_index=322,
            )
        except BesanconEmoiError as err:
            assert "Redirection inattendue" in str(err)
        else:
            msg = "Une BesanconEmoiError était attendue (redirection)"
            raise AssertionError(msg)
    finally:
        await client.close()


async def test_reconnexion_toujours_refusee(hass: HomeAssistant) -> None:
    """Une session encore refusée après reconnexion lève UpdateFailed."""
    from custom_components.besancon_emoi.coordinator import BesanconEmoiCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(
            side_effect=[BesanconEmoiAuthError("expirée"), BesanconEmoiAuthError("encore")]
        )
        coordinator = BesanconEmoiCoordinator(hass, entry, instance)
        with pytest.raises(UpdateFailed, match="toujours refusée"):
            await coordinator._async_update_data()


async def test_auth_expiree_pendant_lHistorique(hass: HomeAssistant) -> None:
    """Une session expirée pendant l'historique déclenche la reconnexion."""
    from custom_components.besancon_emoi.coordinator import BesanconEmoiCoordinator
    from tests.test_init import _contract_data

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(return_value=[_contract_data().contract])
        # L'historique expire, la reconnexion réussit, le second essai passe.
        instance.async_get_index_history = AsyncMock(
            side_effect=[BesanconEmoiAuthError("expirée"), _contract_data().index_history]
        )
        instance.async_get_bills = AsyncMock(return_value=_contract_data().bills)
        coordinator = BesanconEmoiCoordinator(hass, entry, instance)
        data = await coordinator._async_update_data()
        assert data
        instance.async_login.assert_awaited_once()


async def test_saisie_hebdo_plusieurs_contrats(hass: HomeAssistant) -> None:
    """La saisie hebdomadaire est ignorée avec plusieurs contrats."""
    from custom_components.besancon_emoi.api import ContractData
    from custom_components.besancon_emoi.coordinator import BesanconEmoiCoordinator
    from tests.test_init import _contract_data

    aujourdhui = WEEKDAYS[datetime.now().weekday()]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options={
            CONF_AUTO_SUBMIT: True,
            CONF_SUBMIT_WEEKDAY: aujourdhui,
            "index_entity": "sensor.compteur_wmbus_index",
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        contrat_a = _contract_data()
        contrat_b = ContractData(
            contract=Contract(idcontrat="222222-03", abo="88888", last_index=100),
            index_history=[],
            bills=[],
        )
        instance.async_get_contracts = AsyncMock(return_value=[contrat_a.contract, contrat_b.contract])
        instance.async_get_index_history = AsyncMock(return_value=[])
        instance.async_get_bills = AsyncMock(return_value=[])
        instance.async_submit_index = AsyncMock()
        hass.states.async_set("sensor.compteur_wmbus_index", "325")
        coordinator = BesanconEmoiCoordinator(hass, entry, instance)
        data = await coordinator._async_update_data()
        assert len(data) == 2
        instance.async_submit_index.assert_not_awaited()


async def test_saisie_hebdo_index_negatif(hass: HomeAssistant) -> None:
    """Un index négatif sur l'entité source bloque la mise à jour."""
    from custom_components.besancon_emoi.coordinator import BesanconEmoiCoordinator
    from tests.test_init import _contract_data

    aujourdhui = WEEKDAYS[datetime.now().weekday()]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options={
            CONF_AUTO_SUBMIT: True,
            CONF_SUBMIT_WEEKDAY: aujourdhui,
            "index_entity": "sensor.compteur_wmbus_index",
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(return_value=[_contract_data().contract])
        instance.async_get_index_history = AsyncMock(return_value=[])
        instance.async_get_bills = AsyncMock(return_value=[])
        instance.async_submit_index = AsyncMock()
        hass.states.async_set("sensor.compteur_wmbus_index", "-5")
        coordinator = BesanconEmoiCoordinator(hass, entry, instance)
        with pytest.raises(UpdateFailed, match="négatif"):
            await coordinator._async_update_data()
        instance.async_submit_index.assert_not_awaited()


async def test_telechargement_pdf() -> None:
    """Le PDF de facture se télécharge avec la session authentifiée."""
    app = _build_app()
    pdf = b"%PDF-1.4 facture"

    async def facture_pdf(request: web.Request) -> web.Response:
        return web.Response(body=pdf, content_type="application/pdf")

    app.router.add_get("/prestations/eau/facture/id/{rest:.*}", facture_pdf)
    client = TestClient(TestServer(app))
    await client.start_server()
    api = BesanconEmoiClient(client.session, base_url=str(client.make_url("")))
    try:
        contenu = await api.async_download_bill_pdf(
            "/prestations/eau/facture/id/26_70001_111111_02_99999.pdf/abo/99999"
        )
        assert contenu == pdf
    finally:
        await client.close()


async def test_telechargement_pdf_refuse() -> None:
    """Un PDF inaccessible lève une erreur explicite."""
    app = web.Application()

    async def facture_pdf(request: web.Request) -> web.Response:
        return web.Response(text="Accès refusé", status=403)

    app.router.add_get("/prestations/eau/facture/id/{rest:.*}", facture_pdf)
    client = TestClient(TestServer(app))
    await client.start_server()
    api = BesanconEmoiClient(client.session, base_url=str(client.make_url("")))
    try:
        try:
            await api.async_download_bill_pdf(
                "/prestations/eau/facture/id/26_70001_111111_02_99999.pdf/abo/99999"
            )
        except BesanconEmoiAuthError:
            pass
        else:
            msg = "Une BesanconEmoiAuthError était attendue (PDF refusé)"
            raise AssertionError(msg)
    finally:
        await client.close()


async def test_telechargement_pdf_chemin_invalide() -> None:
    """Un chemin de facture non relatif est refusé."""
    app = _build_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    api = BesanconEmoiClient(client.session, base_url=str(client.make_url("")))
    try:
        try:
            await api.async_download_bill_pdf("http://exemple.fr/facture.pdf")
        except BesanconEmoiError as err:
            assert "invalide" in str(err)
        else:
            msg = "Une BesanconEmoiError était attendue (chemin)"
            raise AssertionError(msg)
    finally:
        await client.close()


async def test_saisie_hebdo_source_en_litres(hass: HomeAssistant) -> None:
    """Une source en litres bloque la mise à jour sans transmission."""
    from custom_components.besancon_emoi.coordinator import BesanconEmoiCoordinator
    from tests.test_init import _contract_data

    aujourdhui = WEEKDAYS[datetime.now().weekday()]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
        options={
            CONF_AUTO_SUBMIT: True,
            CONF_SUBMIT_WEEKDAY: aujourdhui,
            "index_entity": "sensor.compteur_wmbus_index",
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.besancon_emoi.BesanconEmoiClient", autospec=True) as mock_client:
        instance = mock_client.return_value
        instance.async_get_contracts = AsyncMock(return_value=[_contract_data().contract])
        instance.async_get_index_history = AsyncMock(return_value=[])
        instance.async_get_bills = AsyncMock(return_value=[])
        instance.async_submit_index = AsyncMock()
        hass.states.async_set("sensor.compteur_wmbus_index", "325", {"unit_of_measurement": "L"})
        coordinator = BesanconEmoiCoordinator(hass, entry, instance)
        with pytest.raises(UpdateFailed, match="litres"):
            await coordinator._async_update_data()
        instance.async_submit_index.assert_not_awaited()


async def test_telechargement_pdf_contenu_inattendu() -> None:
    """Une réponse 200 sans PDF est refusée."""
    app = web.Application()

    async def facture_html(request: web.Request) -> web.Response:
        return web.Response(text="<html><body>Page</body></html>", content_type="text/html")

    app.router.add_get("/prestations/eau/facture/id/{rest:.*}", facture_html)
    client = TestClient(TestServer(app))
    await client.start_server()
    api = BesanconEmoiClient(client.session, base_url=str(client.make_url("")))
    try:
        try:
            await api.async_download_bill_pdf(
                "/prestations/eau/facture/id/26_70001_111111_02_99999.pdf/abo/99999"
            )
        except BesanconEmoiError as err:
            assert "Téléchargement" in str(err)
        else:
            msg = "Une BesanconEmoiError était attendue (contenu non PDF)"
            raise AssertionError(msg)
    finally:
        await client.close()
