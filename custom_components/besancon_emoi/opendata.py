"""Client open data Grand Besançon : calendrier de collecte des déchets.

Le portail « Besançon et moi » affiche le calendrier de collecte via un
encart qui interroge l'open data de Grand Besançon Métropole : ces
données sont publiques, sans connexion. Le jour de collecte est donné
par commune, rue et éventuellement numéro, avec une parité de semaine
(« toutes les semaines » ou « toutes les 2 semaines (semaines
paires/impaires) »).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from aiohttp import ClientResponseError, ClientSession
from bs4 import BeautifulSoup

DATASET_URL = "https://data.grandbesancon.fr/opendata/dataset/joursDeCollecteDechets/"
STREETS_URL = (
    "https://datasets.grandbesancon.fr/besanconfr.php?method=getRuesCommuneAction&output=json&insee={insee}"
)

JOURS: dict[str, int] = {
    "lundi": 0,
    "mardi": 1,
    "mercredi": 2,
    "jeudi": 3,
    "vendredi": 4,
    "samedi": 5,
    "dimanche": 6,
}

PARITE_TOUTES = "toutes"
PARITE_IMPAIRES = "impaires"
PARITE_PAIRES = "paires"

# « jeudi toutes les semaines » ou « mardi toutes les 2 semaines
# (semaines paires) » — le portail emploie les deux formulations.
_HORAIRE = (
    r"(\S+)\s+(?:toutes les semaines|toutes les 2 semaines "
    r"\(semaines (?:paires|impaires)\))"
)
_RESIDUELS_RE = re.compile(r"Résiduels\s*:?\s*" + _HORAIRE)
_RECYCLABLES_RE = re.compile(r"Recyclables\s*:?\s*" + _HORAIRE)


class OpendataError(Exception):
    """Erreur d'accès à l'open data Grand Besançon."""


@dataclass
class Commune:
    """Une commune du Grand Besançon (code INSEE + nom)."""

    insee: str
    nom: str


@dataclass
class Rue:
    """Une rue du référentiel open data (code + nom)."""

    code: str
    nom: str


@dataclass
class CollecteHoraire:
    """Jour et parité d'une collecte (résiduels ou recyclables)."""

    jour: str
    parite: str  # PARITE_TOUTES | PARITE_IMPAIRES | PARITE_PAIRES

    def correspond(self, jour: date) -> bool:
        """Ce jour porte-t-il cette collecte ?"""
        numero = JOURS.get(self.jour.lower())
        if numero is None or jour.weekday() != numero:
            return False
        if self.parite == PARITE_IMPAIRES and jour.isocalendar()[1] % 2 == 0:
            return False
        return self.parite != PARITE_PAIRES or jour.isocalendar()[1] % 2 == 0

    def prochaine(self, a_partir_de: date) -> date | None:
        """Prochaine collecte à partir d'une date (incluse), ou None."""
        jour = a_partir_de
        for _ in range(56):  # au plus huit semaines de recherche
            if self.correspond(jour):
                return jour
            jour = jour + timedelta(days=1)
        return None

    def dates_dans(self, debut: date, fin: date) -> list[date]:
        """Toutes les collectes entre deux dates (bornes incluses)."""
        dates: list[date] = []
        jour = self.prochaine(debut)
        while jour is not None and jour <= fin:
            dates.append(jour)
            jour = self.prochaine(jour + timedelta(days=1))
        return dates


@dataclass
class CollecteAdresse:
    """Le calendrier de collecte d'une adresse."""

    adresse: str
    residuels: CollecteHoraire | None = None
    recyclables: CollecteHoraire | None = None
    calendrier_pdf: str | None = None


@dataclass
class CollecteData:
    """Données de collecte mémorisées par le coordinateur."""

    commune: str
    rue: str
    adresses: list[CollecteAdresse]

    def principale(self) -> CollecteAdresse | None:
        """Première adresse : celle qui pilote les capteurs."""
        return self.adresses[0] if self.adresses else None


