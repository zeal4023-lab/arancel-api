"""
Tasas de IGV e IPM con fecha de vigencia.

Fuente: Ley N° 32387 (publicada el 16/06/2025, vigente desde el 01/01/2026).
La ley reduce gradualmente el IGV y aumenta el IPM; el total se mantiene en
18 %. No se hardcodea una sola tasa: se selecciona por la fecha de cálculo.

IMPORTANTE: antes de usar estos valores en producción, contrastarlos con la
norma vigente y con SUNAT. La tabla es la fuente única de verdad del módulo.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class IgvIpmRate:
    desde: date
    hasta: date | None
    igv: Decimal
    ipm: Decimal
    ley: str
    fuente: str
    verificado: bool = True

    @property
    def total(self) -> Decimal:
        return self.igv + self.ipm

    def etiqueta_vigencia(self) -> str:
        fin = self.hasta.isoformat() if self.hasta else "vigente"
        return f"{self.desde.isoformat()} a {fin}"


# Orden ascendente. La última entrada es abierta (sin `hasta`).
TASAS_IGV_IPM: tuple[IgvIpmRate, ...] = (
    IgvIpmRate(
        desde=date(2019, 1, 1),
        hasta=date(2025, 12, 31),
        igv=Decimal("0.16"),
        ipm=Decimal("0.02"),
        ley="TUO de la Ley del IGV (D.S. 055-99-EF)",
        fuente="SUNAT",
    ),
    IgvIpmRate(
        desde=date(2026, 1, 1),
        hasta=date(2026, 12, 31),
        igv=Decimal("0.155"),
        ipm=Decimal("0.025"),
        ley="Ley N° 32387",
        fuente="SUNAT / El Peruano",
    ),
    IgvIpmRate(
        desde=date(2027, 1, 1),
        hasta=date(2027, 12, 31),
        igv=Decimal("0.15"),
        ipm=Decimal("0.03"),
        ley="Ley N° 32387",
        fuente="SUNAT / El Peruano",
    ),
    IgvIpmRate(
        desde=date(2028, 1, 1),
        hasta=date(2028, 12, 31),
        igv=Decimal("0.145"),
        ipm=Decimal("0.035"),
        ley="Ley N° 32387",
        fuente="SUNAT / El Peruano",
    ),
    IgvIpmRate(
        desde=date(2029, 1, 1),
        hasta=None,
        igv=Decimal("0.14"),
        ipm=Decimal("0.04"),
        ley="Ley N° 32387",
        fuente="SUNAT / El Peruano",
    ),
)


def tasas_vigentes(fecha: date) -> tuple[IgvIpmRate, bool]:
    """
    Devuelve (tasa, exacto).

    `exacto=False` significa que la fecha cae fuera de la tabla (antes de
    2019 o después del último tramo conocido) y se usó el tramo más cercano.
    El llamador debe reflejarlo como advertencia.
    """
    for tasa in TASAS_IGV_IPM:
        if tasa.desde <= fecha and (tasa.hasta is None or fecha <= tasa.hasta):
            return tasa, True
    if fecha < TASAS_IGV_IPM[0].desde:
        return TASAS_IGV_IPM[0], False
    return TASAS_IGV_IPM[-1], False
