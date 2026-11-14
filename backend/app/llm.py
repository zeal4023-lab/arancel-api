"""
Cliente LLM con salida JSON estricta.

- OpenAILLM: cualquier endpoint compatible con OpenAI (OpenAI, Groq,
  DeepSeek, Gemini OpenAI-compat, OpenRouter, NVIDIA NIM...).
- CompositeLLM: permite usar un proveedor para VISIÓN y otro para TEXTO.
- MockLLM:   determinista, sin red. Permite desarrollar la app y correr
             pruebas end-to-end antes de tener API key.

Diferencias entre proveedores que se resuelven aquí:
  1. OpenAI y Groq aceptan `response_format: json_schema` (strict).
     DeepSeek (chat/completions), Gemini-compat y la mayoría de gateways
     solo aceptan `json_object`: en ese modo se inyecta el esquema en el
     system prompt y se valida/parsea en cliente.
  2. Los esquemas con `type: ["string","null"]` se convierten a `anyOf`
     y se eliminan keywords no soportadas en modo strict.
  3. Algunos proveedores rechazan `detail` en `image_url`.

Todo el sistema depende solo de `LLMClient.json(...)`, así que cambiar de
proveedor es cuestión de variables de entorno, no de código.
"""
from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from typing import Any

from .config import Settings, settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMClient(ABC):
    provider_label: str = "desconocido"

    @abstractmethod
    def json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        name: str,
        image_b64: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        ...

    def vision(self, *, system: str, user: str, schema: dict, name: str,
               image_b64: str, model: str | None = None) -> dict[str, Any]:
        return self.json(system=system, user=user, schema=schema, name=name,
                         image_b64=image_b64, model=model)


# ---------------------------------------------------------------------------
# Utilidades de compatibilidad
# ---------------------------------------------------------------------------

# Keywords que los modos "strict" de OpenAI/Groq no admiten.
_UNSUPPORTED_STRICT_KEYS = {"minimum", "maximum", "minItems", "maxItems",
                            "minLength", "maxLength", "pattern", "format",
                            "default", "exclusiveMinimum", "exclusiveMaximum"}


