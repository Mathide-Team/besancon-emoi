"""Client du portail « Besançon et moi » (besancon-emoi.fr).

Le portail n'expose pas d'API publique : ce client s'appuie sur les pages
utilisées par le site (connexion, contrats, factures, historique d'index,
saisie du relevé de compteur).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession
from bs4 import BeautifulSoup

from .const import BASE_URL
from .journal import resumer

_LOGGER = logging.getLogger(__name__)

_LOGIN_FORM_MARKER = 'name="pwd"'

_ABO_RE = re.compile(r"/prestations/eau/[a-z]+/abo/(\d+)")
_IDCONTRAT_RE = re.compile(r"/prestations/eau/saisie/idcontrat/([\d-]+)")
_DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}")


class BesanconEmoiError(Exception):
    """Erreur générique du client."""


class BesanconEmoiAuthError(BesanconEmoiError):
    """Identifiants refusés ou session expirée."""


@dataclass
class IndexReading:
    """Un relevé d'index de compteur."""

    date: date
    index: int
    releve: str
    conso_jour: float | None


@dataclass
class Bill:
    """Un prélèvement lié à une facture d'eau.

    La colonne « Montant » du portail est le montant prélevé
    (mensualité ou ajustement annuel), pas le total de la facture.
    Le total TTC, lu dans le PDF de la facture, est renseigné par le
    coordinateur (une facture n'est téléchargée qu'une fois par session).
    """

    facture: str
    date: date | None
    contrat: str
    montant_preleve: float | None
    pdf_path: str | None
    total_ttc: float | None = None


@dataclass
class Contract:
    """Un contrat d'eau raccroché à son abonnement portail."""

    idcontrat: str
    abo: str
    last_index: int | None = None


@dataclass
class ContractData:
    """Données complètes d'un contrat (historique + factures)."""

    contract: Contract
    index_history: list[IndexReading] = field(default_factory=list)
    bills: list[Bill] = field(default_factory=list)


@dataclass
class Weighing:
    """Une pesée de bac (déchets)."""

    date: date | None
    heure: str
    poids_kg: float | None
    type_bac: str
    volume_l: int | None = None
    vidages: int | None = None
    no_bac: str = ""
    no_puce: str = ""


@dataclass
class WasteContract:
    """Un contrat déchets raccroché à son abonnement portail."""

    idcontrat: str
    abo: str


@dataclass
class WasteContractData:
    """Données d'un contrat déchets (pesées facturées + recyclables)."""

    contract: WasteContract
    pesees: list[Weighing] = field(default_factory=list)
    pesees_jaune: list[Weighing] = field(default_factory=list)


