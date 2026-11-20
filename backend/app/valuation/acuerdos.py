"""
Acuerdos comerciales vigentes entre Perú y otros países.

ADVERTENCIA: esta tabla es REFERENCIAL. No determina si una mercancía
concreta califica para preferencia arancelaria: eso depende del certificado
de origen, de la regla de origen específica y del trato vigente a la fecha
de despacho. La tabla solo sirve para decidir si se muestra una advertencia.

Nunca se usa para calcular: el ad valorem aplicado sigue saliendo del
catálogo (tasa general). El módulo avisa que puede existir una preferencia
y deja la decisión al importador y a SUNAT.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Acuerdo:
    nombre: str
    vigencia: str


# ISO-3166 alfa-2 -> acuerdo. Agrupados por tipo.
_ACUERDOS: dict[str, Acuerdo] = {}


def _registrar(codigos: str, nombre: str, vigencia: str) -> None:
    for codigo in codigos.split():
        _ACUERDOS[codigo] = Acuerdo(nombre, vigencia)


_registrar("BO CO EC", "Comunidad Andina (CAN)", "1969")
_registrar("AR BR PY UY", "MERCOSUR (ACE 58)", "2005")

_registrar("US", "TLC Perú - Estados Unidos", "2009")
_registrar("CN", "Acuerdo Comercial Perú - China", "2010")
_registrar("CA", "TLC Perú - Canadá", "2009")
_registrar("SG", "TLC Perú - Singapur", "2009")
_registrar("CL", "Acuerdo de Libre Comercio Perú - Chile", "2009")
_registrar("MX", "Acuerdo de Integración Comercial Perú - México", "2012")
_registrar("KR", "TLC Perú - Corea del Sur", "2011")
_registrar("JP", "Acuerdo de Asociación Económica Perú - Japón", "2012")
_registrar("TH", "Protocolo Perú - Tailandia", "2011")
_registrar("PA", "TLC Perú - Panamá", "2012")
_registrar("CR", "TLC Perú - Costa Rica", "2013")
_registrar("HN", "TLC Perú - Honduras", "2017")
_registrar("GB", "Acuerdo de Continuidad Perú - Reino Unido", "2021")
_registrar("CH NO IS LI", "TLC Perú - EFTA", "2011")
_registrar("IN", "Acuerdo de Alcance Parcial Perú - India", "2012")

# Acuerdo Comercial Perú - Unión Europea (2013): 27 Estados miembros.
_registrar(
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE",
    "Acuerdo Comercial Perú - Unión Europea",
    "2013",
)

# CPTPP (vigente para Perú desde 2021).
_registrar("AU NZ MY VN BN", "CPTPP", "2021")

# Nombres frecuentes en español -> ISO alfa-2.
_ALIAS = {
    "CHINA": "CN",
    "ESTADOS UNIDOS": "US",
    "EEUU": "US",
    "EE UU": "US",
    "USA": "US",
    "COREA DEL SUR": "KR",
    "COREA": "KR",
    "JAPON": "JP",
    "TAILANDIA": "TH",
    "INDIA": "IN",
    "CANADA": "CA",
    "MEXICO": "MX",
    "CHILE": "CL",
    "BRASIL": "BR",
    "ARGENTINA": "AR",
    "COLOMBIA": "CO",
    "ECUADOR": "EC",
    "BOLIVIA": "BO",
    "SUIZA": "CH",
    "NORUEGA": "NO",
    "REINO UNIDO": "GB",
    "ALEMANIA": "DE",
    "ESPANA": "ES",
    "ITALIA": "IT",
    "FRANCIA": "FR",
    "PAISES BAJOS": "NL",
    "HOLANDA": "NL",
    "BELGICA": "BE",
    "PORTUGAL": "PT",
    "AUSTRALIA": "AU",
    "NUEVA ZELANDA": "NZ",
    "VIETNAM": "VN",
    "MALASIA": "MY",
    "PANAMA": "PA",
    "COSTA RICA": "CR",
    "HONDURAS": "HN",
    "SINGAPUR": "SG",
    "CROACIA": "HR",
    "DINAMARCA": "DK",
    "SUECIA": "SE",
    "FINLANDIA": "FI",
    "IRLANDA": "IE",
    "AUSTRIA": "AT",
    "POLONIA": "PL",
    "RUMANIA": "RO",
    "GRECIA": "GR",
    "REPUBLICA CHECA": "CZ",
    "HUNGRIA": "HU",
    "BULGARIA": "BG",
    "ESLOVAQUIA": "SK",
    "ESLOVENIA": "SI",
    "LUXEMBURGO": "LU",
    "MALTA": "MT",
    "CHIPRE": "CY",
    "ESTONIA": "EE",
    "LETONIA": "LV",
    "LITUANIA": "LT",
}


def _sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def normalizar_origen(origen: str | None) -> str | None:
    """Devuelve el código ISO alfa-2, o None si no se puede interpretar."""
    if not origen:
        return None
    limpio = _sin_acentos(str(origen).strip().upper().replace(".", ""))
    if not limpio:
        return None
    if len(limpio) == 2 and limpio.isalpha():
        return limpio
    return _ALIAS.get(limpio)


def acuerdo_de(origen: str | None) -> Acuerdo | None:
    codigo = normalizar_origen(origen)
    if codigo is None:
        return None
    return _ACUERDOS.get(codigo)


def evaluar_preferencia(origen: str | None, av_pct: float) -> dict:
    """
    Describe si puede aplicar una preferencia arancelaria.

    No calcula ninguna tasa: informa y advierte. `puede_aplicar` en True
    significa "verificar", no "aplica".
    """
    codigo = normalizar_origen(origen)
    acuerdo = _ACUERDOS.get(codigo) if codigo else None

    if av_pct <= 0:
        return {
            "origen": codigo,
            "acuerdo": acuerdo.nombre if acuerdo else None,
            "puede_aplicar": False,
            "ad_valorem_general": av_pct,
            "nota": (
                "Ad valorem 0 %: no hay derecho que reducir, la preferencia "
                "arancelaria es irrelevante para este código."
            ),
        }

    if acuerdo is not None:
        nota = (
            f"Origen {codigo}: existe {acuerdo.nombre} (vigente desde "
            f"{acuerdo.vigencia}). El ad valorem aplicado ({av_pct:.2f} %) es la "
            f"tasa GENERAL. Con certificado de origen válido el derecho podría "
            f"ser menor o 0 %, y este resultado estaría sobreestimando tributos. "
            f"Verifica el tratamiento preferencial en la DAM."
        )
        return {
            "origen": codigo,
            "acuerdo": acuerdo.nombre,
            "puede_aplicar": True,
            "ad_valorem_general": av_pct,
            "nota": nota,
        }

    nota = (
        "No se reconoce un acuerdo comercial con el origen declarado. El ad "
        "valorem aplicado es la tasa GENERAL; si existe alguna preferencia "
        "arancelaria no listada, el derecho real podría ser menor."
    )
    return {
        "origen": codigo,
        "acuerdo": None,
        "puede_aplicar": False,
        "ad_valorem_general": av_pct,
        "nota": nota,
    }