def _strict_schema(node: Any) -> Any:
    """Adapta un JSON Schema al subconjunto de 'structured outputs'."""
    if isinstance(node, list):
        return [_strict_schema(x) for x in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _UNSUPPORTED_STRICT_KEYS:
            continue
        if key == "type" and isinstance(value, list):
            # ["string", "null"] -> anyOf (lo que exigen OpenAI/Groq)
            out["anyOf"] = [{"type": t} for t in value]
            continue
        out[key] = _strict_schema(value)

    # En strict todos los campos son obligatorios y no se permiten extras.
    if out.get("type") == "object" and "properties" in out:
        out["required"] = list(out["properties"].keys())
        out["additionalProperties"] = False
    return out


def _extract_json(raw: str) -> dict[str, Any]:
    """Parsea JSON tolerando ```json ... ```, texto alrededor y saltos."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise LLMError(f"La respuesta no contiene JSON válido: {text[:200]!r}")


def _resolve_json_mode(base_url: str | None, configured: str) -> str:
    """auto | schema | object."""
    if configured in {"schema", "object"}:
        return configured
    url = (base_url or "").lower()
    # OpenAI y Groq implementan json_schema strict. El resto (DeepSeek,
    # Gemini-compat, OpenRouter, NVIDIA, DashScope...) suele limitarse a
    # json_object. Se puede forzar con LLM_JSON_MODE.
    if not url or "api.openai.com" in url or "api.groq.com" in url:
        return "schema"
    return "object"


def _is_openai_endpoint(base_url: str | None) -> bool:
    return not base_url or "api.openai.com" in base_url.lower()


# ---------------------------------------------------------------------------
# Proveedor OpenAI-compatible
# ---------------------------------------------------------------------------

class OpenAILLM(LLMClient):
    def __init__(
        self,
        cfg: Settings = settings,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        json_mode: str | None = None,
    ):
        try:
            from openai import OpenAI  # import perezoso
        except ImportError as exc:  # pragma: no cover
            raise LLMError("Falta el paquete 'openai' (pip install openai)") from exc

        self.cfg = cfg
        self.api_key = api_key or cfg.openai_api_key
        self.base_url = base_url if base_url is not None else cfg.openai_base_url
        if not self.api_key:
            raise LLMError("API key no configurada")
        self.json_mode = json_mode or _resolve_json_mode(
            self.base_url, cfg.llm_json_mode
        )
        self.provider_label = f"openai-compatible ({self.base_url or 'api.openai.com'}, {self.json_mode})"
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=cfg.llm_timeout_s,
        )

    def json(self, *, system: str, user: str, schema: dict, name: str,
             image_b64: str | None = None, model: str | None = None,
             temperature: float = 0.0) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": user}]
        if image_b64:
            image: dict[str, Any] = {
                "url": f"data:image/jpeg;base64,{image_b64}",
            }
            # Solo OpenAI admite 'detail'; otros proveedores pueden rechazarlo.
            if _is_openai_endpoint(self.base_url):
                image["detail"] = "high"
            content.append({"type": "image_url", "image_url": image})

        if self.json_mode == "schema":
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": {
                    "name": name,
                    "strict": True,
                    "schema": _strict_schema(schema),
                },
            }
            final_system = system
        else:
            # DeepSeek exige la palabra "json" en el prompt y un ejemplo;
            # le pasamos el esquema completo como guía.
            response_format = {"type": "json_object"}
            final_system = (
                f"{system}\n\n"
                "Responde ÚNICAMENTE con un objeto JSON válido, sin markdown, "
                "sin comentarios y sin texto adicional. El JSON debe cumplir "
                "exactamente este JSON Schema:\n"
                f"{json.dumps(schema, ensure_ascii=False)}"
            )

        extra: dict[str, Any] = {}
        if self.cfg.llm_max_output_tokens:
            extra["max_tokens"] = self.cfg.llm_max_output_tokens

        last_exc: Exception | None = None
        for attempt in range(self.cfg.llm_max_retries + 1):
            try:
                resp = self.client.chat.completions.create(
                    model=model or self.cfg.text_model,
                    messages=[
                        {"role": "system", "content": final_system},
                        {"role": "user", "content": content},
                    ],
                    temperature=temperature,
                    response_format=response_format,
                    **extra,
                )
                raw = resp.choices[0].message.content or ""
                return _extract_json(raw)
            except Exception as exc:  # red, rate limit, JSON inválido...
                last_exc = exc
                log.warning("LLM intento %s falló: %s", attempt + 1, exc)
                time.sleep(min(2 ** attempt * 0.6, 4))
        raise LLMError(f"LLM falló tras reintentos: {last_exc}")


class CompositeLLM(LLMClient):
    """Un cliente para visión y otro para texto (p. ej. Groq gratis + DeepSeek)."""

    def __init__(self, text: LLMClient, vision: LLMClient):
        self.text = text
        self.vision_client = vision
        self.provider_label = f"texto={text.provider_label} | vision={vision.provider_label}"

    def json(self, **kwargs) -> dict[str, Any]:
        return self.text.json(**kwargs)

    def vision(self, *, system: str, user: str, schema: dict, name: str,
               image_b64: str, model: str | None = None) -> dict[str, Any]:
        return self.vision_client.json(
            system=system, user=user, schema=schema, name=name,
            image_b64=image_b64, model=model,
        )


# ---------------------------------------------------------------------------
# Proveedor simulado (desarrollo)
# ---------------------------------------------------------------------------

class MockLLM(LLMClient):
    """Proveedor simulado: útil para tests y para demos sin API key."""

    provider_label = "MockLLM (simulado)"

    def json(self, *, system: str, user: str, schema: dict, name: str,
             image_b64: str | None = None, model: str | None = None,
             temperature: float = 0.0) -> dict[str, Any]:
        if name == "vision":
            return {
                "es_identificable": True,
                "objeto": "producto de prueba",
                "categoria": "no determinada",
                "descripcion": "Producto genérico identificado en la imagen de prueba.",
                "atributos": {
                    "materia_constitutiva": None,
                    "funcion_principal": None,
                    "estado": "nuevo",
                    "presentacion": None,
                    "partes_incluidas": [],
                    "texto_visible": None,
                },
                "nivel_confianza": 0.5,
                "motivo_baja_confianza": "Proveedor simulado (MockLLM).",
            }
        if name == "fusion":
            ia = re.search(r"<descripcion_ia>(.*?)</descripcion_ia>", user, re.S)
            us = re.search(r"<descripcion_usuario>(.*?)</descripcion_usuario>", user, re.S)
            texto = " ".join(x.group(1).strip() for x in (ia, us)
                             if x and x.group(1).strip())
            return {
                "descripcion_fusionada": texto or "Producto sin descripción.",
                "atributos_clave": {
                    "producto": "producto",
                    "materia": None, "funcion": None, "estado": None,
                    "presentacion": None, "es_parte_o_accesorio": False,
                },
                "informacion_faltante": ["Proveedor simulado."],
                "conflictos": [],
            }
        if name == "classify":
            m = re.search(r"<candidatos>(.*?)</candidatos>", user, re.S)
            cands = json.loads(m.group(1)) if m else []
            if not cands:
                raise LLMError("Sin candidatos para el mock")
            top = cands[0]["codigo"]
            return {
                "codigo_principal": top,
                "confianza": 55,
                "nivel_confianza": "baja",
                "alternativas": [
                    {"codigo": c["codigo"], "confianza": max(5, 50 - i * 10),
                     "motivo": "Similitud léxica (mock)."}
                    for i, c in enumerate(cands[1:4], 1)
                ],
                "explicacion": "Resultado simulado: coincide con el candidato "
                               "mejor puntuado por la búsqueda.",
                "regla_interpretacion": "GRI 1",
                "informacion_faltante": ["Proveedor simulado."],
                "advertencia": "Modo simulado: configure OPENAI_API_KEY.",
            }
        if name == "verify":
            return {"veredicto": "revisar",
                    "codigo_sugerido": None,
                    "motivo": "Proveedor simulado.",
                    "datos_faltantes": ["Proveedor simulado."]}
        raise LLMError(f"Mock sin respuesta para {name}")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_llm(cfg: Settings = settings) -> LLMClient:
    if not cfg.openai_api_key:
        if cfg.allow_mock:
            log.warning("Sin API key: usando MockLLM (solo desarrollo).")
            return MockLLM()
        raise LLMError("API key no configurada y ALLOW_MOCK=false")

    text = OpenAILLM(cfg)

    # Overrides opcionales: usar un proveedor distinto solo para las fotos.
    if cfg.vision_api_key and (
        cfg.vision_api_key != cfg.openai_api_key
        or (cfg.vision_base_url or None) != (cfg.openai_base_url or None)
    ):
        vision = OpenAILLM(
            cfg,
            api_key=cfg.vision_api_key,
            base_url=cfg.vision_base_url if cfg.vision_base_url is not None else cfg.openai_base_url,
        )
        return CompositeLLM(text=text, vision=vision)

    return text
