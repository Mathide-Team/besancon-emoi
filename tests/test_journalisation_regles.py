"""Règles de journalisation de l'équipe (SOCLE §8, issue #24), vérifiées sur l'AST.

- ``custom_components/`` (intégration Home Assistant) : ``logging`` standard,
  un ``_LOGGER = logging.getLogger(__name__)`` par module, messages au format
  ``%s`` paresseux (jamais de f-string dans un appel de journal).
- ``scripts/`` : loguru, jamais ``import logging``, jamais ``%s`` avec arguments.
- Partout : un ``debug`` en entrée de chaque fonction non triviale, un appel
  de journal juste avant chaque ``return``, une fin de fonction tracée, et
  chaque ``except`` journalise.

Exemptions (identiques à l'audit de Gcm4, ``scripts/audit_debug_sorties.py``) :
fonction vide ou réduite à un seul ``return``/``raise``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
INTEGRATION = RACINE / "custom_components" / "besancon_emoi"
SCRIPTS = RACINE / "scripts"
NOMS_LOGGER = {"_LOGGER", "logger"}
METHODES = {"trace", "debug", "info", "success", "warning", "error", "exception", "critical", "log"}

# Fichiers hors règle, chacun avec sa raison.
EXCLUS = {
    # L'outil de résumé du journal lui-même : le tracer reboucle sur chaque trace.
    INTEGRATION / "journal.py": "outil de résumé appelé par chaque trace",
    # Script commun à tous les dépôts : converti dans modele-depot (issue #5) puis recopié.
    SCRIPTS / "check_branch_protection.py": "script du socle, Mathilde-Team/modele-depot#5",
}
# Fonctions hors règle d'entrée/sortie (fichier, nom qualifié) : raison.
FONCTIONS_EXEMPTEES = {
    (
        "scripts/tester_hors_ha.py",
        "_VersLoguru.emit",
    ): "pont logging → loguru : tracer chaque trace la dédoublerait",
}
# Scripts autorisés à importer logging : raison.
LOGGING_PERMIS = {
    "scripts/tester_hors_ha.py": "renvoie vers loguru le journal logging de api.py (règle Home Assistant)",
}


def _modules(dossier: Path) -> list[Path]:
    return sorted(p for p in dossier.glob("*.py") if p not in EXCLUS)


MODULES_HA = _modules(INTEGRATION)
MODULES_SCRIPTS = _modules(SCRIPTS)
TOUS = MODULES_HA + MODULES_SCRIPTS


def _id(p: Path) -> str:
    return str(p.relative_to(RACINE))


def _arbre(p: Path) -> ast.Module:
    return ast.parse(p.read_text(encoding="utf-8"), filename=str(p))


def _appel_journal(node: ast.AST) -> ast.Call | None:
    call = node.value if isinstance(node, ast.Expr) else node
    if (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr in METHODES
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id in NOMS_LOGGER
    ):
        return call
    return None


def _corps(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return body


def _exemptee(body: list[ast.stmt]) -> bool:
    if all(
        isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
        for s in body
    ):
        return True
    return len(body) == 1 and isinstance(body[0], (ast.Return, ast.Raise))


def _blocs(stmt: ast.stmt) -> list[list[ast.stmt]]:
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return []
    blocs = [getattr(stmt, f) for f in ("body", "orelse", "finalbody") if getattr(stmt, f, None)]
    blocs += [h.body for h in getattr(stmt, "handlers", [])]
    blocs += [c.body for c in getattr(stmt, "cases", [])]
    return blocs


def _returns_non_traces(bloc: list[ast.stmt]) -> list[int]:
    fautes, precedent = [], None
    for stmt in bloc:
        if isinstance(stmt, ast.Return) and (precedent is None or _appel_journal(precedent) is None):
            fautes.append(stmt.lineno)
        for b in _blocs(stmt):
            fautes += _returns_non_traces(b)
        precedent = stmt
    return fautes


def _finit_trace(bloc: list[ast.stmt]) -> bool:
    if not bloc:
        return False
    d = bloc[-1]
    if isinstance(d, (ast.Return, ast.Raise)) or _appel_journal(d) is not None:
        return True
    if isinstance(d, ast.If):
        return _finit_trace(d.body) and _finit_trace(d.orelse)
    if isinstance(d, (ast.With, ast.AsyncWith)):
        return _finit_trace(d.body)
    if isinstance(d, ast.Try):
        if d.finalbody and _finit_trace(d.finalbody):
            return True
        return _finit_trace(d.orelse or d.body) and all(_finit_trace(h.body) for h in d.handlers)
    if isinstance(d, ast.Match):
        return all(_finit_trace(c.body) for c in d.cases)
    return isinstance(d, ast.While) and isinstance(d.test, ast.Constant) and d.test.value is True


def _fonctions(arbre: ast.Module):
    return [n for n in ast.walk(arbre) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _qualifiees(arbre: ast.Module) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """(nom qualifié, fonction), classes et fonctions englobantes comprises."""
    resultat = []

    def visiter(noeud: ast.AST, pile: list[str]) -> None:
        for enfant in ast.iter_child_nodes(noeud):
            if isinstance(enfant, (ast.FunctionDef, ast.AsyncFunctionDef)):
                resultat.append((".".join([*pile, enfant.name]), enfant))
                visiter(enfant, [*pile, enfant.name])
            elif isinstance(enfant, ast.ClassDef):
                visiter(enfant, [*pile, enfant.name])
            else:
                visiter(enfant, pile)

    visiter(arbre, [])
    return resultat


@pytest.mark.parametrize("module", TOUS, ids=_id)
def test_entree_et_sorties_tracees(module: Path) -> None:
    fautes = []
    for nom, fn in _qualifiees(_arbre(module)):
        body = _corps(fn)
        if _exemptee(body) or (_id(module), nom) in FONCTIONS_EXEMPTEES:
            continue
        premier = _appel_journal(body[0])
        if premier is None or premier.func.attr not in {"debug", "trace"}:
            fautes.append(f"{fn.name}:{fn.lineno} sans debug d'entrée")
        fautes += [f"{fn.name}:{ligne} return sans trace" for ligne in _returns_non_traces(body)]
        if not _finit_trace(body):
            fautes.append(f"{fn.name}:{fn.lineno} fin de fonction sans trace")
    assert not fautes, "\n".join(fautes)


@pytest.mark.parametrize("module", TOUS, ids=_id)
def test_chaque_except_journalise(module: Path) -> None:
    arbre = _arbre(module)
    exemptees = [
        range(fn.lineno, fn.end_lineno + 1)
        for nom, fn in _qualifiees(arbre)
        if (_id(module), nom) in FONCTIONS_EXEMPTEES
    ]
    fautes = [
        f"ligne {h.lineno}"
        for h in ast.walk(arbre)
        if isinstance(h, ast.ExceptHandler)
        and not any(h.lineno in r for r in exemptees)
        and not any(_appel_journal(n) for n in ast.walk(h))
    ]
    assert not fautes, f"except sans journal : {fautes}"


@pytest.mark.parametrize("module", MODULES_HA, ids=_id)
def test_integration_logger_standard_par_module(module: Path) -> None:
    arbre = _arbre(module)
    if not _fonctions(arbre):
        pytest.skip("module de constantes, rien à journaliser")
    definitions = [
        n
        for n in arbre.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_LOGGER" for t in n.targets)
        and ast.unparse(n.value) == "logging.getLogger(__name__)"
    ]
    assert definitions, "_LOGGER = logging.getLogger(__name__) manquant"
    assert "loguru" not in module.read_text(encoding="utf-8"), "Home Assistant : logging standard, pas loguru"


@pytest.mark.parametrize("module", MODULES_HA, ids=_id)
def test_integration_messages_paresseux(module: Path) -> None:
    """Jamais de f-string ni de ``%`` immédiat : le message n'est formaté que si le niveau est actif."""
    fautes = []
    for node in ast.walk(_arbre(module)):
        call = _appel_journal(node) if isinstance(node, ast.Call) else None
        if call is not None and call.args:
            message = call.args[0]
            if isinstance(message, ast.JoinedStr) or (
                isinstance(message, ast.BinOp) and isinstance(message.op, ast.Mod)
            ):
                fautes.append(node.lineno)
    assert not fautes, f"message formaté d'avance, lignes {fautes}"


