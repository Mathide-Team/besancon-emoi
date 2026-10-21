"""Tests du client du portail Besançon et moi (fixtures synthétiques)."""

from __future__ import annotations

import contextlib
import logging
from datetime import date
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from custom_components.besancon_emoi.api import (
    BesanconEmoiAuthError,
    BesanconEmoiClient,
    BesanconEmoiError,
    WasteContract,
)
from custom_components.besancon_emoi.const import LOGGER

FIXTURES = Path(__file__).parent / "fixtures"
EMAIL = "exemple@fr.fr"
PASSWORD = "secret"


@pytest.fixture(autouse=True)
def _socket_autorise(socket_enabled):
    """Autorise le serveur HTTP local des tests."""
    yield


def _build_app() -> web.Application:
    """Serveur factice reprenant les routes du portail.

    Comme le portail réel, l'authentification repose sur le cookie
    ``grc_session`` : les pages protégées l'exigent, ce qui vérifie que
    le client conserve bien la session entre les requêtes.
    """
    app = web.Application()
    app["sessions"] = set()

    async def login(request: web.Request) -> web.Response:
        form = await request.post()
        if form.get("email") == EMAIL and form.get("pwd") == PASSWORD:
            token = f"session-{len(app['sessions'])}"
            app["sessions"].add(token)
            resp = web.HTTPFound("/")
            resp.set_cookie("grc_session", token)
            return resp
        return web.Response(text=(FIXTURES / "login.html").read_text(), content_type="text/html")

    def _authentifie(request: web.Request) -> bool:
        return request.cookies.get("grc_session") in app["sessions"]

    async def home(request: web.Request) -> web.Response:
        if not _authentifie(request):
            return web.Response(
                text=(FIXTURES / "login.html").read_text(),
                content_type="text/html",
            )
        return web.Response(
            text=(FIXTURES / "accueil.html").read_text(),
            content_type="text/html",
        )

    def _guarded(name: str, request: web.Request) -> web.Response:
        if not _authentifie(request):
            return web.Response(
                text=(FIXTURES / "login.html").read_text(),
                content_type="text/html",
            )
        return web.Response(text=(FIXTURES / name).read_text(), content_type="text/html")

    async def contrats(request: web.Request) -> web.Response:
        return _guarded("contrats.html", request)

    async def saisie_form(request: web.Request) -> web.Response:
        return _guarded("saisie.html", request)

    async def index_abo(request: web.Request) -> web.Response:
        return _guarded("index_abo.html", request)

    async def facturesajax(request: web.Request) -> web.Response:
        return _guarded("facturesajax.html", request)

    async def dechets_index(request: web.Request) -> web.Response:
        return _guarded("dechets_index.html", request)

    async def pesees(request: web.Request) -> web.Response:
        return _guarded("pesees.html", request)

    async def pesees_page(request: web.Request) -> web.Response:
        return _guarded("pesees_page2.html", request)

    async def peseesjaune(request: web.Request) -> web.Response:
        return _guarded("peseesjaune.html", request)

    async def submit(request: web.Request) -> web.Response:
        if not _authentifie(request):
            # Session expirée : redirection vers l'accueil, saisie non
            # enregistrée.
            return web.HTTPFound("/")
        app["submitted"] = dict(await request.post())
        idcontrat = request.match_info["rest"].split("/")[0]
        return web.HTTPFound(f"/prestations/eau/formulaires/idcontrat/{idcontrat}")

    async def formulaires(request: web.Request) -> web.Response:
        return web.Response(
            text=(FIXTURES / "formulaires.html").read_text(),
            content_type="text/html",
        )

    async def logout(request: web.Request) -> web.Response:
        app["sessions"].discard(request.cookies.get("grc_session"))
        return web.HTTPFound("/")

    app.router.add_post("/", login)
    app.router.add_get("/", home)
    app.router.add_get("/prestations/eau/contrats", contrats)
    app.router.add_get("/prestations/eau/saisie/idcontrat/{rest:.*}", saisie_form)
    app.router.add_get("/prestations/eau/index/abo/{abo}", index_abo)
    app.router.add_get("/prestations/eau/facturesajax/abo/{abo}", facturesajax)
    app.router.add_get("/prestations/dechets/index/abo/{abo}", dechets_index)
    app.router.add_get("/prestations/dechets/pesees/id/{idcontrat}", pesees)
    app.router.add_get("/prestations/dechets/pesees/id/{idcontrat}/page/{page}", pesees_page)
    app.router.add_get("/prestations/dechets/peseesjaune/id/{idcontrat}", peseesjaune)
    app.router.add_post("/prestations/eau/saisie/idcontrat/{rest:.*}", submit)
    app.router.add_get("/prestations/eau/formulaires/idcontrat/{rest:.*}", formulaires)
    app.router.add_get("/index/logout", logout)
    return app