class BesanconEmoiClient:
    """Client asynchrone du portail Besançon et moi."""

    def __init__(self, session: ClientSession, base_url: str = BASE_URL) -> None:
        """Initialise le client avec une session aiohttp."""
        _LOGGER.debug("BesanconEmoiClient.__init__: entrée | session=***, base_url=%s", resumer(base_url))
        self._session = session
        self._base_url = base_url.rstrip("/")
        # Journalisation de l'authentification (convention équipe
        # Mathilde-Team) : bavarde (INFO) tant qu'aucune connexion n'a
        # réussi pour ce compte — et après chaque nouvel échec —,
        # silencieuse (DEBUG) ensuite. L'état est propre à ce client :
        # la réussite d'un compte ne masque pas l'échec d'un autre.
        self._auth_bavarde = True
        _LOGGER.debug("BesanconEmoiClient.__init__: sortie")

    def _auth_log(self, message: str, *args: Any) -> None:
        """Trace une étape d'authentification : INFO tant que la connexion
        n'a jamais réussi, DEBUG ensuite."""
        _LOGGER.debug(
            "BesanconEmoiClient._auth_log: entrée | message=%s, *args=%s", resumer(message), resumer(args)
        )
        if self._auth_bavarde:
            _LOGGER.info(message, *args)
        else:
            _LOGGER.debug(message, *args)

    async def _request(self, method: str, path: str, **kwargs: Any) -> ClientResponse:
        """Effectue une requête et détecte une session expirée."""
        _LOGGER.debug(
            "BesanconEmoiClient._request: entrée | method=%s, path=%s, **kwargs=%s",
            resumer(method),
            resumer(path),
            resumer(kwargs),
        )
        url = f"{self._base_url}{path}"
        try:
            resp = await self._session.request(method, url, **kwargs)
        except (TimeoutError, ClientError) as err:
            msg = f"Requête {method} {path} impossible : {err}"
            _LOGGER.debug("BesanconEmoiClient._request: sortie par BesanconEmoiError | cause=%s", err)
            raise BesanconEmoiError(msg) from err
        if resp.status in (500, 502, 503, 504):
            msg = f"Portail indisponible ({resp.status}) sur {path}"
            _LOGGER.debug("BesanconEmoiClient._request: sortie par BesanconEmoiError")
            raise BesanconEmoiError(msg)
        if resp.status in (401, 403):
            _LOGGER.debug("BesanconEmoiClient._request: sortie par BesanconEmoiAuthError")
            raise BesanconEmoiAuthError(f"Accès refusé sur {path}")
        _LOGGER.debug("BesanconEmoiClient._request: sortie | %s", resumer(resp))
        return resp

    async def _get_text(self, path: str) -> str:
        """GET et retourne le corps HTML, en vérifiant la session."""
        _LOGGER.debug("BesanconEmoiClient._get_text: entrée | path=%s", resumer(path))
        resp = await self._request("GET", path)
        text = await resp.text()
        if _LOGIN_FORM_MARKER in text:
            msg = f"Session expirée (formulaire de connexion sur {path})"
            _LOGGER.debug("BesanconEmoiClient._get_text: sortie par BesanconEmoiAuthError")
            raise BesanconEmoiAuthError(msg)
        _LOGGER.debug("BesanconEmoiClient._get_text: sortie | %s", resumer(text))
        return text

    async def async_download_bill_pdf(self, pdf_path: str) -> bytes:
        """Télécharge le PDF d'une facture (chemin vu dans la liste).

        Le chemin relatif est exposé par les capteurs ; le téléchargement
        requiert la session authentifiée.
        """
        _LOGGER.debug("BesanconEmoiClient.async_download_bill_pdf: entrée | pdf_path=%s", resumer(pdf_path))
        if not pdf_path.startswith("/"):
            msg = "Chemin de facture invalide"
            _LOGGER.debug("BesanconEmoiClient.async_download_bill_pdf: sortie par BesanconEmoiError")
            raise BesanconEmoiError(msg)
        resp = await self._request("GET", pdf_path)
        if resp.status != 200 or "pdf" not in resp.content_type:
            msg = f"Téléchargement de la facture impossible ({resp.status})"
            _LOGGER.debug("BesanconEmoiClient.async_download_bill_pdf: sortie par BesanconEmoiError")
            raise BesanconEmoiError(msg)
        _LOGGER.debug("BesanconEmoiClient.async_download_bill_pdf: sortie")
        return await resp.read()

    async def close(self) -> None:
        """Ferme la session HTTP dédiée à ce compte."""
        _LOGGER.debug("BesanconEmoiClient.close: entrée")
        if not self._session.closed:
            await self._session.close()
        _LOGGER.debug("BesanconEmoiClient.close: sortie")

    async def async_login(self, email: str, password: str) -> None:
        """Se connecte au portail.

        Le portail répond 302 vers la page d'accueil en cas de succès et
        ré-affiche le formulaire de connexion sinon.

        Les étapes sont tracées tant qu'aucune connexion n'a réussi
        (convention équipe) : un échec de connexion laisse toujours
        une trace exploitable dans le journal.
        """
        _LOGGER.debug("BesanconEmoiClient.async_login: entrée | email=***, password=***")
        self._auth_log("Connexion au portail %s", self._base_url)
        try:
            resp = await self._request(
                "POST",
                "/",
                data={"email": email, "pwd": password},
                allow_redirects=False,
            )
            await resp.text()
            if resp.status != 302:
                _LOGGER.debug("BesanconEmoiClient.async_login: sortie par BesanconEmoiAuthError")
                raise BesanconEmoiAuthError("Identifiants refusés par le portail")
            self._auth_log("Connexion acceptée par le portail")
            # Vérifie que la session est bien ouverte.
            await self._get_text("/prestations/eau/contrats")
        except BesanconEmoiError:
            # Tout échec — identifiants, session, réseau, portail —
            # maintient l'authentification bavarde pour les prochains essais.
            self._auth_bavarde = True
            _LOGGER.warning("Échec de connexion : authentification maintenue en mode bavard")
            raise
        # Premier succès : l'authentification redevient silencieuse.
        self._auth_bavarde = False
        _LOGGER.info("Connexion réussie : authentification repasse en mode silencieux (debug)")

    async def async_get_contracts(self) -> list[Contract]:
        """Liste les contrats d'eau (idcontrat, abo, dernier index).

        L'appariement idcontrat ↔ abo se fait via le formulaire de saisie
        du relevé, dont l'action contient les deux identifiants.
        """
        _LOGGER.debug("BesanconEmoiClient.async_get_contracts: entrée")
        text = await self._get_text("/prestations/eau/contrats")
        idcontrats = list(dict.fromkeys(_IDCONTRAT_RE.findall(text)))
        contracts: list[Contract] = []
        for idcontrat in idcontrats:
            contracts.append(await self._resolve_contract(idcontrat))
        if not contracts:
            _LOGGER.warning("Aucun contrat d'eau trouvé sur le portail")
        _LOGGER.debug("BesanconEmoiClient.async_get_contracts: sortie | %s", resumer(contracts))
        return contracts

    async def _resolve_contract(self, idcontrat: str) -> Contract:
        """Résout l'abo et le dernier index depuis le formulaire de saisie."""
        _LOGGER.debug("BesanconEmoiClient._resolve_contract: entrée | idcontrat=%s", resumer(idcontrat))
        text = await self._get_text(f"/prestations/eau/saisie/idcontrat/{idcontrat}")
        soup = BeautifulSoup(text, "html.parser")
        form = soup.find("form", action=re.compile(r"/saisie/idcontrat/"))
        abo = ""
        last_index: int | None = None
        if form is not None:
            match = re.search(r"/abo/(\d+)", form.get("action", ""))
            if match:
                abo = match.group(1)
            input_last = form.find("input", attrs={"name": "lastIndex"})
            if input_last is not None and input_last.get("value", "").strip():
                last_index = int(float(input_last["value"].strip().replace(",", ".")))
        _LOGGER.debug("BesanconEmoiClient._resolve_contract: sortie")
        return Contract(idcontrat=idcontrat, abo=abo, last_index=last_index)

    async def async_get_index_history(self, abo: str) -> list[IndexReading]:
        """Historique des relevés d'un abonnement.

        La page contient plusieurs tableaux ; celui des relevés a pour
        entêtes « Date », « Index », « Relevé », « Consommation(m3/jour) ».
        """
        _LOGGER.debug("BesanconEmoiClient.async_get_index_history: entrée | abo=%s", resumer(abo))
        text = await self._get_text(f"/prestations/eau/index/abo/{abo}")
        soup = BeautifulSoup(text, "html.parser")
        readings: list[IndexReading] = []
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True) for th in table.find_all("th")]
            if not headers or headers[0] != "Date" or "Index" not in headers[:3]:
                continue
            for row in table.find_all("tr"):
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cells) < 3:
                    continue
                readings.append(_parse_index_row(cells))
        readings.sort(key=lambda r: r.date, reverse=True)
        _LOGGER.debug("BesanconEmoiClient.async_get_index_history: sortie | %s", resumer(readings))
        return readings

    async def async_get_bills(self, abo: str) -> list[Bill]:
        """Liste les factures d'un abonnement (tableau AJAX)."""
        _LOGGER.debug("BesanconEmoiClient.async_get_bills: entrée | abo=%s", resumer(abo))
        text = await self._get_text(f"/prestations/eau/facturesajax/abo/{abo}")
        soup = BeautifulSoup(text, "html.parser")
        bills: list[Bill] = []
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True) for th in table.find_all("th")]
            if "Facture" not in headers or "Montant" not in headers:
                continue
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) < 7:
                    continue
                bills.append(_parse_bill_row(cells))
        bills.sort(key=lambda b: b.date or date.min, reverse=True)
        _LOGGER.debug("BesanconEmoiClient.async_get_bills: sortie | %s", resumer(bills))
        return bills

    async def async_get_waste_contracts(self) -> list[WasteContract]:
        """Liste les contrats déchets du compte.

        La page d'accueil de l'espace personnel référence chaque
        prestation par son abonnement (``/prestations/dechets/index/abo/…``) ;
        les pages déchets référencent ensuite le contrat par son
        identifiant (``/prestations/dechets/…/id/NNNNNN-NN``).
        """
        _LOGGER.debug("BesanconEmoiClient.async_get_waste_contracts: entrée")
        home_text = await self._get_text("/")
        contracts: list[WasteContract] = []
        for abo in dict.fromkeys(_DECHETS_ABO_RE.findall(home_text)):
            text = await self._get_text(f"/prestations/dechets/index/abo/{abo}")
            for idcontrat in dict.fromkeys(_DECHETS_ID_RE.findall(text)):
                contracts.append(WasteContract(idcontrat=idcontrat, abo=abo))
        _LOGGER.debug("%d contrats déchets récupérés", len(contracts))
        return contracts

    async def async_get_weighings(self, idcontrat: str, jaune: bool = False) -> list[Weighing]:
        """Liste les pesées d'un contrat déchets (toutes les pages).

        La liste « résiduel » regroupe les pesées facturées ; la liste
        « jaune » les pesées des bacs recyclables. La table est paginée
        (50 lignes par page) : les pages suivantes sont lues jusqu'à
        la dernière référencée.
        """
        _LOGGER.debug(
            "BesanconEmoiClient.async_get_weighings: entrée | idcontrat=%s, jaune=%s",
            resumer(idcontrat),
            resumer(jaune),
        )
        base = f"/prestations/dechets/pesees{'jaune' if jaune else ''}/id/{idcontrat}"
        text = await self._get_text(base)
        weighings = _parse_pesees_table(text)
        pages = [int(p) for p in _PAGINATION_RE.findall(text) if int(p) > 1]
        last_page = max(pages) if pages else 1
        # Garde-fou : au plus 30 pages lues par liste.
        for page in range(2, min(last_page, 30) + 1):
            page_text = await self._get_text(f"{base}/page/{page}")
            rows = _parse_pesees_table(page_text)
            if not rows:
                break
            weighings.extend(rows)
        _LOGGER.debug(
            "%d pesées récupérées pour le contrat déchets %s (jaune=%s)",
            len(weighings),
            idcontrat,
            jaune,
        )
        return weighings

    async def async_submit_index(
        self,
        idcontrat: str,
        abo: str,
        index: int,
        date_releve: date | None = None,
        last_index: int | None = None,
    ) -> None:
        """Saisit le relevé de compteur pour un contrat.

        Le portail attend l'ancienne valeur (lastIndex), la date du relevé
        et la nouvelle valeur ; la date est obligatoire côté formulaire.
        """
        _LOGGER.debug(
            "BesanconEmoiClient.async_submit_index: "
            "entrée | idcontrat=%s, abo=%s, index=%s, date_releve=%s, last_index=%s",
            resumer(idcontrat),
            resumer(abo),
            resumer(index),
            resumer(date_releve),
            resumer(last_index),
        )
        if date_releve is None:
            date_releve = date.today()
        if last_index is None:
            contract = await self._resolve_contract(idcontrat)
            last_index = contract.last_index
        if last_index is not None and index < last_index:
            msg = (
                f"Index {index} inférieur au dernier index connu {last_index} : "
                "saisie refusée (vérifiez le compteur)"
            )
            _LOGGER.debug("BesanconEmoiClient.async_submit_index: sortie par BesanconEmoiError")
            raise BesanconEmoiError(msg)
        data = {
            "idcontrat": idcontrat,
            "lastIndex": str(last_index if last_index is not None else ""),
            "dateReleve": date_releve.isoformat(),
            "index": str(index),
            "submit": "",
        }
        path = f"/prestations/eau/saisie/idcontrat/{idcontrat}/abo/{abo}"
        resp = await self._request("POST", path, data=data, allow_redirects=False)
        await resp.text()
        if resp.status != 302:
            msg = f"Saisie refusée par le portail (statut {resp.status})"
            _LOGGER.debug("BesanconEmoiClient.async_submit_index: sortie par BesanconEmoiError")
            raise BesanconEmoiError(msg)
        # Une redirection vers l'accueil ou la déconnexion traduit une
        # session expirée : la saisie n'a pas été enregistrée. Le portail
        # confirme l'enregistrement en redirigeant vers la page
        # « formulaires » du contrat (comportement observé).
        location = resp.headers.get("Location", "")
        if location in ("/", "") or "logout" in location:
            msg = "Session expirée pendant la saisie : relevé non enregistré"
            _LOGGER.debug("BesanconEmoiClient.async_submit_index: sortie par BesanconEmoiAuthError")
            raise BesanconEmoiAuthError(msg)
        if "/formulaires/idcontrat/" not in location:
            msg = f"Redirection inattendue après la saisie : {location}"
            _LOGGER.debug("BesanconEmoiClient.async_submit_index: sortie par BesanconEmoiError")
            raise BesanconEmoiError(msg)
        _LOGGER.info(
            "Relevé transmis pour le contrat %s : %s (au %s)",
            idcontrat,
            index,
            date_releve.isoformat(),
        )


