"""Scripts du dépôt (issue #24) : notes de version et essai hors Home Assistant.

Aucun accès réseau : le client du portail est remplacé par un double.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import types
from datetime import date
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]


def _charger(nom: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(f"_script_{nom}", RACINE / "scripts" / f"{nom}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


publication = _charger("notes_de_version")
essai = _charger("tester_hors_ha")


# --- Notes de version ------------------------------------------------------


def test_notes_de_la_version_du_manifest(capsys) -> None:
    version = publication.version_manifest()
    assert publication.main(["notes", f"v{version}"]) == 0
    sortie = capsys.readouterr().out
    assert sortie.strip()
    assert "## [" not in sortie  # une seule section, sans son titre


def test_tag_different_du_manifest(capsys, journal_loguru) -> None:
    assert publication.main(["notes", "v9.9.9"]) == 1
    assert any("manifest.json" in m for m in journal_loguru)
    assert capsys.readouterr().out == ""  # rien dans notes.md
    assert publication.main(["notes"]) == 2
    assert "Usage" in capsys.readouterr().err


def test_version_absente_du_journal() -> None:
    journal = "# Journal\n\n## [0.2.0] - 2027-01-01\n\n- truc\n\n## [0.1.0] - 2026-10-18\n\n"
    assert publication.notes("0.2.0", journal) == "- truc\n"
    with pytest.raises(ValueError, match="absente"):
        publication.notes("0.1.0", journal)  # section vide
    with pytest.raises(ValueError, match="absente"):
        publication.notes("0.3.0", journal)


def test_version_absente_du_vrai_journal(monkeypatch, capsys, journal_loguru) -> None:
    monkeypatch.setattr(publication, "version_manifest", lambda: "0.0.1")
    assert publication.main(["notes", "v0.0.1"]) == 1
    assert any("absente" in m for m in journal_loguru)
    assert capsys.readouterr().out == ""


# --- Essai hors Home Assistant ---------------------------------------------


def test_client_charge_sans_home_assistant(monkeypatch) -> None:
    """api.py se charge via un paquet fictif, sans importer Home Assistant."""
    monkeypatch.delitem(sys.modules, essai.NOM_PAQUET, raising=False)
    api = essai._charger_client()
    assert api.__name__ == f"{essai.NOM_PAQUET}.api"
    assert hasattr(api, "BesanconEmoiClient")
    assert not any(n == "homeassistant" or n.startswith("homeassistant.") for n in api.__dict__)


def test_pont_logging_vers_loguru(journal_loguru) -> None:
    """Le journal ``logging`` du client ressort dans loguru, niveaux compris."""
    essai._brancher_journal()
    try:
        journal = logging.getLogger("custom_components.besancon_emoi.api")
        journal.info("Connexion au portail : %s", "étape 1")
        journal.warning("portail lent")
        journal.log(25, "niveau propre à logging")  # pas de nom loguru : repris par son numéro
        journal.debug("invisible au niveau INFO")
    finally:
        logging.basicConfig(handlers=[logging.NullHandler()], force=True)
    assert journal_loguru == ["Connexion au portail : étape 1", "portail lent", "niveau propre à logging"]


class _Contrat:
    idcontrat, abo, last_index = "C1", "A1", 120.0


class _Releve:
    date, index, releve = date(2026, 10, 1), 118.0, "R"


class _Facture:
    def __init__(self, montant: float | None, quand: date | None, numero: str | None) -> None:
        self.montant_preleve, self.date, self.facture = montant, quand, numero


def _faux_api(erreur: str | None = None) -> types.SimpleNamespace:
    class Erreur(Exception):
        pass

    class ErreurAuth(Erreur):
        pass

    class Client:
        def __init__(self, session) -> None:
            self.session = session

        async def async_login(self, email: str, password: str) -> None:
            if erreur is not None:
                raise {"auth": ErreurAuth, "portail": Erreur}[erreur]("refusé")

        async def async_get_contracts(self):
            return [_Contrat()]

        async def async_get_index_history(self, abo: str):
            return [_Releve()]

        async def async_get_bills(self, abo: str):
            return [_Facture(42.5, date(2026, 9, 1), "F1"), _Facture(None, None, None)]

    return types.SimpleNamespace(
        BesanconEmoiClient=Client, BesanconEmoiError=Erreur, BesanconEmoiAuthError=ErreurAuth
    )


@pytest.fixture
def identifiants(monkeypatch) -> None:
    monkeypatch.setenv("BESANCON_EMOI_EMAIL", "test@example.org")
    monkeypatch.setenv("BESANCON_EMOI_PASSWORD", "mot-de-passe-de-test")


async def test_essai_complet(monkeypatch, capsys, identifiants) -> None:
    monkeypatch.setattr(essai, "_charger_client", lambda: _faux_api())
    try:
        assert await essai.main() == 0
    finally:
        logging.basicConfig(handlers=[logging.NullHandler()], force=True)
    sortie = capsys.readouterr().out
    assert "1 contrat(s) d'eau trouvé(s)" in sortie
    assert "relevé du 2026-10-01 : index 118.0 (R)" in sortie
    assert "facture F1 du 2026-09-01 : 42.5 €" in sortie
    assert "facture ? du ? : ?" in sortie
    assert "Essai terminé sans erreur" in sortie
    assert "mot-de-passe-de-test" not in sortie


@pytest.mark.parametrize(
    ("erreur", "attendu"), [("auth", "Échec d'authentification"), ("portail", "Échec de connexion")]
)
async def test_essai_en_echec(monkeypatch, capsys, journal_loguru, identifiants, erreur, attendu) -> None:
    monkeypatch.setattr(essai, "_charger_client", lambda: _faux_api(erreur))
    try:
        assert await essai.main() == 1
    finally:
        logging.basicConfig(handlers=[logging.NullHandler()], force=True)
    assert any(attendu in m for m in journal_loguru)
    assert "Essai terminé" not in capsys.readouterr().out
    assert not any("mot-de-passe-de-test" in m or "test@example.org" in m for m in journal_loguru)