async def _make_client(logged_in: bool = False) -> tuple[TestClient, BesanconEmoiClient]:
    """Crée un client ; ``logged_in`` pré-authentifie le serveur.

    Le jeton de session est injecté dans le cookie jar du client : les
    requêtes suivantes ne passent que si la session est réellement
    conservée d'une requête à l'autre.
    """
    app = _build_app()
    client = TestClient(TestServer(app))
    await client.start_server()
    if logged_in:
        app["sessions"].add("session-pre")
        client.session.cookie_jar.update_cookies({"grc_session": "session-pre"})
    api = BesanconEmoiClient(client.session, base_url=str(client.make_url("")))
    return client, api


async def test_login_succes() -> None:
    """La connexion ouvre la session et permet de lire les contrats."""
    client, api = await _make_client()
    try:
        await api.async_login(EMAIL, PASSWORD)
        contracts = await api.async_get_contracts()
        assert len(contracts) == 1
        assert contracts[0].idcontrat == "111111-02"
        assert contracts[0].abo == "99999"
        assert contracts[0].last_index == 322
    finally:
        await client.close()


async def test_login_refuse() -> None:
    """Des identifiants invalides lèvent une erreur d'authentification."""
    client, api = await _make_client()
    try:
        try:
            await api.async_login(EMAIL, "faux")
        except BesanconEmoiAuthError:
            pass
        else:
            msg = "Une BesanconEmoiAuthError était attendue"
            raise AssertionError(msg)
    finally:
        await client.close()


async def test_session_expiree() -> None:
    """Une session expirée est détectée sur les pages protégées."""
    client, api = await _make_client(logged_in=True)
    try:
        await api.async_login(EMAIL, PASSWORD)
        # Simule une expiration : la page suivante renvoie le formulaire.
        await client.get("/index/logout")
        try:
            await api.async_get_contracts()
        except BesanconEmoiAuthError:
            pass
        else:
            msg = "Une BesanconEmoiAuthError était attendue après expiration"
            raise AssertionError(msg)
    finally:
        await client.close()


async def test_auth_bavarde_tant_que_connexion_echoue(caplog) -> None:
    """Avant toute réussite, chaque étape d'authentification est visible au niveau INFO."""
    client, api = await _make_client()
    try:
        caplog.clear()
        with (
            caplog.at_level(logging.INFO, logger=LOGGER.name),
            contextlib.suppress(BesanconEmoiAuthError),
        ):
            await api.async_login(EMAIL, "faux")
        messages = [r.getMessage() for r in caplog.records]
        assert any("Connexion au portail" in m for m in messages)
        assert any("mode bavard" in m for m in messages)
        assert api._auth_bavarde is True
    finally:
        await client.close()


async def test_auth_silencieuse_apres_premiere_reussite(caplog) -> None:
    """Après une connexion réussie, les étapes passent au niveau DEBUG."""
    client, api = await _make_client()
    try:
        await api.async_login(EMAIL, PASSWORD)
        assert api._auth_bavarde is False
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=LOGGER.name):
            await api.async_login(EMAIL, PASSWORD)
            infos = [r for r in caplog.records if r.levelno == logging.INFO]
            assert not any("Connexion au portail" in r.getMessage() for r in infos)
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger=LOGGER.name):
            await api.async_login(EMAIL, PASSWORD)
            assert any("Connexion au portail" in r.getMessage() for r in caplog.records)
    finally:
        await client.close()


