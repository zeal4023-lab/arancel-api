"""
Router de valoración. Totalmente aditivo: no altera endpoints existentes.

Si VALUATION_ENABLED=false, `/calculate` responde 503 controlado y el resto
de la API sigue funcionando igual. Cualquier excepción del módulo se captura
y se devuelve como error JSON; nunca se propaga al clasificador.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import settings
from .calculator import calcular
from .fx import FxUnavailable, get_usd_pen
from .schemas import ValuationRequest

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/valuation", tags=["valuation"])

_LIMA = timezone(timedelta(hours=-5))

METODOS = [
    {
        "numero": 1,
        "nombre": "Valor de transacción",
        "implementado": True,
        "requiere": ["precio", "cantidad", "ajustes del Art. 8"],
        "grado": "estricto",
    },
    {
        "numero": 2,
        "nombre": "Mercancías idénticas",
        "implementado": False,
        "requiere": ["importaciones previas valoradas del mismo modelo"],
        "grado": "aproximado",
    },
    {
        "numero": 3,
        "nombre": "Mercancías similares",
        "implementado": False,
        "requiere": ["comparables equivalentes en función y origen"],
        "grado": "aproximado",
    },
    {
        "numero": 4,
        "nombre": "Valor deductivo",
        "implementado": False,
        "requiere": ["precio de venta local a no vinculados"],
        "grado": "aproximado",
    },
    {
        "numero": 5,
        "nombre": "Valor reconstruido",
        "implementado": False,
        "requiere": ["costos de producción en origen"],
        "grado": "estimado",
    },
    {
        "numero": 6,
        "nombre": "Último recurso",
        "implementado": False,
        "requiere": ["criterio razonable documentado"],
        "grado": "estimado",
    },
]


def _hoy_lima() -> date:
    return datetime.now(timezone.utc).astimezone(_LIMA).date()


def _error(status: int, codigo: str, mensaje: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "ok": False,
            "api_version": settings.valuation_api_version,
            "error": {"codigo": codigo, "mensaje": mensaje},
        },
    )


@router.get("/methods")
def metodos() -> dict:
    """Catálogo de métodos. Funciona aunque la valoración esté apagada."""
    return {
        "ok": True,
        "api_version": settings.valuation_api_version,
        "enabled": settings.valuation_enabled,
        "metodos": METODOS,
    }


@router.post("/calculate")
def calcular_valoracion(body: ValuationRequest, request: Request):
    if not settings.valuation_enabled:
        return _error(
            503,
            "VALUATION_DISABLED",
            "La valoración está desactivada (VALUATION_ENABLED=false).",
        )

    if str(body.api_version).split(".")[0] != "1":
        return _error(
            409,
            "API_VERSION_NO_SOPORTADA",
            f"api_version {body.api_version!r} no soportada; usa 1.x.",
        )

    try:
        servicio = request.app.state.service
        nodo = servicio.get_code(body.codigo)
        if nodo is None:
            return _error(
                404,
                "CODIGO_NO_ENCONTRADO",
                f"El código {body.codigo!r} no existe en el catálogo.",
            )
        if not getattr(nodo, "is_leaf", False):
            return _error(
                400,
                "CODIGO_NO_CLASIFICABLE",
                "Se requiere una línea nacional de 10 dígitos (hoja).",
            )

        fx = None
        if body.moneda == "PEN":
            try:
                fx = get_usd_pen(
                    body.tipo_cambio,
                    timeout=settings.valuation_fx_timeout_s,
                    ttl_h=settings.valuation_fx_cache_h,
                )
            except FxUnavailable as exc:
                return _error(503, "FX_NO_DISPONIBLE", str(exc))

        resultado = calcular(
            nodo=nodo,
            cantidad=body.cantidad,
            precio_unitario=body.precio_unitario,
            moneda=body.moneda,
            condicion=body.condicion,
            ajustes=body.ajustes.model_dump(),
            fx=fx,
            fecha=body.fecha_calculo or _hoy_lima(),
            descripcion=body.descripcion,
            origen=body.origen,
        )
        return resultado

    except ValueError as exc:
        return _error(422, "ENTRADA_INVALIDA", str(exc))
    except Exception as exc:  # nunca debe tumbar la API
        log.exception("Fallo inesperado en valoración: %s", exc)
        return _error(
            500,
            "ERROR_INTERNO",
            "La valoración falló; el resto de la API sigue disponible.",
        )
