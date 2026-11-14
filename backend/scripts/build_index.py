"""
Construye los artefactos de búsqueda:
  data/catalog.json   -> catálogo procesado (hojas + tokens + metadatos)
  data/arancel.db     -> SQLite con FTS5 (para la app móvil y para el backend)

Uso:
  python -m scripts.build_index --input ../../Downloads/arancel_aduanas_2022.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Permite ejecutar como script desde backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.preprocess import build_catalog, load_raw, save_catalog  # noqa: E402


SCHEMA = """
PRAGMA journal_mode=WAL;
DROP TABLE IF EXISTS codes;
CREATE TABLE codes (
    rowid      INTEGER PRIMARY KEY,
    codigo     TEXT NOT NULL UNIQUE,
    digits     TEXT NOT NULL,
    nivel      TEXT NOT NULL,
    descripcion TEXT NOT NULL,
    av         TEXT,
    is_residual INTEGER NOT NULL DEFAULT 0,
    specificity INTEGER NOT NULL DEFAULT 0,
    path       TEXT NOT NULL DEFAULT '',
    search_text TEXT NOT NULL
);
CREATE INDEX idx_codes_digits ON codes(digits);
CREATE INDEX idx_codes_heading ON codes(substr(digits,1,4));

-- Índice de texto completo. unicode61 con remove_diacritics=2 hace que
-- "cafe" encuentre "café" sin preprocesar en el cliente.
CREATE VIRTUAL TABLE codes_fts USING fts5(
    search_text,
    codigo UNINDEXED,
    content='codes',
    content_rowid='rowid',
    tokenize="unicode61 remove_diacritics 2"
);
"""


def build_sqlite(nodes, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    con = sqlite3.connect(out_path)
    con.executescript(SCHEMA)
    rows = []
    for i, n in enumerate(nodes, 1):
        rows.append((
            i, n.codigo, n.codigo_digitos, n.nivel, n.descripcion, n.av,
            1 if n.is_residual else 0, n.specificity,
            " > ".join(f"{p['codigo']} {p['descripcion']}" for p in n.path),
            n.search_text,
        ))
    con.executemany(
        "INSERT INTO codes (rowid,codigo,digits,nivel,descripcion,av,is_residual,specificity,path,search_text)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.execute("INSERT INTO codes_fts(codes_fts) VALUES('rebuild')")
    con.execute("INSERT INTO codes_fts(codes_fts) VALUES('optimize')")
    con.commit()
    con.execute("VACUUM")
    con.close()
    print(f"OK -> {out_path} ({out_path.stat().st_size/1024/1024:.1f} MB)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parents[1] / "data"))
    args = ap.parse_args()

    raw = load_raw(args.input)
    nodes, meta = build_catalog(raw)
    out_dir = Path(args.out_dir)
    save_catalog(nodes, meta, out_dir / "catalog.json")
    build_sqlite(nodes, out_dir / "arancel.db")
    print(json.dumps(meta, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