async def test_auth_redevient_bavarde_apres_echec(caplog) -> None:
    """Un échec après une réussite réactive la journalisation bavarde."""
    client, api = await _make_client()
    try:
        await api.async_login(EMAIL, PASSWORD)
        assert api._auth_bavarde is False
        with contextlib.suppress(BesanconEmoiAuthError):
            await api.async_login(EMAIL, "faux")
        assert api._auth_bavarde is True
        caplog.clear()
        with (
            caplog.at_level(logging.INFO, logger=LOGGER.name),
            contextlib.suppress(BesanconEmoiAuthError),
        ):
            await api.async_login(EMAIL, "faux")
        assert any("Connexion au portail" in r.getMessage() for r in caplog.records)
    finally:
        await client.close()


async def test_auth_reseau_bavard_apres_echec() -> None:
    """Un échec réseau (portail injoignable) maintient l'authentification bavarde."""
    client, api = await _make_client()
    try:
        await api.async_login(EMAIL, PASSWORD)
        assert api._auth_bavarde is False
        await client.server.close()  # le portail ne répond plus
        with contextlib.suppress(BesanconEmoiError):
            await api.async_login(EMAIL, PASSWORD)
        assert api._auth_bavarde is True
    finally:
        await client.close()


async def test_session_partagee_home_assistant(hass) -> None:
    """La connexion fonctionne avec la session partagée de Home Assistant (cookies conservés).

    Vérifie le mécanisme de l'issue #3 avec une session créée par Home
    Assistant : le cookie du portail est conservé entre la connexion et
    les requêtes suivantes. Le bocal est « unsafe » car le faux serveur
    écoute sur 127.0.0.1 : le bocal aiohttp par défaut refuse les
    cookies des hôtes en adresse IP — en production, le portail est un
    nom de domaine et la session partagée convient.
    """
    from aiohttp.cookiejar import CookieJar
    from homeassistant.helpers.aiohttp_client import async_create_clientsession

    client = TestClient(TestServer(_build_app()))
    await client.start_server()
    try:
        session = async_create_clientsession(hass, cookie_jar=CookieJar(unsafe=True))
        api = BesanconEmoiClient(session, base_url=str(client.make_url("")))
        await api.async_login(EMAIL, PASSWORD)
        assert api._auth_bavarde is False
        # La session authentifiée sert directement les pages suivantes :
        # le cookie du portail est conservé dans la session partagée.
        contrats = await api.async_get_contracts()
        assert contrats
        await api.close()
    finally:
        await client.close()


async def test_historique_index() -> None:
    """L'historique des relevés est analysé et trié du plus récent."""
    client, api = await _make_client(logged_in=True)
    try:
        history = await api.async_get_index_history("99999")
        assert [r.index for r in history] == [322, 281, 234]
        assert history[0].date == date(2026, 8, 18)
        assert history[0].releve == "Relevé par télé-relève"
        assert history[0].conso_jour == 0.241
    finally:
        await client.close()


async def test_factures() -> None:
    """Les factures sont analysées : montant, date, lien PDF."""
    client, api = await _make_client(logged_in=True)
    try:
        bills = await api.async_get_bills("99999")
        assert len(bills) == 3
        # La table « Info » est ignorée ; la ligne sans date ni montant est gardée.
        assert bills[2].date is None
        assert bills[2].montant_preleve is None
        # Le n° de facture vient du nom du PDF ; le montant est le prélèvement.
        assert bills[0].date == date(2026, 9, 15)
        assert bills[0].montant_preleve == -0.99
        assert bills[0].facture == "70001"
        assert bills[0].pdf_path is not None
        assert ".pdf" in bills[0].pdf_path
        assert bills[1].montant_preleve == 35.50
    finally:
        await client.close()


