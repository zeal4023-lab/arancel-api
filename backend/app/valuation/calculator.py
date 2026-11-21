"""
Cálculo determinista del primer método de valoración (valor de transacción).

No usa LLM. Todo se calcula con Decimal y se redondea a 2 decimales
(ROUND_HALF_UP) para que el resultado sea reproducible y testeable.

Base legal:
  - Acuerdo de Valoración Aduanera de la OMC, Art. 1 y Art. 8.
  - Decisión 571 de la CAN.
  - D.S. N° 186-99-EF (Reglamento de Valoración), vigente con modificatorias.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from ..preprocess import CatalogNode
from .acuerdos import evaluar_preferencia
from .fx import FxRate
from .rates import tasas_vigentes

CENT = Decimal("0.01")
CIEN = Decimal("100")
AJUSTES_ART8 = ("comisiones", "corretaje", "envases", "regalias", "asistencia", "otros")

AVISO_LEGAL = (
    "Estimación referencial calculada con aritmética determinista. El valor "
    "en aduana y los tributos los determina SUNAT conforme al Acuerdo de "
    "Valoración de la OMC, la Decisión 571 de la CAN y el D.S. 186-99-EF. "
    "No constituye liquidación oficial ni reemplaza una DAM."
)


def _q(valor: Decimal) -> Decimal:
    return Decimal(valor).quantize(CENT, rounding=ROUND_HALF_UP)


def _d(valor: Any) -> Decimal:
    return Decimal(str(valor))


def calcular(
    *,
    nodo: CatalogNode,
    cantidad: Decimal,
    precio_unitario: Decimal,
    moneda: str,
    condicion: str,
    ajustes: dict[str, Decimal],
    fx: FxRate | None,
    fecha: date,
    descripcion: str | None = None,
    origen: str | None = None,
) -> dict[str, Any]:
    """
    Calcula CIF, ad valorem, IGV e IPM para el primer método.

    `ajustes` debe traer las claves de AJUSTES_ART8 más `flete` y `seguro`,
    con importes totales en la moneda original.
    """
    cantidad = _d(cantidad)
    precio = _d(precio_unitario)
    if cantidad <= 0:
        raise ValueError("La cantidad debe ser mayor que cero.")
    if precio < 0:
        raise ValueError("El precio unitario no puede ser negativo.")

    avisos: list[str] = []
    supuestos: list[str] = []

    # --- 1. Convertir a USD ------------------------------------------------
    if moneda == "PEN":
        if fx is None:
            raise ValueError("Se requiere tipo de cambio para operar en PEN.")
        factor = fx.usd_pen
        if factor <= 0:
            raise ValueError("Tipo de cambio inválido.")
    else:
        factor = Decimal("1")

    precio_usd = precio / factor
    flete_original = _d(ajustes.get("flete", 0))
    seguro_original = _d(ajustes.get("seguro", 0))
    otros_original = sum((_d(ajustes.get(k, 0)) for k in AJUSTES_ART8), Decimal("0"))

    flete = flete_original / factor
    seguro = seguro_original / factor
    otros = otros_original / factor

    # --- 2. Valor de transacción y CIF ------------------------------------
    valor_total = precio_usd * cantidad

    if condicion == "CIF":
        if flete_original or seguro_original:
            avisos.append(
                "Condición CIF: se ignoraron flete y seguro porque ya están "
                "incluidos en el precio de venta."
            )
        flete = Decimal("0")
        seguro = Decimal("0")
        cif_total = valor_total + otros
    else:
        cif_total = valor_total + flete + seguro + otros
        if condicion == "EXW":
            avisos.append(
                "Condición EXW: no incluye gastos de carga en origen. Verifica "
                "que el flete cubra ese tramo antes de declarar."
            )

    cif_total = _q(cif_total)
    cif_unitario = _q(cif_total / cantidad)

    # --- 3. Ad valorem (del catálogo oficial) -----------------------------
    av_pct = _d(nodo.av or 0)
    ad_valorem = _q(cif_total * av_pct / CIEN)
    if av_pct == 0:
        avisos.append(
            "Ad valorem 0 % para este código: solo se calculan IGV e IPM."
        )

    # --- 3b. Preferencia arancelaria (informativa, NO altera el cálculo) ---
    # El av del catálogo es la tasa general. Si el origen tiene un acuerdo
    # vigente, el derecho real podría ser menor: se avisa y se muestra la
    # tasa general aplicada. No se inventa ni se aplica ninguna preferencia.
    preferencia = evaluar_preferencia(origen, float(av_pct))
    if origen and av_pct > 0:
        avisos.append(preferencia["nota"])

    # --- 4. IGV e IPM sobre (CIF + ad valorem) ----------------------------
    tasa, tasa_exacta = tasas_vigentes(fecha)
    base_igv = cif_total + ad_valorem
    igv = _q(base_igv * tasa.igv)
    ipm = _q(base_igv * tasa.ipm)
    total_tributos = ad_valorem + igv + ipm

    if not tasa_exacta:
        avisos.append(
            "La fecha de cálculo está fuera de la tabla de tasas conocida; se "
            "usó el tramo más cercano. Verifica la tasa vigente con SUNAT."
        )

    if precio == 0:
        avisos.append(
            "Precio unitario cero: SUNAT podría observar el valor declarado."
        )

    if fx is not None and fx.stale:
        avisos.append(fx.aviso or "Se usó un tipo de cambio de respaldo.")

    # --- 5. Supuestos explícitos -----------------------------------------
    supuestos.extend([
        "Los importes de entrada son totales de la operación en la moneda indicada.",
        "El valor en aduana se expresa en USD.",
        "Base de IGV/IPM = CIF + ad valorem (ISC no incluido).",
        "No se incluyen ISC, derechos antidumping o compensatorios, "
        "salvaguardias ni percepción del IGV.",
        f"Tasas IGV/IPM del período {tasa.etiqueta_vigencia()} ({tasa.ley}).",
    ])

    # --- 6. Respuesta ------------------------------------------------------
    return {
        "ok": True,
        "api_version": "1.1",
        "codigo": nodo.codigo,
        "descripcion": nodo.descripcion,
        "descripcion_usuario": descripcion,
        "metodo": {
            "numero": 1,
            "nombre": "Valor de transacción",
            "estado": "aplicado",
            "grado": "estricto",
            "base_legal": (
                "Acuerdo de Valoración OMC Art. 1 y 8; Decisión 571 CAN; "
                "D.S. 186-99-EF"
            ),
        },
        "moneda_base": "USD",
        "condicion": condicion,
        "tipo_cambio": {
            "usd_pen": float(fx.usd_pen) if (moneda == "PEN" and fx) else None,
            "fecha": fx.fecha if (moneda == "PEN" and fx) else None,
            "fuente": fx.fuente if (moneda == "PEN" and fx) else "no_requerido",
            "stale": bool(fx.stale) if (moneda == "PEN" and fx) else False,
            "aviso": fx.aviso if (moneda == "PEN" and fx) else None,
        },
        "unitario": {
            "precio": float(_q(precio_usd)),
            "flete": float(_q(flete / cantidad)),
            "seguro": float(_q(seguro / cantidad)),
            "otros_ajustes": float(_q(otros / cantidad)),
            "cif": float(cif_unitario),
        },
        "total": {
            "cantidad": float(cantidad),
            "valor": float(_q(valor_total)),
            "flete": float(_q(flete)),
            "seguro": float(_q(seguro)),
            "otros_ajustes": float(_q(otros)),
            "cif": float(cif_total),
        },
        "tributos": {
            "av_pct": float(av_pct),
            "base_igv": float(base_igv),
            "ad_valorem": float(ad_valorem),
            "igv": float(igv),
            "ipm": float(ipm),
            "total": float(total_tributos),
            "tasa_igv": float(tasa.igv),
            "tasa_ipm": float(tasa.ipm),
            "incluye": ["ad_valorem", "igv", "ipm"],
            "no_incluye": [
                "isc", "antidumping", "compensatorios", "salvaguardias",
                "percepcion_igv",
            ],
            "unitario": {
                "ad_valorem": float(_q(ad_valorem / cantidad)),
                "igv": float(_q(igv / cantidad)),
                "ipm": float(_q(ipm / cantidad)),
                "total": float(_q(total_tributos / cantidad)),
            },
        },
        "supuestos": supuestos,
        "avisos": avisos,
        "preferencia_arancelaria": preferencia,
        "aviso_legal": AVISO_LEGAL,
        "trazabilidad": {
            "motor": "python-decimal",
            "redondeo": "ROUND_HALF_UP a 2 decimales",
            "fecha_calculo": fecha.isoformat(),
            "catalogo_av": str(nodo.av),
            "tasas": {
                "periodo": tasa.etiqueta_vigencia(),
                "igv": float(tasa.igv),
                "ipm": float(tasa.ipm),
                "total": float(tasa.total),
                "ley": tasa.ley,
                "fuente": tasa.fuente,
                "verificado": tasa.verificado,
                "exacto": tasa_exacta,
            },
            "moneda_entrada": moneda,
            "factor_usd": float(factor),
            "origen": preferencia["origen"],
        },
    }