def _parse_date(text: str) -> date | None:
    """Analyse une date au format jj/mm/aaaa."""
    _LOGGER.debug("_parse_date: entrée | text=%s", resumer(text))
    match = _DATE_RE.search(text)
    if not match:
        _LOGGER.debug("_parse_date: sortie | None")
        return None
    _LOGGER.debug("_parse_date: sortie")
    return datetime.strptime(match.group(0), "%d/%m/%Y").date()


def _parse_float(text: str) -> float | None:
    """Analyse un nombre au format français (virgule décimale)."""
    _LOGGER.debug("_parse_float: entrée | text=%s", resumer(text))
    cleaned = text.strip().replace(" ", "").replace("€", "")
    cleaned = cleaned.replace(",", ".")
    if not cleaned or not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        _LOGGER.debug("_parse_float: sortie | None")
        return None
    _LOGGER.debug("_parse_float: sortie")
    return float(cleaned)


def _parse_index_row(cells: list[str]) -> IndexReading:
    """Construit un relevé depuis une ligne du tableau d'historique."""
    _LOGGER.debug("_parse_index_row: entrée | cells=%s", resumer(cells))
    reading_date = _parse_date(cells[0]) or date.min
    index_value = int(float(cells[1])) if cells[1].isdigit() else 0
    conso = _parse_float(cells[3]) if len(cells) > 3 else None
    _LOGGER.debug("_parse_index_row: sortie")
    return IndexReading(
        date=reading_date,
        index=index_value,
        releve=cells[2],
        conso_jour=conso,
    )


