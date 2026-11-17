"""
Equivalencias de clasificación que NO aparecen literalmente en el arancel.

El JSON oficial solo trae descripciones. Muchos productos se clasifican por
NOTA EXPLICATIVA en una línea "Los demás" cuyo texto no nombra el producto.
BM25 no puede recuperar esos códigos con el nombre comercial, porque el índice
no contiene esa palabra. Este módulo inyecta esos términos en el índice en
tiempo de carga (no modifica catalog.json) y adjunta la nota al candidato
para que el LLM sepa por qué aparece ahí.

Mantener este archivo es la forma correcta de ampliar el recall sin tocar el
catálogo oficial: descripción, nivel y av SIEMPRE se copian del catálogo.

Formato: código -> {"terminos": [...], "nota": "..."}
"""
from __future__ import annotations

from .normalize import normalize_code, tokenize

EQUIVALENCIAS: dict[str, dict] = {
    "0106.19.00.00": {
        "terminos": (
            "perro", "perros", "canino", "caninos", "canes", "can",
            "cachorro", "cachorros", "perra", "perras",
            "gato", "gatos", "felino", "felinos", "gata", "gatas",
            "minino", "mascota", "mascotas",
        ),
        "nota": (
            "Nota explicativa de la partida 01.06: los perros y gatos "
            "domesticos vivos se clasifican en 'Los demas' de Mamiferos "
            "(0106.19.00.00), aunque el texto oficial de la linea no los nombra. "
            "Es la linea correcta, no un residual elegido por comodidad."
        ),
    },
    "8528.72.00.00": {
        "terminos": (
            "televisor", "televisores", "television", "televisiones", "tv",
            "tele", "led", "lcd", "plasma", "pantalla plana", "pantalla de cristal",
            "smart tv", "monitor de television",
        ),
        "nota": (
            "Televisor completo a color con pantalla integrada (LCD, LED o "
            "plasma): 8528.72.00.00. El 8528.71 es para aparatos que NO "
            "incorporan pantalla de visualizacion; el 8528.73 es para los "
            "monocromos."
        ),
    },
    # --- Pava / tetera / hervidor (caso real nov 2026) --------------------
    # El texto oficial de 7615.10 no nombra "pava" ni "tetera". La diferencia
    # con 7612 es el USO: cocinar (7615) vs almacenar/transportar (7612).
    "7615.10.20.00": {
        "terminos": (
            "pava", "pavas", "tetera", "teteras", "hervidor", "hervidores",
            "hervir", "fuego", "cocina", "cocinar", "estufa",
        ),
        "nota": (
            "Articulos de uso domestico de aluminio para COCINAR: pavas, "
            "teteras, hervidores, ollas y sartenes van en 7615.10; una pava "
            "o tetera para estufa corresponde a 7615.10.20.00 ('las demas "
            "ollas, sartenes y articulos similares'). NO es 7612: esa "
            "partida es para recipientes que almacenan o transportan "
            "mercancia (tambores, bidones), no utensilios de cocina."
        ),
    },
    "7615.10.80.00": {
        "terminos": (
            "pava", "pavas", "tetera", "teteras", "hervidor", "hervidores",
            "cocina", "cocinar", "estufa", "domestico",
        ),
        "nota": (
            "Articulo de uso domestico de aluminio para cocinar que no "
            "encaja en 'ollas, sartenes y similares' (7615.10.20.00). "
            "Ejemplo: hervidores o pavas de forma especial. NO es 7612 "
            "(recipientes de almacenamiento/transporte)."
        ),
    },
    # --- Vehiculo turismo a gasolina 1000-1500 cc (caso real nov 2026) ----
    "8703.22.10.00": {
        "terminos": (
            "sedan", "sedanes", "hatchback", "gasolina", "turismo",
            "pasajeros", "cilindrada", "1400", "4x4",
        ),
        "nota": (
            "Vehiculo de turismo a gasolina de cilindrada 1.000-1.500 cm3 "
            "(p. ej. 1400 cc): partida 8703.22. Con traccion en las cuatro "
            "ruedas (4x4) es 8703.22.10.00; si es 4x2 (dos ruedas), es "
            "8703.22.90.20. Si la cilindrada fuera menor a 1.000 cc usar "
            "8703.21; si fuera mayor a 1.500 cc, 8703.23."
        ),
    },
    "8703.22.90.20": {
        "terminos": (
            "sedan", "sedanes", "hatchback", "gasolina", "turismo",
            "pasajeros", "cilindrada", "1400", "ensamblado",
        ),
        "nota": (
            "Vehiculo de turismo a gasolina de cilindrada 1.000-1.500 cm3 "
            "(p. ej. 1400 cc), de traccion en dos ruedas (4x2): "
            "8703.22.90.20 (los demas ensamblados). Solo si es 4x4 el "
            "codigo es 8703.22.10.00."
        ),
    },
    # --- Raton / mouse de computadora (caso real nov 2026) -----------------
    # El texto oficial no dice "raton": dice "teclados, dispositivos por
    # coordenadas x-y". Sin este puente, "raton" no aparece en el top 40 y
    # ademas "computadora" arrastra a 8471.41 (equipo completo).
    "8471.60.20.00": {
        "terminos": (
            "raton", "ratones", "mouse", "mouses", "trackball", "touchpad",
            "coordenadas x-y", "teclado", "teclados", "entrada",
        ),
        "nota": (
            "Unidad de entrada de computadora (raton, mouse, teclado, "
            "trackball, touchpad) = 8471.60.20.00 ('teclados, dispositivos "
            "por coordenadas x-y'). NO es 8471.41.00.00: esa linea es un "
            "equipo completo con unidad central de proceso, entrada y salida "
            "en la misma envoltura. Si el dispositivo de entrada no es "
            "teclado ni dispositivo x-y, la alternativa es 8471.60.90.00. "
            "OJO: 8471.60.00.00 no existe en el arancel peruano."
        ),
    },
    # --- Mesa de plastico para camping (MISS historico) -------------------
    # El texto oficial dice solo "muebles de plastico"; la consulta dice
    # "mesa ... camping" y 3924 (vajilla de uso domestico) la desplaza.
    "9403.70.00.00": {
        "terminos": (
            "mesa", "mesas", "camping", "campament",
            "plegable", "jardin", "exterior", "terraza",
        ),
        "nota": (
            "Mesas y sillas de plastico para exterior o camping son "
            "MUEBLES de plastico (9403.70.00.00), no articulos de uso "
            "domestico de vajilla (partida 39.24). Si el mueble es de "
            "metal, seria 9403.20.00.00."
        ),
    },
}


def indices_por_codigo() -> dict[str, tuple[list[str], str]]:
    """codigo_digitos -> (tokens stemmeados, nota de clasificacion)."""
    out: dict[str, tuple[list[str], str]] = {}
    for codigo, cfg in EQUIVALENCIAS.items():
        tokens: list[str] = []
        for termino in cfg["terminos"]:
            for tok in tokenize(termino):
                if tok not in tokens:
                    tokens.append(tok)
        out[normalize_code(codigo)] = (tokens, cfg["nota"])
    return out
