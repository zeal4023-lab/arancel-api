"""
Router de valoración. Totalmente aditivo: no altera endpoints existentes.

Si VALUATION_ENABLED=false, `/calculate` responde 503 controlado y el resto
de la API sigue funcionando igual. Cualquier excepción del módulo se captura
y se devuelve como error JSON; nunca se propaga al clasificador.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import settings
from .calculator import calcular
from .deductive import calcular_deductivo
from .fx import FxUnavailable, get_usd_pen
from .reference import (
    BraveProvider,
    NullProvider,
    ReferenceLimitReached,
    ReferenceUnavailable,
    consultar_referencia,
    estado_cupo,
    get_provider,
    set_provider,
)
from .schemas import DeductiveRequest, ReferenceRequest, ValuationRequest

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
        "implementado": True,
        "requiere": ["precio de venta en Perú", "margen y gastos internos"],
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


@router.post("/deductive")
def valoracion_deductiva(body: DeductiveRequest, request: Request):
    """
    Método 4: valor deductivo a partir del precio de venta en Perú.

    Devuelve el valor en aduana implícito y, si se envía un precio declarado,
    lo compara para avisar si está muy por debajo (riesgo de observación).
    """
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

        return calcular_deductivo(
            nodo=nodo,
            cantidad=body.cantidad,
            precio_venta_unitario=body.precio_venta_unitario,
            moneda=body.moneda,
            incluye_igv=body.incluye_igv,
            margen_pct=body.margen_pct,
            gastos_internos=body.gastos_internos,
            igv_acreditable=body.igv_acreditable,
            fx=fx,
            fecha=body.fecha_calculo or _hoy_lima(),
            descripcion=body.descripcion,
            origen=body.origen,
            precio_declarado_unitario=body.precio_declarado_unitario,
        )

    except ValueError as exc:
        return _error(422, "ENTRADA_INVALIDA", str(exc))
    except Exception as exc:  # nunca debe tumbar la API
        log.exception("Fallo inesperado en valor deductivo: %s", exc)
        return _error(
            500,
            "ERROR_INTERNO",
            "El valor deductivo falló; el resto de la API sigue disponible.",
        )


@router.post("/reference")
def referencia_precios(body: ReferenceRequest, request: Request):
    """
    Precios de referencia desde búsqueda web (Brave) + estimación opcional.

    Es referencial: alimenta el método 4 con un precio de venta plausible.
    Nunca se presenta como valor en aduana ni como prueba documental.
    """
    if not settings.valuation_enabled:
        return _error(
            503,
            "VALUATION_DISABLED",
            "La valoración está desactivada (VALUATION_ENABLED=false).",
        )
    if not settings.reference_search_enabled:
        return _error(
            503,
            "REFERENCE_DISABLED",
            "La búsqueda de precios está desactivada "
            "(REFERENCE_SEARCH_ENABLED=false).",
        )
    if str(body.api_version).split(".")[0] != "1":
        return _error(
            409,
            "API_VERSION_NO_SOPORTADA",
            f"api_version {body.api_version!r} no soportada; usa 1.x.",
        )

    # El proveedor se construye la primera vez que hay clave configurada.
    if isinstance(get_provider(), NullProvider):
        if not settings.brave_api_key:
            return _error(
                503,
                "REFERENCE_UNAVAILABLE",
                "Falta configurar BRAVE_API_KEY en el servidor.",
            )
        set_provider(BraveProvider(
            settings.brave_api_key,
            timeout=settings.reference_timeout_s,
        ))

    try:
        resultado = consultar_referencia(
            body.consulta,
            cantidad=settings.reference_count,
            limite_mensual=settings.reference_monthly_limit,
        )
    except ReferenceLimitReached as exc:
        return _error(429, "REFERENCE_LIMIT_REACHED", str(exc))
    except ReferenceUnavailable as exc:
        return _error(503, "REFERENCE_UNAVAILABLE", str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("Fallo inesperado en precios de referencia: %s", exc)
        return _error(
            500,
            "ERROR_INTERNO",
            "La búsqueda de precios falló; el resto de la API sigue disponible.",
        )

    resultado["api_version"] = settings.valuation_api_version
    resultado["cupo"] = estado_cupo(settings.reference_monthly_limit)
    resultado["estimacion_aduanera"] = None

    # Estimación con método 4 usando el precio mediano (solo si hay código).
    rango = resultado.get("rango")
    if body.codigo and rango and resultado.get("moneda") == "PEN":
        try:
            nodo = request.app.state.service.get_code(body.codigo)
            if nodo is not None and getattr(nodo, "is_leaf", False):
                fx = get_usd_pen(
                    body.tipo_cambio,
                    timeout=settings.valuation_fx_timeout_s,
                    ttl_h=settings.valuation_fx_cache_h,
                )
                est = calcular_deductivo(
                    nodo=nodo,
                    cantidad=body.cantidad,
                    precio_venta_unitario=Decimal(str(rango["mediana"])),
                    moneda="PEN",
                    incluye_igv=body.incluye_igv,
                    margen_pct=body.margen_pct,
                    gastos_internos=Decimal("0"),
                    igv_acreditable=True,
                    fx=fx,
                    fecha=_hoy_lima(),
                    origen=body.origen,
                    precio_declarado_unitario=body.precio_declarado_unitario,
                )
                resultado["estimacion_aduanera"] = {
                    "metodo": 4,
                    "precio_venta_usado": rango["mediana"],
                    "valor_deducido_unitario_usd": est["valor_deducido"]["unitario_usd"],
                    "valor_deducido_total_usd": est["valor_deducido"]["total_usd"],
                    "tributos": est["equivalente"]["tributos"],
                    "avisos": est["avisos"],
                }
        except (FxUnavailable, ValueError) as exc:
            resultado["aviso_estimacion"] = f"No se pudo estimar el valor: {exc}"
        except Exception as exc:  # noqa: BLE001
            # La referencia sigue siendo útil aunque falle la estimación.
            log.warning("Fallo la estimación desde referencia: %s", exc)
            resultado["aviso_estimacion"] = "No se pudo estimar el valor en aduana."

    return resultado


