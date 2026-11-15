"""
Normalización de texto para el buscador arancelario.

Objetivos:
  1. Que "Café" == "cafe" == "CAFÉ" == "café,".
  2. Eliminar palabras vacías y la plantilla repetitiva del arancel
     ("los demás", "excepto", "comprendidos", ...), que si no se elimina
     hace que TODOS los códigos residuales se parezcan entre sí.
  3. Reducir plurales de forma conservadora (no usamos un stemmer agresivo
     porque en arancel "vivo" vs "vivos" ya se resuelve con el plural).
  4. Expandir sinónimos del español peruano -> lenguaje técnico del arancel
     (laptop -> computadora portátil, palta -> aguacate, polo -> camiseta).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable

# --- Palabras vacías del español (lista corta y estable) ---------------------
STOPWORDS: frozenset[str] = frozenset(
    """
    a al algo algun alguna algunas alguno algunos ante antes como con contra cual
    cuales cuando de del desde donde dos el ella ellas ellos en entre era es esa
    esas ese eso esos esta estan estas este esto estos fue ha hasta la las le les
    lo los mas me mi mis mucho muy nada ni no nos o os otra otras otro otros para
    pero poco por porque que quien quienes se sea segun ser si sin sobre son su
    sus tambien tan tanto te tiene tienen todo todos tu tus un una unas uno unos y ya
    """.split()
)

# Plantilla legal del arancel: no aporta señal discriminativa.
TARIFF_BOILERPLATE: frozenset[str] = frozenset(
    """
    los las demas otros otras incluso excepto comprendidos comprendidas expresados
    expresadas citados citadas partida partidas seccion capitulo nota notas salvo
    articulos articulo manufacturas productos producto
    """.split()
)

# Términos demasiado genéricos para usarse como señal positiva fuerte.
GENERIC_TERMS: frozenset[str] = frozenset(
    """
    aparatos aparato maquinas maquina instrumentos instrumento material materiales
    similar similares diversos diversas clase clases tipo tipos
    """.split()
)

# Sinónimos / variantes regionales (Perú) -> términos del arancel.
# Se aplican SOLO a la consulta, nunca al índice, para no contaminar documentos.
SYNONYMS: dict[str, tuple[str, ...]] = {
    # tecnología
    "laptop": ("computadora", "portatil"),
    "laptops": ("computadora", "portatil"),
    "notebook": ("computadora", "portatil"),
    "celular": ("telefono", "movil"),
    "celulares": ("telefono", "movil"),
    "smartphone": ("telefono", "movil"),
    "smartphones": ("telefono", "movil"),
    "usb": ("memoria", "almacenamiento"),
    "tv": ("television",),
    "tele": ("television",),
    "televisor": ("television",),
    "televisores": ("television",),
    "led": ("pantalla", "visualizacion", "display"),
    "lcd": ("pantalla", "visualizacion", "display"),
    "plasma": ("pantalla", "visualizacion", "display"),
    "pava": ("olla", "domestico", "cocina"),
    "pavas": ("olla", "domestico", "cocina"),
    "tetera": ("olla", "domestico", "cocina"),
    "teteras": ("olla", "domestico", "cocina"),
    "hervidor": ("olla", "domestico", "cocina"),
    "hervidores": ("olla", "domestico", "cocina"),
    "foco": ("lampara", "bombilla"),
    "bombilla": ("lampara",),
    "cargador": ("cargador", "electrico"),
    # vestimenta
    "polo": ("camiseta",),
    "polos": ("camiseta",),
    "zapatillas": ("calzado", "deportivo"),
    "zapatos": ("calzado",),
    "chompa": ("sueter", "jersey"),
    "casaca": ("abrigo", "chaqueta"),
    # vehículos
    "auto": ("automovil", "turismo", "personas"),
    "autos": ("automovil", "turismo", "personas"),
    "carro": ("automovil", "turismo", "personas"),
    "sedan": ("automovil", "turismo", "personas"),
    "sedán": ("automovil", "turismo", "personas"),
    "sedanes": ("automovil", "turismo", "personas"),
    "pasajeros": ("personas", "turismo"),
    "pasajero": ("personas", "turismo"),
    "gasolina": ("encendido", "chispa", "piston"),
    "automovil": ("turismo", "personas"),
    "automoviles": ("turismo", "personas"),
    "moto": ("motocicleta",),
    "llanta": ("neumatico",),
    "neumatico": ("llanta",),
    # alimentos
    "palta": ("aguacate",),
    "mani": ("cacahuete",),
    "durazno": ("melocoton",),
    "pina": ("anana",),
    "platano": ("banano",),
    "guineo": ("banano",),
    "papa": ("patata",),
    "choclo": ("maiz",),
    "frijol": ("frijol", "frejol"),
    "frijoles": ("frijol", "frejol"),
    "mermelada": ("confitura",),
    "galletas": ("galleta",),
    # hiperónimos de muebles (el arancel agrupa por "muebles", no por "mesa")
    "mesa": ("mueble",),
    "silla": ("asiento", "mueble"),
    "ropero": ("armario", "mueble"),
    "cama": ("mueble",),
    "estante": ("mueble",),
    # bebidas / eléctrico
    "tinto": ("vino",),
    "cargador": ("convertidor", "electrico"),
    # hogar
    "refrigeradora": ("refrigerador",),
    "refri": ("refrigerador",),
    "lentes": ("anteojos", "gafas"),
    "gafas": ("anteojos",),
    "anteojos": ("gafas",),
    "silla": ("asiento",),
}


def strip_accents(text: str) -> str:
    """Quita tildes y diacríticos preservando la ñ como n."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize(text: str) -> str:
    """Minúsculas, sin tildes, sin puntuación, espacios colapsados."""
    if not text:
        return ""
    text = strip_accents(str(text)).lower()
    # conserva letras, dígitos y espacios; los puntos de códigos se eliminan aquí
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_CODE_RE = re.compile(r"[^0-9]")


