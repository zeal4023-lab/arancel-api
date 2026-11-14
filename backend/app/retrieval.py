"""
Paso B del pipeline: pre-filtrado léxico (BM25 + señales de campo).

Se indexan solo las 8.003 hojas clasificables. Para ~8k documentos un BM25
implementado en Python responde en decenas de milisegundos, sin depender de
Elasticsearch. En la app móvil el mismo ranking se replica con SQLite FTS5
(ver mobile/src/lib/db.ts y docs/ARQUITECTURA.md).
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable

from .equivalencias import indices_por_codigo
from .normalize import expand_query, is_residual, normalize, normalize_code, tokenize
from .preprocess import CatalogNode, LEVEL_ORDER


@dataclass
class Candidate:
    node: CatalogNode
    score: float
    bm25: float
    keyword_hits: int
    exact_phrase: bool
    score_norm: float = 0.0
    nota: str = ""

    def to_prompt_dict(self) -> dict:
        data = {
            "codigo": self.node.codigo,
            "nivel": self.node.nivel,
            "descripcion": self.node.descripcion,
            "av": self.node.av,
            "es_residual": self.node.is_residual,
            "confianza_busqueda": round(self.score_norm, 3),
        }
        if self.nota:
            data["nota"] = self.nota
        return data


class BM25Retriever:
    def __init__(self, nodes: list[CatalogNode], k1: float = 1.3, b: float = 0.72):
        self.nodes = nodes
        self.k1, self.b = k1, b
        self.postings: dict[str, list[tuple[int, float]]] = defaultdict(list)
        self.doc_len = [0.0] * len(nodes)
        self.kw_sets: list[frozenset[str]] = []
        self.heading: list[str] = []
        self.notas: dict[int, str] = {}
        equivalencias = indices_por_codigo()

        df: Counter[str] = Counter()
        for i, node in enumerate(nodes):
            # 1. Recalcular el marcado de residual: el catalogo generado puede
            #    tener falsos positivos heredados del titulo de la partida.
            node.is_residual = is_residual(node.descripcion)

            # 2. Enriquecer con terminos de notas explicativas (perro/gato...).
            #    Se inyecta en el indice en tiempo de carga: catalog.json no
            #    se toca y descripcion/nivel/av siguen siendo los oficiales.
            extra = equivalencias.get(node.codigo_digitos)
            if extra:
                terminos, nota = extra
                node.tokens = {
                    **node.tokens,
                    **{t: max(node.tokens.get(t, 0.0), 3.0) for t in terminos},
                }
                node.palabras_clave = list(dict.fromkeys(
                    [*node.palabras_clave, *terminos]
                ))
                self.notas[i] = nota

            self.doc_len[i] = sum(node.tokens.values()) or 1.0
            for term, tf in node.tokens.items():
                self.postings[term].append((i, tf))
                df[term] += 1
            self.kw_sets.append(frozenset(tokenize(" ".join(node.palabras_clave))))
            self.heading.append(node.codigo_digitos[:4])
        self.n = len(nodes)
        self.avgdl = sum(self.doc_len) / max(self.n, 1)
        self.idf: dict[str, float] = {
            term: math.log(1 + (self.n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }
        self._filtered_kw = [
            frozenset(t for t in kws if len(t) >= 4) for kws in self.kw_sets
        ]
        # Términos de la descripción oficial: coincidir aquí vale más que
        # coincidir en una keyword suelta.
        self.desc_terms = [frozenset(tokenize(n.descripcion)) for n in nodes]
        # Vocabulario para tolerancia a errores de tipeo/OCR.
        self.vocab = [t for t in self.postings if not t.startswith("c")]

    # ------------------------------------------------------------------ #
    @staticmethod
    def _levenshtein(a: str, b: str, limit: int) -> int:
        """Distancia de edición con corte temprano (limit+1 si se excede)."""
        if abs(len(a) - len(b)) > limit:
            return limit + 1
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, 1):
            cur = [i]
            best = cur[0]
            for j, cb in enumerate(b, 1):
                cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                               prev[j - 1] + (ca != cb)))
                best = min(best, cur[j])
            if best > limit:
                return limit + 1
            prev = cur
        return prev[-1]

    def _fuzzy_expand(self, q_weights: dict[str, float]) -> dict[str, float]:
        """
        Si un término de la consulta no existe en el índice, busca variantes
        cercanas ('refrijeradora', 'zapatillla', errores de OCR). Peso 0.5.
        """
        extra: dict[str, float] = {}
        for term, weight in q_weights.items():
            if term in self.postings:
                continue
            limit = 1 if len(term) < 8 else 2
            best: list[tuple[int, int, str]] = []
            for cand in self.vocab:
                if cand[0] != term[0] or abs(len(cand) - len(term)) > limit:
                    continue
                dist = self._levenshtein(term, cand, limit)
                if dist <= limit:
                    best.append((dist, -len(self.postings[cand]), cand))
            for dist, _, cand in sorted(best)[:3]:
                extra[cand] = max(extra.get(cand, 0.0), 0.5 * weight / (1 + dist))
        if extra:
            q_weights = {**q_weights, **extra}
        return q_weights

    # ------------------------------------------------------------------ #
    def _bm25(self, q_weights: dict[str, float]) -> dict[int, float]:
        scores: dict[int, float] = defaultdict(float)
        for term, qw in q_weights.items():
            posting = self.postings.get(term)
            if not posting:
                continue
            idf = self.idf[term]
            for doc_i, tf in posting:
                denom = tf + self.k1 * (1 - self.b + self.b * self.doc_len[doc_i] / self.avgdl)
                scores[doc_i] += qw * idf * (tf * (self.k1 + 1)) / denom
        return scores

    def search(
        self,
        query: str,
        *,
        top_k: int = 40,
        pool: int = 400,
        context: str = "",
        max_per_heading: int = 6,
        diversify: bool = True,
    ) -> list[Candidate]:
        full_query = " ".join([query, context]).strip()
        q_norm = normalize(full_query)
        if not q_norm:
            return []

        # Atajo: el usuario escribió/pegó un código arancelario.
        digits = normalize_code(full_query)
        looks_like_code = re.fullmatch(r"[\d.,\s]+", full_query) is not None
        if looks_like_code and len(digits) >= 6:
            hits = [n for n in self.nodes if n.codigo_digitos.startswith(digits)]
            if hits:
                return [Candidate(n, 100.0, 100.0, 5, True, 1.0)
                        for n in sorted(hits, key=lambda x: x.specificity)[:top_k]]

        q_tokens = tokenize(full_query)
        if not q_tokens:
            return []
        q_weights = expand_query(q_tokens)

        # Heurística de núcleo: en español el sustantivo principal suele ir
        # primero ("camiseta de poliéster", "detergente en polvo"). Sin esto,
        # los modificadores con IDF alto (poliéster, ropa) ganan al producto.
        for pos, tok in enumerate(q_tokens[:2]):
            if tok in q_weights:
                q_weights[tok] *= 1.7 if pos == 0 else 1.25

        q_weights = self._fuzzy_expand(q_weights)
        q_terms = set(q_tokens)

        scores = self._bm25(q_weights)
        if not scores:
            return []

        # --- Señales adicionales -----------------------------------------
        phrase_bonus = 0.0
        if 8 <= len(q_norm) <= 120:
            phrase_bonus = 3.5  # la consulta completa aparece en el doc

        cands: list[Candidate] = []
        for doc_i, base in scores.items():
            node = self.nodes[doc_i]
            kw_hits = len(q_terms & self._filtered_kw[doc_i])
            desc_overlap = sum(w for term, w in q_weights.items()
                               if term in self.desc_terms[doc_i])
            exact = phrase_bonus if q_norm in node.search_text else 0.0

            score = base + 0.55 * kw_hits + 0.85 * desc_overlap + exact
            if node.is_residual:
                score *= 0.90          # desempate: no ganar por "los demás"
            # los nodos más profundos son más específicos y preferibles
            score *= 1.0 + 0.012 * (node.specificity - 6)

            cands.append(Candidate(node, score, base, kw_hits, bool(exact),
                                   nota=self.notas.get(doc_i, "")))

        cands.sort(key=lambda c: c.score, reverse=True)
        cands = cands[:pool]

        # --- Diversificación por partida (4 dígitos) ---------------------
        if diversify:
            per_heading: Counter[str] = Counter()
            picked: list[Candidate] = []
            leftover: list[Candidate] = []
            for cand in cands:
                h = cand.node.codigo_digitos[:4]
                if per_heading[h] < max_per_heading:
                    per_heading[h] += 1
                    picked.append(cand)
                else:
                    leftover.append(cand)
                if len(picked) >= top_k:
                    break
            if len(picked) < top_k:
                picked.extend(leftover[: top_k - len(picked)])
            cands = picked
        else:
            cands = cands[:top_k]

        # --- Normalización de score para UI/confianza --------------------
        top = cands[0].score if cands else 1.0
        for cand in cands:
            cand.score_norm = cand.score / top if top else 0.0
        return cands

    def explain(self, query: str, code: str) -> dict:
        """Depuración: por qué un código dado (no) aparece en el top-k."""
        cands = self.search(query, top_k=50, diversify=False)
        for rank, cand in enumerate(cands, 1):
            if cand.node.codigo_digitos == normalize_code(code):
                return {"rank": rank, "score": round(cand.score, 3),
                        "bm25": round(cand.bm25, 3),
                        "keyword_hits": cand.keyword_hits,
                        "exact_phrase": cand.exact_phrase}
        return {"rank": None, "reason": "fuera del pool de 50"}
