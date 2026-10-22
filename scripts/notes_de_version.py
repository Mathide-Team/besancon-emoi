"""Notes d'une version, tirées de CHANGELOG.md, et contrôle tag = version du manifest (issue #10).

Usage : python scripts/notes_de_version.py v0.1.0 > notes.md
Code de sortie 1 si le tag ne correspond pas à manifest.json ou si la version manque au journal.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from loguru import logger

RACINE = Path(__file__).resolve().parents[1]


def version_manifest() -> str:
    return json.loads((RACINE / "custom_components/besancon_emoi/manifest.json").read_text(encoding="utf-8"))[
        "version"
    ]


def notes(version: str, journal: str) -> str:
    """Section ``## [version]`` du journal, sans son titre."""
    logger.debug("notes: entrée | version={!r:.80}, journal={!r:.80}", version, journal)
    motif = re.compile(rf"^## \[{re.escape(version)}\].*?$(.*?)(?=^## \[|\Z)", re.MULTILINE | re.DOTALL)
    trouve = motif.search(journal)
    if trouve is None or not trouve.group(1).strip():
        logger.debug("notes: sortie par ValueError")
        raise ValueError(f"version {version} absente de CHANGELOG.md")
    logger.debug("notes: sortie")
    return trouve.group(1).strip() + "\n"


def main(argv: list[str]) -> int:
    logger.debug("main: entrée | argv={!r:.80}", argv)
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        logger.debug("main: sortie | 2")
        return 2
    version = argv[1].removeprefix("v")
    if version != version_manifest():
        logger.error("tag v{} ≠ manifest.json {}", version, version_manifest())
        return 1
    try:
        sys.stdout.write(notes(version, (RACINE / "CHANGELOG.md").read_text(encoding="utf-8")))
    except ValueError as err:
        logger.error("{}", err)
        return 1
    logger.debug("main: sortie | 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