def normalize_code(code: str) -> str:
    """'0101.21.00.00' -> '0101210000' (prefijos jerárquicos comparables)."""
    return _CODE_RE.sub("", str(code or ""))


def stem(word: str) -> str:
    """
    Stemmer conservador orientado al español arancelario.

    Resuelve:
      * plurales:      caballos -> caballo, animales -> animal, lápices -> lapiz
      * género:        bovina/bovino -> bovin, congelada/congelado -> congelad
                       (el arancel usa "especie bovina" y el usuario "bovino")

    No usa Snowball completo a propósito: fusionaría términos técnicos
    distintos y perderíamos precisión en un vocabulario tan específico.
    El umbral de 6 letras evita colisiones como mango/manga, pollo/polla.
    """
    if len(word) <= 4:
        return word
    if word.endswith("ces") and len(word) > 5:
        word = word[:-3] + "z"          # lápices -> lapiz
    elif word.endswith("es") and len(word) > 5:
        word = word[:-2]                # animales -> animal
    elif word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]                # caballos -> caballo

    # Género gramatical: solo en palabras largas para no crear colisiones.
    if len(word) >= 6 and word[-1] in "ao":
        word = word[:-1]                # bovina -> bovin, congelado -> congelad
    return word


def tokenize(
    text: str,
    *,
    drop_stopwords: bool = True,
    drop_boilerplate: bool = True,
) -> list[str]:
    """Tokeniza + normaliza + aplica stem. Sin expansión de sinónimos."""
    tokens: list[str] = []
    for raw in normalize(text).split():
        if len(raw) < 2:
            continue
        if drop_stopwords and raw in STOPWORDS:
            continue
        if drop_boilerplate and raw in TARIFF_BOILERPLATE:
            continue
        token = stem(raw)
        if len(token) < 2:
            continue
        tokens.append(token)
    return tokens


def expand_query(tokens: Iterable[str]) -> dict[str, float]:
    """
    Devuelve {término: peso} para la consulta.
    Los sinónimos entran con peso 0.55 para no dominar la señal original.
    """
    weights: dict[str, float] = {}
    for token in tokens:
        weights[token] = max(weights.get(token, 0.0), 1.0)
        for syn in SYNONYMS.get(token, ()):
            syn_norm = normalize(syn)
            for part in syn_norm.split():
                part = stem(part)
                if part:
                    weights[part] = max(weights.get(part, 0.0), 0.55)
    return weights


def is_residual(descripcion: str) -> bool:
    """
    Código 'bolsa' (los demás): válido, pero no debe ganar por defecto.

    IMPORTANTE: `descripcion` es la ruta completa unida con " - ". Solo mira
    el ÚLTIMO tramo (la descripción propia de la hoja). Si se evalúa la ruta
    entera, partidas cuyo título contiene "Los demás" (p. ej. 01.06 "Los demás
    animales vivos") marcarían TODAS sus líneas como residuales, incluso
    "Primates", "Conejos y liebres" o "Aves de rapiña", que son específicas.
    """
    propia = descripcion.rsplit(" - ", 1)[-1]
    return bool(re.search(r"\b(?:los|las) demas\b", normalize(propia)))
