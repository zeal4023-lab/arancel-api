"""Contratos HTTP (Pydantic v2)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class ContextoIn(BaseModel):
    uso_previsto: str | None = Field(None, examples=["Consumo humano"])
    origen: str | None = Field(None, examples=["Perú"])
    destino: str | None = Field(None, examples=["China"])
    cantidad_peso: str | None = Field(None, examples=["1000 kg"])


class ClassifyIn(BaseModel):
    descripcion_ia: str = Field("", max_length=2000)
    descripcion_usuario: str | None = Field(None, max_length=2000)
    contexto: ContextoIn = Field(default_factory=ContextoIn)
    top_k: int | None = Field(None, ge=10, le=60)
    verificar: bool | None = None

    @model_validator(mode="after")
    def al_menos_una_descripcion(self):
        if not (self.descripcion_ia or "").strip() and not (self.descripcion_usuario or "").strip():
            raise ValueError("Se requiere descripcion_ia o descripcion_usuario")
        return self


class CodeOut(BaseModel):
    codigo: str
    descripcion: str
    nivel: str
    av: str | None = None


class AlternativeOut(CodeOut):
    confianza: int
    motivo: str = ""
    es_residual: bool = False


class ClassifyOut(BaseModel):
    resultado: CodeOut
    confianza: int
    nivel_confianza: str
    explicacion: str
    regla_interpretacion: str
    alternativas: list[AlternativeOut]
    informacion_faltante: list[str]
    advertencia: str | None = None
    descripcion_fusionada: str
    trazabilidad: dict[str, Any]


class VisionOut(BaseModel):
    es_identificable: bool
    objeto: str
    categoria: str
    descripcion: str
    atributos: dict[str, Any]
    # Aditivo (VISION_ATRIBUTOS_V2): atributos pensados para buscar precios.
    # Las APK antiguas lo ignoran.
    atributos_precio: dict[str, Any] | None = None
    nivel_confianza: float
    motivo_baja_confianza: str | None = None


class SearchOut(BaseModel):
    candidatos: list[dict[str, Any]]