async def test_saisie_index() -> None:
    """La saisie transmet les champs attendus par le portail."""
    client, api = await _make_client(logged_in=True)
    try:
        await api.async_submit_index(
            "111111-02",
            "99999",
            330,
            date_releve=date(2026, 10, 18),
            last_index=322,
        )
        submitted = client.app["submitted"]
        assert submitted["idcontrat"] == "111111-02"
        assert submitted["lastIndex"] == "322"
        assert submitted["dateReleve"] == "2026-10-18"
        assert submitted["index"] == "330"
    finally:
        await client.close()


async def test_saisie_index_inferieur_refusee() -> None:
    """Un index inférieur au dernier relevé connu est refusé."""
    client, api = await _make_client(logged_in=True)
    try:
        try:
            await api.async_submit_index(
                "111111-02",
                "99999",
                100,
                date_releve=date(2026, 10, 18),
                last_index=322,
            )
        except BesanconEmoiError:
            pass
        else:
            msg = "Une BesanconEmoiError était attendue pour un index plus petit"
            raise AssertionError(msg)
        assert "submitted" not in client.app
    finally:
        await client.close()


async def test_deux_comptes_sessions_independantes() -> None:
    """Deux comptes gardent des sessions séparées sur le même portail."""
    app = _build_app()
    server = TestServer(app)
    await server.start_server()
    base_url = str(server.make_url(""))
    client_a = TestClient(server)
    client_b = TestClient(server)
    await client_a.start_server()
    await client_b.start_server()
    api_a = BesanconEmoiClient(client_a.session, base_url=base_url)
    api_b = BesanconEmoiClient(client_b.session, base_url=base_url)
    try:
        await api_a.async_login(EMAIL, PASSWORD)
        await api_b.async_login(EMAIL, PASSWORD)
        # Chaque client lit ses contrats avec sa propre session.
        assert len(await api_a.async_get_contracts()) == 1
        assert len(await api_b.async_get_contracts()) == 1
        # La déconnexion de l'un n'affecte pas l'autre.
        await client_a.get("/index/logout")
        try:
            await api_a.async_get_contracts()
        except BesanconEmoiAuthError:
            pass
        else:
            msg = "Une BesanconEmoiAuthError était attendue pour le compte A"
            raise AssertionError(msg)
        assert len(await api_b.async_get_contracts()) == 1
    finally:
        await client_a.close()
        await client_b.close()


async def test_dechets_contrats() -> None:
    """Les contrats déchets sont découverts via l'accueil puis l'index déchets."""
    client, api = await _make_client(logged_in=True)
    try:
        contracts = await api.async_get_waste_contracts()
        assert contracts == [WasteContract(idcontrat="333333-04", abo="88888")]
    finally:
        await client.close()


async def test_pesees_toutes_pages() -> None:
    """Les pesées des deux pages sont lues, total et note exclus."""
    client, api = await _make_client(logged_in=True)
    try:
        weighings = await api.async_get_weighings("333333-04")
        # 3 lignes en page 1 + 2 lignes en page 2 (le total et la note
        # de la page 1 ne sont pas des pesées).
        assert len(weighings) == 5
        first = weighings[0]
        assert first.date == date(2026, 10, 5)
        assert first.heure == "05h42"
        assert first.poids_kg == 25.5
        assert first.type_bac == "Résiduel"
        assert first.volume_l == 140
        assert first.vidages == 1
        assert first.no_bac == "700111"
        assert first.no_puce == "1A2B3C4D5E"
        assert weighings[-1].date == date(2026, 8, 11)
    finally:
        await client.close()


async def test_pesees_jaune() -> None:
    """La liste jaune (recyclable) est lue séparément."""
    client, api = await _make_client(logged_in=True)
    try:
        weighings = await api.async_get_weighings("333333-04", jaune=True)
        assert len(weighings) == 2
        assert weighings[0].type_bac == "Recyclable"
        assert weighings[0].poids_kg == 11.5
    finally:
        await client.close()


async def test_pesees_session_expiree() -> None:
    """Sans session, la lecture des pesées échoue en erreur d'authentification."""
    client, api = await _make_client()
    try:
        with pytest.raises(BesanconEmoiAuthError):
            await api.async_get_weighings("333333-04")
    finally:
        await client.close()