_PDF_NAME_RE = re.compile(r"/\d+_(\d+)_\d+.*?\.pdf")
_DECHETS_ABO_RE = re.compile(r"/prestations/dechets/index/abo/(\d+)")
_DECHETS_ID_RE = re.compile(r"/prestations/dechets/[a-z]+/id/(\d+-\d+)")
_PAGINATION_RE = re.compile(r"/page/(\d+)")
_HEURE_RE = re.compile(r"\d{1,2}h\d{2}")


def _parse_pesees_table(text: str) -> list[Weighing]:
    """Construit les pesées depuis la table d'une page de pesées.

    Les lignes de total et la note de bas de tableau n'ont pas huit
    cellules : elles sont ignorées, seules les lignes de données sont
    retenues.
    """
    _LOGGER.debug("_parse_pesees_table: entrée | text=%s", resumer(text))
    soup = BeautifulSoup(text, "html.parser")
    weighings: list[Weighing] = []
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) != 8:
            continue
        texts = [c.get_text(strip=True) for c in cells]
        heure = texts[1] if _HEURE_RE.fullmatch(texts[1]) else ""
        weighings.append(
            Weighing(
                date=_parse_date(texts[0]),
                heure=heure,
                poids_kg=_parse_float(texts[2]),
                type_bac=texts[3],
                volume_l=int(texts[4]) if texts[4].isdigit() else None,
                vidages=int(texts[5]) if texts[5].isdigit() else None,
                no_bac=texts[6],
                no_puce=texts[7],
            )
        )
    _LOGGER.debug("_parse_pesees_table: sortie | %s", resumer(weighings))
    return weighings


def _parse_bill_row(cells: list) -> Bill:
    """Construit un prélèvement depuis une ligne du tableau des factures."""
    _LOGGER.debug("_parse_bill_row: entrée | cells=%s", resumer(cells))
    link = cells[0].find("a", href=True) if cells[0].name == "td" else None
    pdf_path = None
    facture = ""
    if link is not None:
        pdf_path = link["href"]
        # Le libellé du lien est une icône : le n° de facture est dans
        # le nom du fichier PDF (NN_NNNNN_NNNNNN_NN_NNNNNN.pdf).
        match = _PDF_NAME_RE.search(pdf_path)
        if match:
            facture = match.group(1)
    texts = [c.get_text(strip=True) for c in cells]
    bill_date = _parse_date(texts[1]) if len(texts) > 1 else None
    contrat = texts[2] if len(texts) > 2 else ""
    montant = _parse_float(texts[6]) if len(texts) > 6 else None
    _LOGGER.debug("_parse_bill_row: sortie")
    return Bill(
        facture=facture,
        date=bill_date,
        contrat=contrat,
        montant_preleve=montant,
        pdf_path=pdf_path,
    )
