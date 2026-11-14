"""
Orquestación del pipeline (Pasos A, B y C) + validación anti-alucinación.

    foto ──► visión ──► fusión ──► pre-filtrado BM25 ──► LLM clasificador
                                                  └──► verificación (opcional)

La confianza final NO es la que declara el LLM: se calibra con evidencia
recuperada (posición del código, datos faltantes) para evitar el exceso de
confianza típico de los LLM.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .config import Settings, settings
from .llm import LLMClient, LLMError, build_llm
from .normalize import normalize_code
from .preprocess import CatalogNode, load_catalog
from .prompts import (
    CLASSIFY_SCHEMA,
    CLASSIFY_SYSTEM,
    CLASSIFY_USER,
    FUSION_SCHEMA,
    FUSION_SYSTEM,
    FUSION_USER,
    VERIFY_SCHEMA,
    VERIFY_SYSTEM,
    VISION_SCHEMA,
    VISION_SYSTEM,
    VISION_USER,
)
from .retrieval import BM25Retriever, Candidate

log = logging.getLogger(__name__)


@dataclass
class ProductContext:
    uso_previsto: str | None = None
    origen: str | None = None
    destino: str | None = None
    cantidad_peso: str | None = None

    def to_prompt(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v}

    def to_search_text(self) -> str:
        return " ".join(v for v in asdict(self).values() if v)


@dataclass
class ClassificationResult:
    codigo: str
    descripcion: str
    nivel: str
    av: str | None
    confianza: int
    nivel_confianza: str
    explicacion: str
    regla_interpretacion: str
    alternativas: list[dict[str, Any]] = field(default_factory=list)
    informacion_faltante: list[str] = field(default_factory=list)
    advertencia: str | None = None
    descripcion_fusionada: str = ""
    trazabilidad: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ClassificationService:
    def __init__(self, cfg: Settings = settings, llm: LLMClient | None = None):
        self.cfg = cfg
        nodes, meta = load_catalog(cfg.catalog_path)
        self.nodes = nodes
        self.meta = meta
        self.by_digits = {n.codigo_digitos: n for n in nodes}
        self.retriever = BM25Retriever(nodes)
        self.llm = llm or build_llm(cfg)

    # ------------------------------------------------------------------
    # Paso 0: visión
    # ------------------------------------------------------------------
    def describe_image(self, image_b64: str) -> dict[str, Any]:
        result = self.llm.vision(
            system=VISION_SYSTEM,
            user=VISION_USER,
            schema=VISION_SCHEMA,
            name="vision",
            image_b64=image_b64,
            model=self.cfg.vision_model,
        )
        # Normalización defensiva del contrato.
        result.setdefault("atributos", {})
        result["descripcion"] = (result.get("descripcion") or "").strip()
        if not result["descripcion"]:
            result["es_identificable"] = False
            result["motivo_baja_confianza"] = (
                result.get("motivo_baja_confianza")
                or "La IA no devolvió una descripción utilizable."
            )
        return result

    # ------------------------------------------------------------------
    # Paso A: fusión
    # ------------------------------------------------------------------
    def fuse(
        self,
        descripcion_ia: str,
        descripcion_usuario: str | None,
        contexto: ProductContext,
    ) -> dict[str, Any]:
        user_prompt = FUSION_USER.format(
            descripcion_ia=descripcion_ia.strip() or "(sin descripción de imagen)",
            descripcion_usuario=(descripcion_usuario or "").strip() or "(sin aporte del usuario)",
            contexto_json=json.dumps(contexto.to_prompt(), ensure_ascii=False) or "{}",
        )
        try:
            fusion = self.llm.json(
                system=FUSION_SYSTEM, user=user_prompt, schema=FUSION_SCHEMA,
                name="fusion", model=self.cfg.text_model,
            )
        except LLMError as exc:
            # Degradación elegante: la búsqueda sigue funcionando sin fusión.
            log.warning("Fusión LLM falló, usando fallback: %s", exc)
            fusion = {
                "descripcion_fusionada": " ".join(filter(None, [
                    descripcion_ia.strip(), (descripcion_usuario or "").strip(),
                    contexto.to_search_text(),
                ])),
                "atributos_clave": {"producto": "", "materia": None, "funcion": None,
                                    "estado": None, "presentacion": None,
                                    "es_parte_o_accesorio": False},
                "informacion_faltante": [],
                "conflictos": [],
            }
        fusion["descripcion_fusionada"] = (
            fusion.get("descripcion_fusionada") or descripcion_ia or ""
        ).strip()
        return fusion

    # ------------------------------------------------------------------
    # Pasos B + C
    # ------------------------------------------------------------------
    def classify(
        self,
        descripcion_ia: str,
        descripcion_usuario: str | None = None,
        contexto: ProductContext | None = None,
        *,
        top_k: int | None = None,
        fuse_first: bool = True,
        verify: bool | None = None,
    ) -> ClassificationResult:
        contexto = contexto or ProductContext()
        top_k = top_k or self.cfg.top_k

        fusion = (self.fuse(descripcion_ia, descripcion_usuario, contexto)
                  if fuse_first else {"descripcion_fusionada": descripcion_ia,
                                      "informacion_faltante": []})
        fused = fusion["descripcion_fusionada"]

        # --- Paso B: candidatos -----------------------------------------
        cands = self.retriever.search(
            fused,
            context=contexto.to_search_text(),
            top_k=top_k,
            max_per_heading=self.cfg.max_per_heading,
        )
        if not cands:
            raise ValueError(
                "No se encontraron candidatos. Refina la descripción del producto."
            )

        # --- Paso C: clasificación --------------------------------------
        user_prompt = CLASSIFY_USER.format(
            descripcion_fusionada=fused,
            contexto_json=json.dumps(contexto.to_prompt(), ensure_ascii=False),
            candidatos_json=json.dumps(
                [c.to_prompt_dict() for c in cands], ensure_ascii=False
            ),
        )
        raw = self.llm.json(
            system=CLASSIFY_SYSTEM, user=user_prompt, schema=CLASSIFY_SCHEMA,
            name="classify", model=self.cfg.text_model,
        )

        result = self._validate_and_calibrate(raw, cands, fused)
        result.informacion_faltante = self._dedupe(
            list(result.informacion_faltante) + list(fusion.get("informacion_faltante") or [])
        )
        result.trazabilidad.update({
            "fusion": fusion,
            "candidatos": [
                {"codigo": c.node.codigo, "score": round(c.score_norm, 3),
                 "residual": c.node.is_residual}
                for c in cands
            ],
            "modelos": {"vision": self.cfg.vision_model, "texto": self.cfg.text_model},
            "catalogo": self.meta,
        })

        if verify if verify is not None else self.cfg.verify_enabled:
            self._maybe_verify(result, fused, cands)

        return result

    # ------------------------------------------------------------------
    def _maybe_verify(self, result: ClassificationResult, fused: str,
                      cands: list[Candidate]) -> None:
        """Segunda opinión barata; solo se aplica si el veredicto es claro."""
        allowed = [c.node.codigo for c in cands]
        payload = {
            "ficha": fused,
            "codigo_elegido": result.codigo,
            "alternativas": [a["codigo"] for a in result.alternativas],
            "codigos_permitidos": allowed,
        }
        try:
            verdict = self.llm.json(
                system=VERIFY_SYSTEM,
                user=json.dumps(payload, ensure_ascii=False),
                schema=VERIFY_SCHEMA, name="verify", model=self.cfg.verify_model,
            )
        except LLMError as exc:
            log.warning("Verificación omitida: %s", exc)
            return

        result.trazabilidad["verificacion"] = verdict
        suggested = normalize_code(verdict.get("codigo_sugerido") or "")
        if (verdict.get("veredicto") == "rechazar" and suggested in self.by_digits
                and any(c.node.codigo_digitos == suggested for c in cands)):
            # Cambio de código: se degrada la confianza y se avisa al usuario.
            node = self.by_digits[suggested]
            prev = result.codigo
            result.codigo = node.codigo
            result.descripcion = node.descripcion
            result.nivel = node.nivel
            result.av = node.av
            result.confianza = max(20, result.confianza - 25)
            result.nivel_confianza = self._label(result.confianza)
            result.advertencia = (
                f"El verificador corrigió {prev} -> {node.codigo}: {verdict.get('motivo', '')}"
            )

    # ------------------------------------------------------------------
    def _validate_and_calibrate(
        self, raw: dict[str, Any], cands: list[Candidate], fused: str
    ) -> ClassificationResult:
        """
        El LLM propone; el catálogo dispone. Toda descripción/nivel/av se toma
        del catálogo, nunca del texto generado. Los códigos fuera de la lista
        candidata se descartan (anti-alucinación).
        """
        cand_by_code = {c.node.codigo_digitos: c for c in cands}
        discarded: list[str] = []

        def resolve(code: Any) -> tuple[CatalogNode, Candidate] | None:
            digits = normalize_code(str(code or ""))
            cand = cand_by_code.get(digits)
            if not cand:
                if digits:
                    discarded.append(str(code))
                return None
            return cand.node, cand

        principal = resolve(raw.get("codigo_principal"))
        warning = raw.get("advertencia")
        if principal is None:
            principal = (cands[0].node, cands[0])
            warning = ("El código propuesto por la IA no estaba en la lista; "
                       "se muestra el mejor candidato de búsqueda. Requiere revisión.")

        node, cand = principal
        rank = next(i for i, c in enumerate(cands, 1) if c is cand)
        llm_conf = int(max(0, min(100, raw.get("confianza", 50))))
        missing = list(raw.get("informacion_faltante") or [])

        # --- Calibración de confianza -----------------------------------
        retrieval_conf = max(0.0, 100.0 - 12.0 * (rank - 1))
        completeness = 100.0 - min(60.0, 20.0 * len(missing))
        final = 0.60 * llm_conf + 0.25 * retrieval_conf + 0.15 * completeness
        final = min(final, llm_conf + 25)       # el retrieval no "infla" solo
        # Un residual justificado por nota explicativa NO se penaliza: es la
        # línea correcta (p. ej. perros/gatos en 0106.19.00.00).
        if node.is_residual and not cand.nota:
            specific = [c for c in cands
                        if not c.node.is_residual and c.score >= cand.score * 0.92]
            if specific:
                final = min(final, 74)          # residual con alternativa específica
        final_i = int(round(max(0, min(100, final))))

        # --- Alternativas: solo códigos válidos, sin duplicados ----------
        alts: list[dict[str, Any]] = []
        seen = {node.codigo_digitos}
        for alt in raw.get("alternativas") or []:
            resolved = resolve(alt.get("codigo"))
            if not resolved:
                continue
            a_node, _ = resolved
            if a_node.codigo_digitos in seen:
                continue
            seen.add(a_node.codigo_digitos)
            alts.append({
                "codigo": a_node.codigo,
                "descripcion": a_node.descripcion,
                "nivel": a_node.nivel,
                "av": a_node.av,
                "confianza": int(max(0, min(100, alt.get("confianza", 0)))),
                "motivo": str(alt.get("motivo") or ""),
                "es_residual": a_node.is_residual,
            })
            if len(alts) == 4:
                break

        # Relleno con los siguientes candidatos del retrieval si el LLM
        # devolvió menos de 3 alternativas.
        if len(alts) < 3:
            for c in cands:
                if c.node.codigo_digitos in seen:
                    continue
                seen.add(c.node.codigo_digitos)
                alts.append({
                    "codigo": c.node.codigo,
                    "descripcion": c.node.descripcion,
                    "nivel": c.node.nivel,
                    "av": c.node.av,
                    "confianza": int(round(c.score_norm * llm_conf * 0.8)),
                    "motivo": "Alternativa recuperada por similitud léxica.",
                    "es_residual": c.node.is_residual,
                })
                if len(alts) >= 3:
                    break

        return ClassificationResult(
            codigo=node.codigo,
            descripcion=node.descripcion,
            nivel=node.nivel,
            av=node.av,
            confianza=final_i,
            nivel_confianza=self._label(final_i),
            explicacion=raw.get("explicacion") or "",
            regla_interpretacion=raw.get("regla_interpretacion") or "GRI 1",
            alternativas=alts,
            informacion_faltante=missing,
            advertencia=warning,
            descripcion_fusionada=fused,
            trazabilidad={
                "rank_recuperacion": rank,
                "score_recuperacion": round(cand.score_norm, 3),
                "confianza_llm": llm_conf,
                "confianza_recuperacion": round(retrieval_conf, 1),
                "completitud": round(completeness, 1),
                "codigos_descartados": discarded,
            },
        )

    def _label(self, conf: int) -> str:
        if conf >= self.cfg.confidence_high:
            return "alta"
        if conf >= self.cfg.confidence_medium:
            return "media"
        return "baja"

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen, out = set(), []
        for item in items:
            key = str(item).strip().lower()
            if key and key not in seen:
                seen.add(key)
                out.append(str(item).strip())
        return out

    # ------------------------------------------------------------------
    def get_code(self, codigo: str) -> CatalogNode | None:
        digits = normalize_code(codigo)
        node = self.by_digits.get(digits)
        if node:
            return node
        # Búsqueda por prefijo (p. ej. el usuario escribe 8471.30)
        matches = [n for n in self.nodes if n.codigo_digitos.startswith(digits)]
        return matches[0] if len(matches) == 1 else None

    def search(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        cands = self.retriever.search(query, top_k=top_k)
        return [
            {"codigo": c.node.codigo, "descripcion": c.node.descripcion,
             "nivel": c.node.nivel, "av": c.node.av,
             "score": round(c.score_norm, 3),
             "es_residual": c.node.is_residual}
            for c in cands
        ]
