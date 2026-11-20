"""
Valor deductivo (método 4 del Acuerdo de Valoración).

Se usa cuando NO hay un precio de transacción aceptable (sin factura, partes
vinculadas, precio manifiestamente bajo). Parte del precio al que la mercancía
se vende en el país de importación y desanda el camino hasta el valor en aduana.

Base legal:
  - Acuerdo de Valoración de la OMC, Art. 6.
  - Decisión 571 de la CAN, Art. 6.
  - D.S. N° 186-99-EF.

Modelo (aritmética determinista, sin LLM):

    R   = precio de venta al público por unidad (con IGV)
    t   = tasa IGV + IPM vigente
    a   = ad valorem del código
    m   = margen del importador + gastos generales (sobre el costo)
    E   = gastos internos (flete, almacén) de toda la operación

    neto  = R / (1 + t)                 -> quita el IGV trasladado al cliente
    costo = neto / (1 + m)              -> quita margen y gastos generales
    CIF   = (costo - E) / (1 + a)       -> quita el ad valorem

Si el importador NO puede acreditar el IGV de la importación (p. ej. RUS),
ese IGV sí es un costo y el divisor pasa a ser (1 + a)(1 + t).

El resultado es una ESTIMACIÓN: el valor deductivo real exige precios de
venta a compradores no vinculados en el mayor agregado y deducciones que
SUNAT verifica caso por caso.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from ..preprocess import CatalogNode
from .acuerdos import evaluar_preferencia
from .calculator import AVISO_LEGAL, calcular
from .fx import FxRate
from .rates import tasas_vigentes

CENT = Decimal("0.01")
CIEN = Decimal("100")


def _q(valor: Decimal) -> Decimal:
    return Decimal(valor).quantize(CENT, rounding=ROUND_HALF_UP)


def _d(valor: Any) -> Decimal:
    return Decimal(str(valor))


def calcular_deductivo(
    *,
    nodo: CatalogNode,
    cantidad: Decimal,
    precio_venta_unitario: Decimal,
    moneda: str,
    incluye_igv: bool,
    margen_pct: Decimal,
    gastos_internos: Decimal,
    igv_acreditable: bool,
    fx: FxRate | None,
    fecha: date,
    descripcion: str | None = None,
    origen: str | None = None,
    precio_declarado_unitario: Decimal | None = None,
) -> dict[str, Any]:
    cantidad = _d(cantidad)
    venta = _d(precio_venta_unitario)
    margen = _d(margen_pct)
    gastos = _d(gastos_internos)

    if cantidad <= 0:
        raise ValueError("La cantidad debe ser mayor que cero.")
    if venta <= 0:
        raise ValueError("El precio de venta debe ser mayor que cero.")
    if margen < 0:
        raise ValueError("El margen no puede ser negativo.")
    if gastos < 0:
        raise ValueError("Los gastos internos no pueden ser negativos.")

    avisos: list[str] = []
    supuestos: list[str] = []
    cadena: list[dict[str, Any]] = []

    # --- 1. Moneda --------------------------------------------------------
    if moneda == "PEN":
        if fx is None:
            raise ValueError("Se requiere tipo de cambio para operar en PEN.")
        factor = fx.usd_pen
        if factor <= 0:
            raise ValueError("Tipo de cambio inválido.")
    else:
        factor = Decimal("1")

    # --- 2. Tasas ---------------------------------------------------------
    tasa, tasa_exacta = tasas_vigentes(fecha)
    igv_tasa = tasa.igv
    ipm_tasa = tasa.ipm
    t = igv_tasa + ipm_tasa
    a = _d(nodo.av or 0) / CIEN

    # --- 3. Cadena deductiva ---------------------------------------------
    venta_total = venta * cantidad
    cadena.append({
        "paso": 1,
        "concepto": "Precio de venta al público (total)",
        "valor": float(_q(venta_total)),
        "moneda": moneda,
        "nota": f"{cantidad} unid. x {venta}",
    })

    if incluye_igv:
        neto_total = venta_total / (1 + t)
        cadena.append({
            "paso": 2,
            "concepto": f"Menos IGV + IPM trasladado ({(t * 100):.1f} %)",
            "valor": float(_q(neto_total)),
            "moneda": moneda,
            "nota": "Se divide entre 1 + tasa",
        })
    else:
        neto_total = venta_total
        cadena.append({
            "paso": 2,
            "concepto": "El precio de venta no incluye IGV",
            "valor": float(_q(neto_total)),
            "moneda": moneda,
            "nota": "Sin cambios",
        })

    m = margen / CIEN
    costo_total = neto_total / (1 + m)
    cadena.append({
        "paso": 3,
        "concepto": f"Menos margen y gastos generales ({margen} %)",
        "valor": float(_q(costo_total)),
        "moneda": moneda,
        "nota": "Costo en el que debe incurrir el importador",
    })

    costo_usd = costo_total / factor
    gastos_usd = gastos / factor

    if gastos_usd > 0:
        if gastos_usd >= costo_usd:
            avisos.append(
                "Los gastos internos superan el costo estimado: revisa los "
                "importes, el resultado no es defendible."
            )
        costo_neto_usd = costo_usd - gastos_usd
        cadena.append({
            "paso": 4,
            "concepto": "Menos gastos internos (flete, almacén…)",
            "valor": float(_q(costo_neto_usd)),
            "moneda": "USD",
            "nota": "Gastos posteriores a la importación",
        })
    else:
        costo_neto_usd = costo_usd

    if igv_acreditable:
        divisor = (1 + a)
        nota_divisor = (
            "Solo se descuenta el ad valorem: el IGV de la importación se "
            "acredita contra el IGV de las ventas."
        )
    else:
        divisor = (1 + a) * (1 + t)
        nota_divisor = (
            "Se descuentan ad valorem e IGV/IPM de importación porque no se "
            "acreditan (régimen sin crédito fiscal)."
        )
        avisos.append(
            "Se asumió que el IGV de importación NO es acreditable. Si el "
            "importador tiene crédito fiscal, el valor sería mayor."
        )

    if divisor <= 0:
        raise ValueError("Divisor inválido.")

    cif_total = costo_neto_usd / divisor
    if cif_total <= 0:
        raise ValueError(
            "Con estos datos el valor en aduana resultaría negativo o cero. "
            "Revisa el precio de venta, el margen y los gastos internos."
        )

    cadena.append({
        "paso": 5,
        "concepto": "Menos ad valorem" + (" e IGV/IPM de importación" if not igv_acreditable else ""),
        "valor": float(_q(cif_total)),
        "moneda": "USD",
        "nota": nota_divisor,
    })

    cif_unitario = cif_total / cantidad

    # --- 4. Cuadro completo con el motor del método 1 ---------------------
    # Se recalcula el CIF deducido para obtener el desglose coherente de
    # tributos con exactamente las mismas reglas que /calculate.
    detalle = calcular(
        nodo=nodo,
        cantidad=cantidad,
        precio_unitario=cif_unitario,
        moneda="USD",
        condicion="FOB",
        ajustes={"flete": Decimal("0"), "seguro": Decimal("0")},
        fx=None,
        fecha=fecha,
        descripcion=descripcion,
        origen=origen,
    )

    # --- 5. Comparación con un precio declarado (si lo hay) ---------------
    comparacion = None
    if precio_declarado_unitario is not None:
        declarado = _d(precio_declarado_unitario)
        if declarado <= 0:
            raise ValueError("El precio declarado debe ser mayor que cero.")
        ratio = declarado / cif_unitario if cif_unitario > 0 else Decimal("0")
        comparacion = {
            "precio_declarado_unitario_usd": float(_q(declarado)),
            "valor_deducido_unitario_usd": float(_q(cif_unitario)),
            "ratio_pct": float((ratio * 100).quantize(CENT, rounding=ROUND_HALF_UP)),
            "muy_por_debajo": bool(ratio < Decimal("0.80")),
            "muy_por_encima": bool(ratio > Decimal("1.50")),
        }
        if comparacion["muy_por_debajo"]:
            avisos.append(
                "El precio declarado está muy por debajo del valor deductivo. "
                "SUNAT podría observarlo y exigir justificación."
            )
        elif comparacion["muy_por_encima"]:
            avisos.append(
                "El precio declarado está muy por encima del valor deductivo. "
                "Revisa si incluye costos que no corresponden al valor en aduana."
            )

    # --- 6. Supuestos y avisos -------------------------------------------
    supuestos.extend([
        "Método 4 (valor deductivo): estimación a partir del precio de venta.",
        "El precio de venta corresponde a compradores no vinculados en Perú.",
        "El margen incluye utilidad y gastos generales del importador.",
        f"Base de IGV/IPM: tasas del período {tasa.etiqueta_vigencia()} ({tasa.ley}).",
        "No se incluyen ISC, antidumping, salvaguardias ni percepción.",
    ])
    if not tasa_exacta:
        avisos.append(
            "La fecha de cálculo está fuera de la tabla de tasas conocida; se "
            "usó el tramo más cercano. Verifica la tasa vigente con SUNAT."
        )

    preferencia = evaluar_preferencia(origen, float(_d(nodo.av or 0)))
    if origen and _d(nodo.av or 0) > 0:
        avisos.append(preferencia["nota"])

    avisos.append(
        "El valor deductivo solo se aplica cuando el método 1 no es admisible; "
        "SUNAT lo determina con sus propios comparables."
    )

    return {
        "ok": True,
        "api_version": "1.1",
        "codigo": nodo.codigo,
        "descripcion": nodo.descripcion,
        "descripcion_usuario": descripcion,
        "metodo": {
            "numero": 4,
            "nombre": "Valor deductivo",
            "estado": "estimado",
            "grado": "aproximado",
            "base_legal": (
                "Acuerdo de Valoración OMC Art. 6; Decisión 571 CAN Art. 6; "
                "D.S. 186-99-EF"
            ),
            "requiere_metodo_1_inadmisible": True,
        },
        "moneda_base": "USD",
        "tipo_cambio": {
            "usd_pen": float(fx.usd_pen) if (moneda == "PEN" and fx) else None,
            "fecha": fx.fecha if (moneda == "PEN" and fx) else None,
            "fuente": fx.fuente if (moneda == "PEN" and fx) else "no_requerido",
            "stale": bool(fx.stale) if (moneda == "PEN" and fx) else False,
            "aviso": fx.aviso if (moneda == "PEN" and fx) else None,
        },
        "entrada": {
            "cantidad": float(cantidad),
            "precio_venta_unitario": float(venta),
            "moneda": moneda,
            "incluye_igv": incluye_igv,
            "margen_pct": float(margen),
            "gastos_internos": float(gastos),
            "igv_acreditable": igv_acreditable,
        },
        "valor_deducido": {
            "unitario_usd": float(_q(cif_unitario)),
            "total_usd": float(_q(cif_total)),
        },
        "cadena": cadena,
        "equivalente": detalle,
        "comparacion": comparacion,
        "preferencia_arancelaria": preferencia,
        "supuestos": supuestos,
        "avisos": avisos,
        "aviso_legal": AVISO_LEGAL,
        "trazabilidad": {
            "motor": "python-decimal",
            "redondeo": "ROUND_HALF_UP a 2 decimales",
            "fecha_calculo": fecha.isoformat(),
            "tasas": {
                "periodo": tasa.etiqueta_vigencia(),
                "igv": float(tasa.igv),
                "ipm": float(tasa.ipm),
                "ley": tasa.ley,
            },
            "av_pct": float(_d(nodo.av or 0)),
        },
    }
