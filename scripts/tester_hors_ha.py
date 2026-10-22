"""Essai de l'intégration « Besançon et moi » hors de Home Assistant.

Charge le client du portail (api.py) sans Home Assistant et vérifie, en
lecture seule : la connexion, les contrats d'eau, l'historique d'index et
les derniers prélèvements. Aucune donnée n'est envoyée au portail (pas de
saisie de relevé, pas d'écriture).

Utilisation, depuis la racine du dépôt :

    BESANCON_EMOI_EMAIL=... BESANCON_EMOI_PASSWORD=... \
        python scripts/tester_hors_ha.py

Sans variables d'environnement, les identifiants sont demandés au
lancement (le mot de passe est saisi sans écho). Prérequis : aiohttp et
beautifulsoup4, loguru (``uv run --only-group test python scripts/tester_hors_ha.py``).
"""

from __future__ import annotations

import asyncio
import getpass
import importlib
import logging
import os
import sys
import types
from pathlib import Path

from loguru import logger

# Le client vit dans le paquet Home Assistant, dont le __init__ importe
# Home Assistant. Pour rester « hors HA », un paquet fictif est monté sur
# le dossier du composant : seuls const.py et api.py sont chargés.
COMPOSANT = Path(__file__).resolve().parents[1] / "custom_components" / "besancon_emoi"
NOM_PAQUET = "besancon_emoi_hors_ha"


def _charger_client() -> types.ModuleType:
    """Charge le module api sans déclencher l'import de Home Assistant."""
    logger.debug("_charger_client: entrée")
    paquet = types.ModuleType(NOM_PAQUET)
    paquet.__path__ = [str(COMPOSANT)]
    sys.modules[NOM_PAQUET] = paquet
    importlib.import_module(f"{NOM_PAQUET}.const")
    logger.debug("_charger_client: sortie")
    return importlib.import_module(f"{NOM_PAQUET}.api")


async def _essai_lecture_seule(api: types.ModuleType, email: str, password: str) -> None:
    """Déroule les vérifications en lecture seule et affiche le résultat."""
    logger.debug("_essai_lecture_seule: entrée | api={!r:.80}, email=***, password=***", api)
    from aiohttp import ClientSession

    async with ClientSession() as session:
        client = api.BesanconEmoiClient(session)
        await client.async_login(email, password)
        contrats = await client.async_get_contracts()
        print(f"{len(contrats)} contrat(s) d'eau trouvé(s)")
        for contrat in contrats:
            print(
                f"- contrat {contrat.idcontrat} (abo {contrat.abo}), "
                f"dernier index connu : {contrat.last_index}"
            )
            historique = await client.async_get_index_history(contrat.abo)
            for releve in historique[:3]:
                print(f"    relevé du {releve.date.isoformat()} : index {releve.index} ({releve.releve})")
            factures = await client.async_get_bills(contrat.abo)
            for facture in factures[:3]:
                montant = f"{facture.montant_preleve} €" if facture.montant_preleve else "?"
                print(
                    f"    facture {facture.facture or '?'} du "
                    f"{facture.date.isoformat() if facture.date else '?'} : {montant}"
                )
    logger.debug("_essai_lecture_seule: sortie")


class _VersLoguru(logging.Handler):
    """Renvoie le journal ``logging`` du client (api.py, règle Home Assistant) vers loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            niveau: str | int = logger.level(record.levelname).name
        except ValueError:  # niveau propre à logging : repris tel quel (pas de trace, on est dans le journal)
            niveau = record.levelno
        logger.opt(depth=6, exception=record.exc_info).log(niveau, record.getMessage())


def _brancher_journal() -> None:
    """Un seul format, celui de loguru, pour le script et le client."""
    logger.debug("_brancher_journal: entrée")
    logging.basicConfig(handlers=[_VersLoguru()], level=logging.INFO, force=True)
    logger.debug("_brancher_journal: sortie")


async def main() -> int:
    """Point d'entrée : renvoie 0 si tout est lu, 1 sinon."""
    logger.debug("main: entrée")
    _brancher_journal()
    api = _charger_client()
    email = os.environ.get("BESANCON_EMOI_EMAIL") or input("Courriel : ")
    password = os.environ.get("BESANCON_EMOI_PASSWORD") or getpass.getpass("Mot de passe : ")
    try:
        await _essai_lecture_seule(api, email, password)
    except api.BesanconEmoiAuthError as err:
        logger.error("Échec d'authentification : {}", err)
        return 1
    except api.BesanconEmoiError as err:
        logger.error("Échec de connexion au portail : {}", err)
        return 1
    print("Essai terminé sans erreur (lecture seule).")
    logger.debug("main: sortie | 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
