#!/usr/bin/env python3
"""Compare la protection réelle d'une branche à la règle versionnée.

Règle : .github/branch-protection/<branche>.json (format de l'API GitHub
« Update branch protection », applicable telle quelle par un administrateur) :

    gh api -X PUT repos/<owner>/<repo>/branches/<branche>/protection \
      --input .github/branch-protection/<branche>.json

Avec le GITHUB_TOKEN d'un workflow (sans droit d'administration), seule la
partie publique de la protection est lisible (GET /branches/<branche>) :
branche protégée, checks requis, application aux administrateurs. Le reste de
la règle n'est pas vérifiable ici et est signalé en information.

Bibliothèque standard uniquement. Code de sortie 1 en cas d'écart.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def lire_protection(depot: str, branche: str, jeton: str | None) -> dict:
    """Renvoie la représentation publique de la branche (GET /branches/<branche>)."""
    requete = urllib.request.Request(
        f"https://api.github.com/repos/{depot}/branches/{branche}",
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    )
    if jeton:
        requete.add_header("Authorization", f"Bearer {jeton}")
    with urllib.request.urlopen(requete, timeout=30) as reponse:
        return json.load(reponse)


def checks_attendus(regle: dict) -> set[str]:
    """Noms des checks requis par la règle versionnée."""
    rsc = regle.get("required_status_checks") or {}
    noms = {c["context"] for c in rsc.get("checks", [])}
    return noms | set(rsc.get("contexts", []))


def comparer(regle: dict, branche_api: dict) -> list[str]:
    """Liste des écarts entre la règle et la protection réelle."""
    if not branche_api.get("protected"):
        return ["la branche n'est pas protégée"]
    ecarts = []
    protection = branche_api.get("protection") or {}
    rsc = protection.get("required_status_checks") or {}
    reels = {c["context"] for c in rsc.get("checks", [])} | set(rsc.get("contexts", []))
    attendus = checks_attendus(regle)
    if manquants := sorted(attendus - reels):
        ecarts.append(f"checks requis absents : {', '.join(manquants)}")
    if en_trop := sorted(reels - attendus):
        ecarts.append(f"checks requis non prévus par la règle : {', '.join(en_trop)}")
    admins_attendu = bool(regle.get("enforce_admins"))
    admins_reel = rsc.get("enforcement_level") == "everyone"
    if attendus and admins_attendu != admins_reel:
        ecarts.append(
            f"application aux administrateurs : attendu {admins_attendu}, réel {admins_reel}"
        )
    return ecarts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--branche", required=True)
    parser.add_argument("--depot", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--regles", default=".github/branch-protection")
    args = parser.parse_args()
    if not args.depot:
        print("::error::dépôt inconnu (--depot ou GITHUB_REPOSITORY)")
        return 2

    regle = json.loads(Path(args.regles, f"{args.branche}.json").read_text(encoding="utf-8"))
    try:
        branche_api = lire_protection(args.depot, args.branche, os.environ.get("GITHUB_TOKEN"))
    except urllib.error.HTTPError as erreur:
        print(f"::error::lecture de la branche {args.branche} impossible : HTTP {erreur.code}")
        return 1

    ecarts = comparer(regle, branche_api)
    for ecart in ecarts:
        print(f"::error::{args.branche} : {ecart}")
    print(
        "::notice::non vérifiable avec le GITHUB_TOKEN : branche à jour (strict), revues de PR, "
        "force-push, suppression"
    )
    if ecarts:
        print(
            f"Pour appliquer la règle : gh api -X PUT repos/{args.depot}/branches/{args.branche}"
            f"/protection --input {args.regles}/{args.branche}.json"
        )
        return 1
    print(f"Protection de {args.branche} conforme à {args.regles}/{args.branche}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
