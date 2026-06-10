"""Extraction one-shot des constantes démo de frontend/rouge.html vers api/demo/*.json.

Une seule source de vérité : la section « DONNÉES DÉMO » du front est
évaluée telle quelle par node (seed mulberry32 déterministe, donc sorties
stables), puis sérialisée en un JSON par endpoint. Aucune copie manuelle
des constantes en Python — pas de drift silencieux.

Usage : ./venv/bin/python scripts/extract_demo.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONT = REPO_ROOT / "frontend" / "rouge.html"
OUT_DIR = REPO_ROOT / "api" / "demo"

# Borne la section évaluée : des utilitaires (mulberry32, R…) jusqu'à
# l'horloge — uniquement des constantes, aucun accès DOM.
START_MARK = "================= UTILS"
END_MARK = "================= HORLOGE"

HARNESS = """
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const out = new Function(src + `;
  return {
    layers: {news: NEWSPTS, pm: PMARKETS, ais: AIS, mil: MILPTS, choke: CHOKE, zones: ZONES},
    cot: COT,
    macro: {events: MACRO, scores: SCORE},
    trend: TREND,
    fx: {strength: FXS, pairs: PAIRS},
    markets: MKT,
    pm: PMARKETS,
  };
`)();
process.stdout.write(JSON.stringify(out));
"""


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("erreur : node introuvable (requis pour évaluer la section JS du front)", file=sys.stderr)
        return 1

    lines = FRONT.read_text(encoding="utf-8").splitlines()
    try:
        a = next(i for i, l in enumerate(lines) if START_MARK in l)
        b = next(i for i, l in enumerate(lines) if END_MARK in l)
    except StopIteration:
        print(f"erreur : marqueurs '{START_MARK}' / '{END_MARK}' introuvables dans {FRONT}", file=sys.stderr)
        return 1
    if b <= a:
        print("erreur : marqueurs dans le mauvais ordre", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "demo_section.js"
        src.write_text("\n".join(lines[a:b]), encoding="utf-8")
        harness = Path(tmp) / "harness.js"
        harness.write_text(HARNESS, encoding="utf-8")
        proc = subprocess.run(
            [node, str(harness), str(src)], capture_output=True, text=True, timeout=60
        )
    if proc.returncode != 0:
        print(f"erreur node :\n{proc.stderr}", file=sys.stderr)
        return 1

    datasets = json.loads(proc.stdout)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, payload in datasets.items():
        path = OUT_DIR / f"{name}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        size = (
            len(payload) if isinstance(payload, list)
            else "+".join(f"{k}:{len(v)}" for k, v in payload.items())
        )
        print(f"  api/demo/{name}.json  ({size})")
    print(f"OK — {len(datasets)} fixtures extraites de {FRONT.name} (lignes {a + 1}-{b})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