class GrandBesanconOpendataClient:
    """Client de l'open data Grand Besançon (sans authentification)."""

    def __init__(
        self,
        session: ClientSession,
        dataset_url: str = DATASET_URL,
        streets_url: str = STREETS_URL,
    ) -> None:
        self._session = session
        self._dataset_url = dataset_url
        self._streets_url = streets_url

    async def async_get_communes(self) -> list[Commune]:
        """Liste les communes proposées par le formulaire public."""
        html = await self._async_get(self._dataset_url)
        soup = BeautifulSoup(html, "html.parser")
        select = soup.find("select", {"name": "commune"})
        if select is None:
            msg = "Formulaire de collecte introuvable sur la page open data"
            raise OpendataError(msg)
        communes = [
            Commune(insee=option.get("value", "").strip(), nom=option.get_text(strip=True))
            for option in select.find_all("option")
            if option.get("value", "").strip()
        ]
        return communes

    async def async_get_streets(self, insee: str) -> list[Rue]:
        """Liste les rues d'une commune (référentiel du formulaire)."""
        url = self._streets_url.format(insee=insee)
        try:
            async with self._session.get(url) as response:
                response.raise_for_status()
                donnees = await response.json()
        except (ClientResponseError, TimeoutError) as err:
            msg = f"Impossible de récupérer la liste des rues : {err}"
            raise OpendataError(msg) from err
        rues = [
            Rue(code=str(rue["cdrue"]), nom=rue["librueweb"])
            for rue in donnees
            if rue.get("cdrue") and rue.get("librueweb")
        ]
        rues.sort(key=lambda rue: rue.nom.lower())
        return rues

    async def async_get_collecte(
        self, commune: str, rue: str, numero: str | None = None
    ) -> list[CollecteAdresse]:
        """Demande le calendrier de collecte d'une rue (et numéro).

        Reproduit le formulaire public de l'open data : la réponse est
        une table « Calendrier de collecte pour les adresses
        concernées » avec une ligne par adresse et, dans la deuxième
        cellule, les jours des résiduels et des recyclables.
        """
        formulaire = {"commune": commune, "rue": rue}
        if numero:
            formulaire["numero"] = numero
        html = await self._async_post(self._dataset_url, formulaire)
        return _parse_collecte_page(html)

    async def _async_get(self, url: str) -> str:
        """GET protégé sur l'open data."""
        try:
            async with self._session.get(url) as response:
                response.raise_for_status()
                return await response.text()
        except (ClientResponseError, TimeoutError) as err:
            msg = f"Open data inaccessible : {err}"
            raise OpendataError(msg) from err

    async def _async_post(self, url: str, donnees: dict[str, str]) -> str:
        """POST protégé sur l'open data."""
        try:
            async with self._session.post(url, data=donnees) as response:
                response.raise_for_status()
                return await response.text()
        except (ClientResponseError, TimeoutError) as err:
            msg = f"Open data inaccessible : {err}"
            raise OpendataError(msg) from err


def _parse_collecte_page(html: str) -> list[CollecteAdresse]:
    """Construit les adresses depuis la page de résultat du formulaire.

    La table du résultat a deux ou trois colonnes : l'adresse, les jours
    de collecte, et parfois le calendrier PDF téléchargeable.
    """
    soup = BeautifulSoup(html, "html.parser")
    adresses: list[CollecteAdresse] = []
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        texte = cells[1].get_text(" ", strip=True)
        lien = row.find("a", href=re.compile(r"\.pdf"))
        adresses.append(
            CollecteAdresse(
                adresse=cells[0].get_text(" ", strip=True),
                residuels=_parse_horaire(_RESIDUELS_RE, texte),
                recyclables=_parse_horaire(_RECYCLABLES_RE, texte),
                calendrier_pdf=lien["href"] if lien else None,
            )
        )
    return adresses


def _parse_horaire(motif: re.Pattern[str], texte: str) -> CollecteHoraire | None:
    """Extrait un jour et sa parité depuis le texte de la cellule."""
    match = motif.search(texte)
    if match is None or match.group(1).lower() not in JOURS:
        return None
    brut = match.group(0).lower()
    parite = PARITE_TOUTES
    if "semaines impaires" in brut:
        parite = PARITE_IMPAIRES
    elif "semaines paires" in brut:
        parite = PARITE_PAIRES
    return CollecteHoraire(jour=match.group(1).lower(), parite=parite)
