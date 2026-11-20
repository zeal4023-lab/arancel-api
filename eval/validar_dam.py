"""
Valida el motor de valoracion F1 contra una DAM real ya liquidada.

Uso:
    python eval\\validar_dam.py
    python eval\\validar_dam.py --tolerancia 1.00
    python eval\\validar_dam.py eval\\dam_casos.json

Lee `eval/dam_casos.json`. Solo evalua los casos con `estado = "validado"`.
Los casos `pendiente` se ignoran (asi la plantilla puede quedarse en el repo).

Criterio de aprobacion: cada cifra debe coincidir con la DAM dentro de la
tolerancia en USD (por defecto 1.00). El ad valorem y las tasas se informan
aparte porque una diferencia ahi suele significar codigo o tasa equivocada,
no redondeo.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.preprocess import load_catalog  # noqa: E402
from app.valuation.calculator import calcular  # noqa: E402
from app.valuation.fx import FxRate  # noqa: E402

CAMPOS = [
    ("ad_valorem", "ad_valorem_dam"),
    ("igv", "igv_dam"),
    ("ipm", "ipm_dam"),
]


def _fecha(valor: str | None) -> date:
    if not valor:
        return date.today()
    return date.fromisoformat(valor)


def _cargar_catalogo() -> dict:
    nodes, _ = load_catalog(ROOT / "backend" / "data" / "catalog.json")
    return {n.codigo: n for n in nodes}


def _comparar(etiqueta: str, motor: float, dam: float, tolerancia: float) -> bool:
    delta = motor - dam
    ok = abs(delta) <= tolerancia
    marca = "OK  " if ok else "DIFF"
    print(f"    {marca} {etiqueta:<22} motor={motor:>12,.2f}  dam={dam:>12,.2f}  delta={delta:>+9,.2f}")
    return ok


def validar_caso(caso: dict, catalogo: dict, tolerancia: float) -> tuple[bool, list[str]]:
    fallos: list[str] = []
    codigo = caso["codigo"]
    nodo = catalogo.get(codigo)
    if nodo is None:
        return False, [f"el codigo {codigo} no existe en el catalogo"]

    oficial = caso.get("oficial_dam") or {}
    moneda = caso.get("moneda", "USD")
    fecha = _fecha(caso.get("fecha_despacho"))

    fx = None
    if moneda == "PEN":
        tc = caso.get("tipo_cambio") or oficial.get("tipo_cambio_dam")
        if not tc:
            return False, ["falta el tipo de cambio para un caso en PEN"]
        fx = FxRate(Decimal(str(tc)), fecha.isoformat(), "dam")

    try:
        res = calcular(
            nodo=nodo,
            cantidad=Decimal(str(caso["cantidad"])),
            precio_unitario=Decimal(str(caso["precio_unitario_fob"])),
            moneda=moneda,
            condicion="FOB",
            ajustes={
                "flete": Decimal(str(caso.get("flete_usd", 0))),
                "seguro": Decimal(str(caso.get("seguro_usd", 0))),
            },
            fx=fx,
            fecha=fecha,
            descripcion=caso.get("nombre"),
        )
    except Exception as exc:  # noqa: BLE001
        return False, [f"el motor fallo: {exc}"]

    print(f"\n  Codigo {codigo} | av catalogo = {res['tributos']['av_pct']}% "
          f"| fecha = {fecha.isoformat()}")
    print(f"  Tasas aplicadas: IGV {res['tributos']['tasa_igv']:.4f} "
          f"/ IPM {res['tributos']['tasa_ipm']:.4f} "
          f"({res['trazabilidad']['tasas']['periodo']})")

    # El ad valorem oficial puede traer una tasa distinta: se avisa, no se oculta.
    av_dam = oficial.get("av_pct_dam")
    if av_dam is not None and abs(float(av_dam) - res["tributos"]["av_pct"]) > 0.001:
        fallos.append(
            f"ad valorem del catalogo ({res['tributos']['av_pct']}%) no coincide con "
            f"la DAM ({av_dam}%): revisar clasificacion o vigencia del arancel"
        )

    ok = True
    for campo_motor, campo_dam in CAMPOS:
        if oficial.get(campo_dam) is None:
            continue
        if not _comparar(campo_motor, res["tributos"][campo_motor], float(oficial[campo_dam]), tolerancia):
            ok = False
            fallos.append(f"{campo_motor} fuera de tolerancia")

    # El CIF se compara desde total, no desde tributos
    if oficial.get("cif_dam") is not None:
        if not _comparar("cif (valor aduana)", res["total"]["cif"], float(oficial["cif_dam"]), tolerancia):
            ok = False
            fallos.append("CIF fuera de tolerancia")

    # FOB / flete / seguro se controlan porque explican casi todas las diferencias
    for campo, clave in (("valor", "fob_usd"), ("flete", "flete_usd"), ("seguro", "seguro_usd")):
        if oficial.get(clave) is not None:
            _comparar(f"{campo} (entrada)", res["total"][campo], float(oficial[clave]), tolerancia)

    if oficial.get("total_tributos_dam") is not None:
        if not _comparar("total tributos", res["tributos"]["total"],
                         float(oficial["total_tributos_dam"]), tolerancia):
            ok = False
            fallos.append("total de tributos fuera de tolerancia")

    if oficial.get("percepcion_dam"):
        print(f"    info percepcion DAM = {float(oficial['percepcion_dam']):,.2f} "
              f"(la F1 no la calcula; queda fuera del total)")

    return ok, fallos


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida la F1 contra DAM reales.")
    parser.add_argument("archivo", nargs="?",
                        default=str(ROOT / "eval" / "dam_casos.json"))
    parser.add_argument("--tolerancia", type=float, default=1.0,
                        help="Tolerancia en USD por cifra (por defecto 1.00)")
    args = parser.parse_args()

    # utf-8-sig: tolera el BOM que agrega el Bloc de notas al guardar.
    datos = json.loads(Path(args.archivo).read_text(encoding="utf-8-sig"))
    casos = [c for c in datos.get("casos", []) if c.get("estado") == "validado"]

    print(f"Casos validados en {Path(args.archivo).name}: {len(casos)}")
    print(f"Tolerancia: +/- {args.tolerancia:,.2f} USD")

    if not casos:
        print("\nNada que evaluar: la plantilla sigue en 'pendiente'.")
        print("Cuando tengas la DAM, copia el bloque, llena las cifras y pon")
        print('"estado": "validado".')
        return 0

    catalogo = _cargar_catalogo()
    fallidos = 0
    for caso in casos:
        print(f"\n=== {caso.get('nombre', caso.get('id', 'sin nombre'))} ===")
        ok, fallos = validar_caso(caso, catalogo, args.tolerancia)
        if ok:
            print("  RESULTADO: coincide con la DAM")
        else:
            fallidos += 1
            print("  RESULTADO: NO coincide")
            for fallo in fallos:
                print(f"    - {fallo}")

    print(f"\nResumen: {len(casos) - fallidos}/{len(casos)} casos coinciden")
    return 1 if fallidos else 0


if __name__ == "__main__":
    raise SystemExit(main())
