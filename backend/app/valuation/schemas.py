"""Contratos del endpoint de valoración (versión 1.1)."""
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
    api_version: str = Field(default="1.1", max_length=16)
    codigo: str = Field(..., min_length=1, max_length=20)
    descripcion: str | None = Field(default=None, max_length=2000)
    # País de origen (ISO alfa-2 o nombre). Opcional: si se omite, el
    # comportamiento es idéntico al de la versión anterior.
    origen: str | None = Field(default=None, max_length=40)
    cantidad: Decimal = Field(default=Decimal("1"), gt=0)
    precio_unitario: Decimal = Field(..., ge=0)
    moneda: Literal["USD", "PEN"] = "USD"
    condicion: Literal["FOB", "CIF", "EXW"] = "FOB"
    ajustes: AjustesIn = Field(default_factory=AjustesIn)
    tipo_cambio: Decimal | None = Field(default=None, gt=0)
    # Solo para simulación/pruebas: permite calcular con las tasas de otra
    # fecha. En producción se omite y se usa la fecha de Lima.
    fecha_calculo: date | None = None


class DeductiveRequest(BaseModel):
    """
    Valor deductivo (método 4).

    Se usa cuando el método 1 no es admisible. Parte del precio de venta al
    público en Perú y desanda hasta el valor en aduana.
    """

    api_version: str = Field(default="1.1", max_length=16)
    codigo: str = Field(..., min_length=1, max_length=20)
    descripcion: str | None = Field(default=None, max_length=2000)
    origen: str | None = Field(default=None, max_length=40)
    cantidad: Decimal = Field(default=Decimal("1"), gt=0)

    # Precio al que se vendería (o se vende) la unidad en Perú.
    precio_venta_unitario: Decimal = Field(..., gt=0)
    moneda: Literal["USD", "PEN"] = "PEN"
    incluye_igv: bool = True

    # Margen del importador + gastos generales, sobre el costo.
    margen_pct: Decimal = Field(default=Decimal("35"), ge=0, le=95)
    # Flete interno, almacén y otros gastos posteriores a la importación.
    # TOTAL de la operación, en la moneda del precio de venta.
    gastos_internos: Decimal = Field(default=Decimal("0"), ge=0)
    # Si el importador no tiene crédito fiscal, el IGV de importación es costo.
    igv_acreditable: bool = True

    # Opcional: comparar con el precio que se piensa declarar (método 1).
    precio_declarado_unitario: Decimal | None = Field(default=None, gt=0)

    tipo_cambio: Decimal | None = Field(default=None, gt=0)
    fecha_calculo: date | None = None

