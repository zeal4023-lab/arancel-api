"""
Construye el catálogo de búsqueda a partir del JSON oficial.

Decisiones de diseño (validadas contra arancel_aduanas_2022.json):
  * El JSON tiene 10.377 nodos y 4 niveles, pero la jerarquía está INCOMPLETA:
    solo 1.271 subpartidas de 6/8 dígitos están presentes para 7.804 líneas
    nacionales. Por eso los ancestros se resuelven por prefijo de dígitos y
    "saltando" niveles ausentes, no asumiendo que el padre inmediato existe.
  * Solo los nodos HOJA son clasificables (8.003). Los 2.374 nodos internos
    (capítulos, partidas, subpartidas con hijos) no se indexan como respuesta;
    su texto se pliega en el campo de búsqueda del hijo como contexto.
  * 3.344 de las 7.804 líneas nacionales son "Los demás" (residuales). Se
    marcan con is_residual para desempatar en el ranking y avisar al LLM.
  * `av` (ad valorem) solo existe en las 7.804 líneas nacionales.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

from .normalize import (
    GENERIC_TERMS,
    is_residual,
    normalize,
    normalize_code,
    stem,
    tokenize,
)

LEVEL_BY_DIGITS = {2: "capitulo", 4: "partida", 6: "subpartida",
                   8: "subpartida", 10: "subpartida_nacional"}
LEVEL_ORDER = {"capitulo": 1, "partida": 2, "subpartida": 3,
               "subpartida_nacional": 4}

# Pesos de campo para BM25 (se convierten en frecuencia ponderada).
W_DESC = 1.0
W_KEYWORDS = 1.6
W_CONTEXT = 0.5
# Techos de frecuencia: evita que una keyword repetida o una descripción
# larguísima dominen el score (los documentos con 60 keywords sesgaban).
CAP_DESC = 2
CAP_KEYWORDS = 1
CAP_CONTEXT = 1


@dataclass
class CatalogNode:
    codigo: str
    codigo_digitos: str
    nivel: str
    descripcion: str
    av: str | None
    palabras_clave: list[str]
    is_leaf: bool
    is_residual: bool
    parent: str | None
    path: list[dict[str, str]]
    search_text: str
    tokens: dict[str, float] = field(default_factory=dict)
    # profundidad efectiva (dígitos) para desempatar especificidad
    specificity: int = 0

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def load_raw(path: str | Path) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("El JSON raíz debe ser un array de objetos")
    return data


def _clean_keywords(keywords: Iterable[str] | None) -> list[str]:
    """Normaliza, deduplica y descarta keywords basura."""
    seen: set[str] = set()
    out: list[str] = []
    for kw in keywords or []:
        if not isinstance(kw, str):
            continue
        norm = normalize(kw)
        if len(norm) < 3 or norm in seen:
            continue
        # keywords que son puro boilerplate no aportan
        if all(t in GENERIC_TERMS or len(t) < 3 for t in norm.split()):
            continue
        seen.add(norm)
        out.append(norm)
    return out


def build_catalog(raw: list[dict[str, Any]]) -> tuple[list[CatalogNode], dict[str, Any]]:
    """Devuelve (nodos_hoja_indexables, metadatos_de_construcción)."""
    by_digits: dict[str, dict[str, Any]] = {}
    for item in raw:
        digits = normalize_code(item["codigo"])
        if not digits:
            raise ValueError(f"Código inválido: {item.get('codigo')!r}")
        if digits in by_digits:
            raise ValueError(f"Código duplicado: {item['codigo']}")
        by_digits[digits] = item

    # --- 1. Detección de hojas por prefijo de dígitos -----------------------
    parents: set[str] = set()
    for digits in by_digits:
        for cut in (2, 4, 6, 8):
            if len(digits) > cut and digits[:cut] in by_digits:
                parents.add(digits[:cut])

    leaves = [d for d in by_digits if d not in parents]
    orphans_national = 0
    nodes: list[CatalogNode] = []

    for digits in leaves:
        item = by_digits[digits]
        codigo = item["codigo"]
        nivel = LEVEL_BY_DIGITS.get(len(digits), item.get("nivel") or "subpartida")

        # --- 2. Resolución de ancestros por prefijo, tolerando huecos ------
        path: list[dict[str, str]] = []
        for cut in (2, 4, 6, 8):
            if cut >= len(digits):
                break
            anc = by_digits.get(digits[:cut])
            if anc:
                path.append({"codigo": anc["codigo"], "descripcion": anc["descripcion"]})
        if len(digits) == 10 and not any(
            len(normalize_code(p["codigo"])) == 6 for p in path
        ):
            orphans_national += 1

        parent = path[-1]["codigo"] if path else None

        keywords = _clean_keywords(item.get("palabras_clave"))
        descripcion = str(item.get("descripcion") or "").strip()
        search_text = normalize(
            " ".join([descripcion, " ".join(keywords),
                      " ".join(p["descripcion"] for p in path)])
        )

        # --- 3. Bolsa de términos ponderada ---------------------------------
        # Se concatenan listas y se ponderan multiplicando la frecuencia;
        # BM25 usa tf, así que el peso del campo es un multiplicador de tf.
        tokens: dict[str, float] = {}
        for source, mult, cap in (
            (tokenize(descripcion), W_DESC, CAP_DESC),
            (tokenize(" ".join(keywords)), W_KEYWORDS, CAP_KEYWORDS),
            (tokenize(" ".join(p["descripcion"] for p in path)), W_CONTEXT, CAP_CONTEXT),
        ):
            for tok, count in Counter(source).items():
                tokens[tok] = tokens.get(tok, 0.0) + min(count, cap) * mult

        # El código en sí es buscable (el usuario puede pegar un código).
        for group in re.findall(r"\d{2,}", digits):
            tokens[f"c{group}"] = 2.0

        nodes.append(
            CatalogNode(
                codigo=codigo,
                codigo_digitos=digits,
                nivel=nivel,
                descripcion=descripcion,
                av=item.get("av"),
                palabras_clave=keywords,
                is_leaf=True,
                is_residual=is_residual(descripcion),
                parent=parent,
                path=path,
                search_text=search_text,
                tokens=tokens,
                specificity=len(digits),
            )
        )

    nodes.sort(key=lambda n: n.codigo_digitos)
    meta = {
        "total_raw": len(raw),
        "total_leaves": len(nodes),
        "total_internal_nodes": len(parents),
        "residual_leaves": sum(n.is_residual for n in nodes),
        "national_orphans": orphans_national,
        "with_av": sum(n.av is not None for n in nodes),
    }
    return nodes, meta


def save_catalog(nodes: list[CatalogNode], meta: dict[str, Any],
                 out_path: str | Path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "nodes": [n.to_json() for n in nodes]}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"OK -> {out_path}  ({size_mb:.1f} MB, {len(nodes)} hojas)")


def load_catalog(path: str | Path) -> tuple[list[CatalogNode], dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    nodes = [CatalogNode(**n) for n in payload["nodes"]]
    return nodes, payload.get("meta", {})
