"""Client du portail « Besançon et moi » (besancon-emoi.fr).

Le portail n'expose pas d'API publique : ce client s'appuie sur les pages
utilisées par le site (connexion, contrats, factures, historique d'index,
saisie du relevé de compteur).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from aiohttp import ClientError, ClientResponse, ClientSession
from bs4 import BeautifulSoup

from .const import BASE_URL, LOGGER

_LOGIN_FORM_MARKER = 'name="pwd"'

# Journalisation de l'authentification : tant qu'aucune connexion n'a
# réussi — et après chaque nouvel échec —, les étapes de connexion sont
# tracées au niveau INFO pour rester visibles dans le journal sans avoir
# à activer les logs de debug. Après une première connexion réussie,
# elles retombent au niveau DEBUG (convention équipe Mathilde-Team).
_auth_debug_actif = True


def _auth_log(message: str, *args: Any) -> None:
    """Trace une étape d'authentification.

    INFO tant qu'aucune connexion n'a réussi (visible par défaut),
    DEBUG ensuite : l'authentification ne reste bavarde que tant
    qu'elle échoue.
    """
    if _auth_debug_actif:
        LOGGER.info(message, *args)
    else:
        LOGGER.debug(message, *args)


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
        self._session = session
        self._base_url = base_url.rstrip("/")

    async def _request(self, method: str, path: str, **kwargs: Any) -> ClientResponse:
        """Effectue une requête et détecte une session expirée."""
        url = f"{self._base_url}{path}"
        try:
            resp = await self._session.request(method, url, **kwargs)
        except (TimeoutError, ClientError) as err:
            msg = f"Requête {method} {path} impossible : {err}"
            raise BesanconEmoiError(msg) from err
        if resp.status in (500, 502, 503, 504):
            msg = f"Portail indisponible ({resp.status}) sur {path}"
            raise BesanconEmoiError(msg)
        if resp.status in (401, 403):
            raise BesanconEmoiAuthError(f"Accès refusé sur {path}")
        return resp

    async def _get_text(self, path: str) -> str:
        """GET et retourne le corps HTML, en vérifiant la session."""
        resp = await self._request("GET", path)
        text = await resp.text()
        if _LOGIN_FORM_MARKER in text:
            msg = f"Session expirée (formulaire de connexion sur {path})"
            raise BesanconEmoiAuthError(msg)
        return text

    async def async_download_bill_pdf(self, pdf_path: str) -> bytes:
        """Télécharge le PDF d'une facture (chemin vu dans la liste).

        Le chemin relatif est exposé par les capteurs ; le téléchargement
        requiert la session authentifiée.
        """
        if not pdf_path.startswith("/"):
            msg = "Chemin de facture invalide"
            raise BesanconEmoiError(msg)
        resp = await self._request("GET", pdf_path)
        if resp.status != 200 or "pdf" not in resp.content_type:
            msg = f"Téléchargement de la facture impossible ({resp.status})"
            raise BesanconEmoiError(msg)
        return await resp.read()

    async def close(self) -> None:
        """Ferme la session HTTP dédiée à ce compte."""
        if not self._session.closed:
            await self._session.close()

    async def async_login(self, email: str, password: str) -> None:
        """Se connecte au portail.

        Le portail répond 302 vers la page d'accueil en cas de succès et
        ré-affiche le formulaire de connexion sinon.

        Les étapes sont tracées tant qu'aucune connexion n'a réussi
        (convention équipe) : un échec de connexion laisse toujours
        une trace exploitable dans le journal.
        """
        global _auth_debug_actif
        _auth_log("Connexion au portail %s", self._base_url)
        resp = await self._request(
            "POST",
            "/",
            data={"email": email, "pwd": password},
            allow_redirects=False,
        )
        await resp.text()
        if resp.status != 302:
            # Échec : l'authentification (re)devient bavarde.
            _auth_debug_actif = True
            LOGGER.warning(
                "Connexion refusée par le portail (statut %s, redirection attendue) : "
                "authentification maintenue en mode bavard",
                resp.status,
            )
            raise BesanconEmoiAuthError("Identifiants refusés par le portail")
        _auth_log("Connexion acceptée (redirection vers %s)", resp.headers.get("Location", ""))
        # Vérifie que la session est bien ouverte.
        try:
            await self._get_text("/prestations/eau/contrats")
        except BesanconEmoiAuthError:
            _auth_debug_actif = True
            LOGGER.warning(
                "Session non ouverte après connexion acceptée : authentification maintenue en mode bavard"
            )
            raise
        # Premier succès : l'authentification redevient silencieuse.
        _auth_debug_actif = False
        LOGGER.info("Connexion réussie : authentification repasse en mode silencieux (debug)")

    async def async_get_contracts(self) -> list[Contract]:
        """Liste les contrats d'eau (idcontrat, abo, dernier index).

        L'appariement idcontrat ↔ abo se fait via le formulaire de saisie
        du relevé, dont l'action contient les deux identifiants.
        """
        text = await self._get_text("/prestations/eau/contrats")
        idcontrats = list(dict.fromkeys(_IDCONTRAT_RE.findall(text)))
        contracts: list[Contract] = []
        for idcontrat in idcontrats:
            contracts.append(await self._resolve_contract(idcontrat))
        if not contracts:
            LOGGER.warning("Aucun contrat d'eau trouvé sur le portail")
        return contracts

    async def _resolve_contract(self, idcontrat: str) -> Contract:
        """Résout l'abo et le dernier index depuis le formulaire de saisie."""
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
        return Contract(idcontrat=idcontrat, abo=abo, last_index=last_index)

    async def async_get_index_history(self, abo: str) -> list[IndexReading]:
        """Historique des relevés d'un abonnement.

        La page contient plusieurs tableaux ; celui des relevés a pour
        entêtes « Date », « Index », « Relevé », « Consommation(m3/jour) ».
        """
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
        return readings

    async def async_get_bills(self, abo: str) -> list[Bill]:
        """Liste les factures d'un abonnement (tableau AJAX)."""
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
        return bills

    async def async_get_waste_contracts(self) -> list[WasteContract]:
        """Liste les contrats déchets du compte.

        La page d'accueil de l'espace personnel référence chaque
        prestation par son abonnement (``/prestations/dechets/index/abo/…``) ;
        les pages déchets référencent ensuite le contrat par son
        identifiant (``/prestations/dechets/…/id/NNNNNN-NN``).
        """
        home_text = await self._get_text("/")
        contracts: list[WasteContract] = []
        for abo in dict.fromkeys(_DECHETS_ABO_RE.findall(home_text)):
            text = await self._get_text(f"/prestations/dechets/index/abo/{abo}")
            for idcontrat in dict.fromkeys(_DECHETS_ID_RE.findall(text)):
                contracts.append(WasteContract(idcontrat=idcontrat, abo=abo))
        LOGGER.debug("%d contrats déchets récupérés", len(contracts))
        return contracts

    async def async_get_weighings(self, idcontrat: str, jaune: bool = False) -> list[Weighing]:
        """Liste les pesées d'un contrat déchets (toutes les pages).

        La liste « résiduel » regroupe les pesées facturées ; la liste
        « jaune » les pesées des bacs recyclables. La table est paginée
        (50 lignes par page) : les pages suivantes sont lues jusqu'à
        la dernière référencée.
        """
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
        LOGGER.debug(
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
            raise BesanconEmoiError(msg)
        # Une redirection vers l'accueil ou la déconnexion traduit une
        # session expirée : la saisie n'a pas été enregistrée. Le portail
        # confirme l'enregistrement en redirigeant vers la page
        # « formulaires » du contrat (comportement observé).
        location = resp.headers.get("Location", "")
        if location in ("/", "") or "logout" in location:
            msg = "Session expirée pendant la saisie : relevé non enregistré"
            raise BesanconEmoiAuthError(msg)
        if "/formulaires/idcontrat/" not in location:
            msg = f"Redirection inattendue après la saisie : {location}"
            raise BesanconEmoiError(msg)
        LOGGER.info(
            "Relevé transmis pour le contrat %s : %s (au %s)",
            idcontrat,
            index,
            date_releve.isoformat(),
        )


def _parse_date(text: str) -> date | None:
    """Analyse une date au format jj/mm/aaaa."""
    match = _DATE_RE.search(text)
    if not match:
        return None
    return datetime.strptime(match.group(0), "%d/%m/%Y").date()


def _parse_float(text: str) -> float | None:
    """Analyse un nombre au format français (virgule décimale)."""
    cleaned = text.strip().replace(" ", "").replace("€", "")
    cleaned = cleaned.replace(",", ".")
    if not cleaned or not re.fullmatch(r"-?\d+(\.\d+)?", cleaned):
        return None
    return float(cleaned)


def _parse_index_row(cells: list[str]) -> IndexReading:
    """Construit un relevé depuis une ligne du tableau d'historique."""
    reading_date = _parse_date(cells[0]) or date.min
    index_value = int(float(cells[1])) if cells[1].isdigit() else 0
    conso = _parse_float(cells[3]) if len(cells) > 3 else None
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
    return weighings


def _parse_bill_row(cells: list) -> Bill:
    """Construit un prélèvement depuis une ligne du tableau des factures."""
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
    return Bill(
        facture=facture,
        date=bill_date,
        contrat=contrat,
        montant_preleve=montant,
        pdf_path=pdf_path,
    )
