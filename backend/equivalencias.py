"""
Equivalencias de clasificación que NO aparecen literalmente en el arancel.

El JSON oficial solo trae descripciones. Muchos productos se clasifican por
NOTA EXPLICATIVA en una línea "Los demás" cuyo texto no nombra el producto.
BM25 no puede recuperar esos códigos con el nombre comercial, porque el índice
no contiene esa palabra. Este módulo inyecta esos términos en el índice en
tiempo de carga (no modifica catalog.json) y adjunta la nota al candidato
para que el LLM sepa por qué aparece ahí.

Mantener este archivo es la forma correcta de ampliar el recall sin tocar el
catálogo oficial: descripción, nivel y av SIEMPRE se copian del catálogo.

Formato: código -> {"terminos": [...], "nota": "..."}
"""
from __future__ import annotations

from .normalize import normalize_code, tokenize

EQUIVALENCIAS: dict[str, dict] = {
    "0106.19.00.00": {
        "terminos": (
            "perro", "perros", "canino", "caninos", "canes", "can",
            "cachorro", "cachorros", "perra", "perras",
            "gato", "gatos", "felino", "felinos", "gata", "gatas",
            "minino", "mascota", "mascotas",
        ),
        "nota": (
            "Nota explicativa de la partida 01.06: los perros y gatos "
            "domesticos vivos se clasifican en 'Los demas' de Mamiferos "
            "(0106.19.00.00), aunque el texto oficial de la linea no los nombra. "
            "Es la linea correcta, no un residual elegido por comodidad."
        ),
    },
}


def indices_por_codigo() -> dict[str, tuple[list[str], str]]:
    """codigo_digitos -> (tokens stemmeados, nota de clasificacion)."""
    out: dict[str, tuple[list[str], str]] = {}
    for codigo, cfg in EQUIVALENCIAS.items():
        tokens: list[str] = []
        for termino in cfg["terminos"]:
            for tok in tokenize(termino):
                if tok not in tokens:
                    tokens.append(tok)
        out[normalize_code(codigo)] = (tokens, cfg["nota"])
    return out
