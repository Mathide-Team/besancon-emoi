"""Notes d'une version, tirées de CHANGELOG.md, et contrôle tag = version du manifest (issue #10).

Usage : python scripts/notes_de_version.py v0.1.0 > notes.md
Code de sortie 1 si le tag ne correspond pas à manifest.json ou si la version manque au journal.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]


def version_manifest() -> str:
    return json.loads((RACINE / "custom_components/besancon_emoi/manifest.json").read_text(encoding="utf-8"))[
        "version"
    ]


def notes(version: str, journal: str) -> str:
    """Section ``## [version]`` du journal, sans son titre."""
    motif = re.compile(rf"^## \[{re.escape(version)}\].*?$(.*?)(?=^## \[|\Z)", re.MULTILINE | re.DOTALL)
    trouve = motif.search(journal)
    if trouve is None or not trouve.group(1).strip():
        raise ValueError(f"version {version} absente de CHANGELOG.md")
    return trouve.group(1).strip() + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    version = argv[1].removeprefix("v")
    if version != version_manifest():
        print(f"tag v{version} ≠ manifest.json {version_manifest()}", file=sys.stderr)
        return 1
    try:
        sys.stdout.write(notes(version, (RACINE / "CHANGELOG.md").read_text(encoding="utf-8")))
    except ValueError as err:
        print(err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