@pytest.mark.parametrize("module", MODULES_SCRIPTS, ids=_id)
def test_scripts_loguru(module: Path) -> None:
    arbre = _arbre(module)
    imports = {a.name for n in ast.walk(arbre) if isinstance(n, ast.Import) for a in n.names}
    if _id(module) not in LOGGING_PERMIS:
        assert "logging" not in imports, "scripts : loguru, pas logging"
    if _fonctions(arbre):
        assert any(
            isinstance(n, ast.ImportFrom)
            and n.module == "loguru"
            and any(a.name == "logger" for a in n.names)
            for n in arbre.body
        ), "from loguru import logger manquant"


@pytest.mark.parametrize("module", MODULES_SCRIPTS, ids=_id)
def test_scripts_sans_pourcent_s(module: Path) -> None:
    """loguru formate avec ``{}`` : un ``%s`` avec arguments s'afficherait tel quel."""
    fautes = []
    for node in ast.walk(_arbre(module)):
        call = _appel_journal(node) if isinstance(node, ast.Call) else None
        if call is not None and len(call.args) > 1 and isinstance(call.args[0], ast.Constant):
            texte = str(call.args[0].value)
            if "%" in texte and "{" not in texte:
                fautes.append(node.lineno)
    assert not fautes, f"format % avec loguru, lignes {fautes}"


def test_les_exclusions_existent() -> None:
    """Une exclusion qui ne désigne plus rien doit disparaître de la liste."""
    assert all(p.exists() for p in EXCLUS)
    assert all((RACINE / f).exists() for f in LOGGING_PERMIS)
    for fichier, nom in FONCTIONS_EXEMPTEES:
        assert nom in dict(_qualifiees(_arbre(RACINE / fichier))), f"{fichier}:{nom} n'existe plus"
