"""Contratos del endpoint de valoración (versión 1.0)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class AjustesIn(BaseModel):
    """
    Ajustes del Art. 8 del Acuerdo de Valoración.

    Todos los importes son TOTALES de la operación y están en la misma
    moneda que `precio_unitario`. No se envían por unidad.
    """

    flete: Decimal = Field(default=Decimal("0"), ge=0)
    seguro: Decimal = Field(default=Decimal("0"), ge=0)
    comisiones: Decimal = Field(default=Decimal("0"), ge=0)
    corretaje: Decimal = Field(default=Decimal("0"), ge=0)
    envases: Decimal = Field(default=Decimal("0"), ge=0)
    regalias: Decimal = Field(default=Decimal("0"), ge=0)
    asistencia: Decimal = Field(default=Decimal("0"), ge=0)
    otros: Decimal = Field(default=Decimal("0"), ge=0)


class ValuationRequest(BaseModel):
    api_version: str = Field(default="1.0", max_length=16)
    codigo: str = Field(..., min_length=1, max_length=20)
    descripcion: str | None = Field(default=None, max_length=2000)
    cantidad: Decimal = Field(default=Decimal("1"), gt=0)
    precio_unitario: Decimal = Field(..., ge=0)
    moneda: Literal["USD", "PEN"] = "USD"
    condicion: Literal["FOB", "CIF", "EXW"] = "FOB"
    ajustes: AjustesIn = Field(default_factory=AjustesIn)
    tipo_cambio: Decimal | None = Field(default=None, gt=0)
    # Solo para simulación/pruebas: permite calcular con las tasas de otra
    # fecha. En producción se omite y se usa la fecha de Lima.
    fecha_calculo: date | None = None
