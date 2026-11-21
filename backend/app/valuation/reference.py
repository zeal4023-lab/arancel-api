"""
Precios de referencia desde búsqueda web (Brave Search API).

Qué hace:
  - Busca un producto en la web y extrae precios de los resultados.
  - Resume los precios con aritmética determinista (mediana y rango).
  - Opcionalmente alimenta el método 4 con el precio mediano.

Qué NO hace:
  - No guarda resultados. Los términos de Brave (cláusula 3(b)(i)) prohíben
    almacenar o cachear Search Results salvo de forma transitoria. Aquí se
    calcula, se devuelve y se descarta.
  - No expone listas de resultados: solo el dominio de origen y la estimación.
  - No usa LLM. Extracción con expresiones regulares y estadística simple.
  - Un precio web NO es valor aduanero. Siempre se etiqueta como referencial.

Proveedor intercambiable: si Brave desaparece o se apaga el flag, el resto de
la valoración (métodos 1 y 4) sigue funcionando.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Protocol

log = logging.getLogger(__name__)

_LIMA = timezone(timedelta(hours=-5))
CENT = Decimal("0.01")


class ReferenceUnavailable(RuntimeError):
    """No se pudo obtener una referencia de precios."""


class ReferenceLimitReached(RuntimeError):
    """Se agotó el cupo mensual configurado."""


# ---------------------------------------------------------------------------
# Proveedor
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResultadoBusqueda:
    titulo: str
    descripcion: str
    url: str


class ProveedorBusqueda(Protocol):
    def buscar(self, consulta: str, cantidad: int) -> list[ResultadoBusqueda]: ...


class _HttpError(Exception):
    def __init__(self, code: int, cuerpo: str) -> None:
        super().__init__(f"{code}: {cuerpo[:200]}")
        self.code = code
        self.cuerpo = cuerpo


class BraveProvider:
    """Cliente de Brave. Usa LLM Context (texto de página) con reserva a web."""

    URL_CONTEXT = "https://api.search.brave.com/res/v1/llm/context"
    URL_WEB = "https://api.search.brave.com/res/v1/web/search"

    # Combinaciones de parámetros opcionales, de la más específica a la más
    # simple. Si Brave rechaza una (422), se baja un escalón y se recuerda
    # la que funciona para no repetir el error en cada llamada.
    _COMBOS = (
        {"country": "pe", "search_lang": "es", "safesearch": "moderate"},
        {"country": "pe", "search_lang": "es"},
        {"search_lang": "es"},
        {},
    )
    _combo_desde = 0

    def __init__(self, api_key: str, timeout: float = 8.0) -> None:
        if not api_key:
            raise ReferenceUnavailable("Falta BRAVE_API_KEY.")
        self._key = api_key
        self._timeout = max(1.0, timeout)

    @staticmethod
    def _decodificar(crudo: bytes, encoding: str | None) -> str:
        if (encoding or "").lower() == "gzip":
            try:
                import gzip

                crudo = gzip.decompress(crudo)
            except OSError:
                pass
        return crudo.decode("utf-8", errors="replace")

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self._key,
            "User-Agent": "arancel-api-reference/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                texto = self._decodificar(resp.read(), resp.headers.get("Content-Encoding"))
        except urllib.error.HTTPError as exc:
            raise _HttpError(
                exc.code,
                self._decodificar(exc.read(), exc.headers.get("Content-Encoding")),
            ) from None
        except Exception as exc:  # red, DNS, timeout
            raise ReferenceUnavailable(f"No se pudo consultar Brave: {exc}") from exc

        try:
            return json.loads(texto)
        except json.JSONDecodeError as exc:
            raise ReferenceUnavailable(
                f"Brave devolvió una respuesta ilegible: {texto[:120]}"
            ) from exc

    @staticmethod
    def _mapear(exc: _HttpError) -> Exception:
        detalle = " ".join(exc.cuerpo.split())[:180]
        if exc.code in (401, 403):
            return ReferenceUnavailable(f"La clave de Brave no es válida ({exc.code}).")
        if exc.code == 402:
            return ReferenceLimitReached(
                "Se agotaron los créditos de Brave para este período."
            )
        if exc.code == 429:
            return ReferenceLimitReached("Brave rechazó la solicitud por límite de uso.")
        return ReferenceUnavailable(f"Brave respondió {exc.code}: {detalle}")

    def buscar(self, consulta: str, cantidad: int) -> list[ResultadoBusqueda]:
        """Intenta LLM Context (texto de página); si falla, usa web/search."""
        try:
            return self._buscar_contexto(consulta)
        except ReferenceLimitReached:
            raise
        except (ReferenceUnavailable, _HttpError) as exc:
            log.warning("LLM Context no disponible (%s); se usa web/search.", exc)
        return self._buscar_web(consulta, cantidad)

    def _buscar_contexto(self, consulta: str) -> list[ResultadoBusqueda]:
        params = {"q": consulta, "maximum_number_of_tokens": 4096}
        url = f"{self.URL_CONTEXT}?{urllib.parse.urlencode(params)}"
        try:
            data = self._get(url)
        except _HttpError as exc:
            if exc.code == 422:
                # Sin parámetros opcionales por si alguno dejó de ser válido.
                data = self._get(f"{self.URL_CONTEXT}?{urllib.parse.urlencode({'q': consulta})}")
            else:
                raise

        genericos = ((data or {}).get("grounding") or {}).get("generic") or []
        salida: list[ResultadoBusqueda] = []
        for g in genericos:
            if not isinstance(g, dict):
                continue
            texto = " ".join(str(s) for s in (g.get("snippets") or []))
            salida.append(ResultadoBusqueda(
                titulo=str(g.get("title") or ""),
                descripcion=texto,
                url=str(g.get("url") or ""),
            ))
        if not salida:
            raise ReferenceUnavailable("LLM Context no devolvió contenido.")
        return salida

    def _buscar_web(self, consulta: str, cantidad: int) -> list[ResultadoBusqueda]:
        n = max(1, min(cantidad, 20))
        ultimo: _HttpError | None = None
        for idx in range(BraveProvider._combo_desde, len(self._COMBOS)):
            params = {"q": consulta, "count": n, **self._COMBOS[idx]}
            url = f"{self.URL_WEB}?{urllib.parse.urlencode(params)}"
            try:
                data = self._get(url)
            except _HttpError as exc:
                if exc.code == 422:
                    log.warning(
                        "Brave rechazó la combinación %s (422): %s",
                        sorted(self._COMBOS[idx]),
                        " ".join(exc.cuerpo.split())[:160],
                    )
                    ultimo = exc
                    continue
                raise self._mapear(exc) from None
            BraveProvider._combo_desde = idx
            return self._parsear(data)

        if ultimo is not None:
            raise self._mapear(ultimo) from None
        raise ReferenceUnavailable("Brave no devolvió resultados utilizables.")

    @staticmethod
    def _parsear(data: dict) -> list[ResultadoBusqueda]:
        resultados = ((data or {}).get("web") or {}).get("results") or []
        salida: list[ResultadoBusqueda] = []
        for r in resultados:
            salida.append(ResultadoBusqueda(
                titulo=str(r.get("title") or ""),
                descripcion=str(r.get("description") or ""),
                url=str(r.get("url") or ""),
            ))
        return salida


class NullProvider:
    """Proveedor vacío: el módulo existe pero no hace llamadas."""

    def buscar(self, consulta: str, cantidad: int) -> list[ResultadoBusqueda]:
        raise ReferenceUnavailable("La búsqueda de precios no está configurada.")


_proveedor: ProveedorBusqueda = NullProvider()


def set_provider(proveedor: ProveedorBusqueda) -> None:
    """Solo para pruebas y para inyectar el proveedor al arrancar."""
    global _proveedor
    _proveedor = proveedor


def get_provider() -> ProveedorBusqueda:
    return _proveedor


# ---------------------------------------------------------------------------
# Extracción de precios (determinista, sin LLM)
# ---------------------------------------------------------------------------

_SOLES = re.compile(
    r"(?:S\s*/\s*\.?|PEN)\s*(\d[\d.,]*)"
    r"|(\d[\d.,]*)\s*(?:soles|SOLES|S\s*/\s*\.?)",
    re.IGNORECASE,
)
_DOLARES = re.compile(r"(?:US\s*\$|USD|\$)\s*(\d[\d.,]*)")


def _a_decimal(crudo: str) -> Decimal | None:
    """Convierte '1,299.90', '1.299,90', '59,90' o '59.90' a Decimal."""
    texto = crudo.strip().rstrip(".,")
    if not texto:
        return None
    tiene_coma = "," in texto
    tiene_punto = "." in texto

    if tiene_coma and tiene_punto:
        # El último separador es el decimal.
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif tiene_coma:
        # Coma decimal si deja 2 dígitos al final; si no, separador de miles.
        if len(texto.rsplit(",", 1)[1]) == 2:
            texto = texto.replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif tiene_punto:
        # Punto decimal si deja 2 dígitos al final; si no, de miles.
        if len(texto.rsplit(".", 1)[1]) != 2:
            texto = texto.replace(".", "")

    try:
        valor = Decimal(texto)
    except InvalidOperation:
        return None
    return valor if valor > 0 else None


def extraer_precios(resultados: list[ResultadoBusqueda]) -> tuple[list[Decimal], list[Decimal]]:
    """Devuelve (precios en soles, precios en dólares) encontrados en el texto."""
    soles: list[Decimal] = []
    dolares: list[Decimal] = []
    for r in resultados:
        texto = f"{r.titulo} {r.descripcion}"
        for m in _SOLES.finditer(texto):
            valor = _a_decimal(m.group(1) or m.group(2) or "")
            if valor is not None:
                soles.append(valor)
        for m in _DOLARES.finditer(texto):
            valor = _a_decimal(m.group(1))
            if valor is not None:
                dolares.append(valor)
    return soles, dolares


def _mediana(valores: list[Decimal]) -> Decimal:
    ordenados = sorted(valores)
    n = len(ordenados)
    medio = n // 2
    if n % 2:
        return ordenados[medio]
    return (ordenados[medio - 1] + ordenados[medio]) / 2


def resumir(precios: list[Decimal]) -> dict | None:
    """
    Resume precios con una limpieza simple y determinista.

    Descarta valores a más de 4x o menos de 1/4 de la mediana (accesorios,
    repuestos, errores de tipeo). Exige al menos 2 muestras tras la limpieza.
    """
    if len(precios) < 2:
        return None
    mediana_inicial = _mediana(precios)
    if mediana_inicial <= 0:
        return None
    limite_bajo = mediana_inicial / 4
    limite_alto = mediana_inicial * 4
    limpios = [p for p in precios if limite_bajo <= p <= limite_alto]
    if len(limpios) < 2:
        return None
    return {
        "muestras": len(limpios),
        "descartados": len(precios) - len(limpios),
        "min": float(min(limpios).quantize(CENT)),
        "mediana": float(_mediana(limpios).quantize(CENT)),
        "max": float(max(limpios).quantize(CENT)),
    }


# ---------------------------------------------------------------------------
# Cupo mensual (en memoria; no sustituye al límite de Brave)
# ---------------------------------------------------------------------------

_dia: str = ""
_usadas: int = 0
_ultimo_error: str | None = None


def _clave_mes() -> str:
    return datetime.now(timezone.utc).astimezone(_LIMA).strftime("%Y-%m")


def _reiniciar_si_cambio_mes() -> None:
    global _dia, _usadas
    actual = _clave_mes()
    if actual != _dia:
        _dia = actual
        _usadas = 0


def estado_cupo(limite: int) -> dict:
    _reiniciar_si_cambio_mes()
    return {
        "periodo": _dia,
        "usadas": _usadas,
        "limite": limite,
        "restantes": max(0, limite - _usadas),
        "ultimo_error": _ultimo_error,
    }


def _consumir_cupo(limite: int) -> None:
    """Cuenta el intento ANTES de llamar: si falla, Brave igual pudo facturar."""
    global _usadas
    _reiniciar_si_cambio_mes()
    if limite > 0 and _usadas >= limite:
        raise ReferenceLimitReached(
            f"Se alcanzó el límite mensual de {limite} búsquedas de referencia."
        )
    _usadas += 1


def _anotar_error(mensaje: str | None) -> None:
    global _ultimo_error
    _ultimo_error = mensaje


# ---------------------------------------------------------------------------
# Caché transitoria (segundos, no persistente)
# ---------------------------------------------------------------------------
# La cláusula 3(b)(i) permite "almacenamiento transitorio requerido para la
# operación". Esto evita que un doble toque en la app gaste dos consultas.
# No es una base de datos ni un caché de días: vive en memoria y caduca sola.

_TRANSITORIO: dict[str, tuple[float, list[ResultadoBusqueda]]] = {}
_TTL_TRANSITORIO = 120.0


def _buscar_con_transitorio(consulta: str, cantidad: int) -> list[ResultadoBusqueda]:
    clave = f"{consulta.lower().strip()}|{cantidad}"
    ahora = time.time()
    guardado = _TRANSITORIO.get(clave)
    if guardado and ahora - guardado[0] < _TTL_TRANSITORIO:
        return guardado[1]
    resultados = get_provider().buscar(consulta, cantidad)
    if len(_TRANSITORIO) > 200:  # techo de memoria
        _TRANSITORIO.clear()
    _TRANSITORIO[clave] = (ahora, resultados)
    return resultados


def limpiar_transitorio() -> None:
    _TRANSITORIO.clear()


def reiniciar_cupo() -> None:
    """Solo para pruebas: vuelve el contador mensual a cero."""
    global _dia, _usadas, _ultimo_error
    _dia = ""
    _usadas = 0
    _ultimo_error = None


def _es_peruano(url: str) -> bool:
    """Solo se aceptan tiendas peruanas: evita mezclar pesos con soles."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host.endswith(".pe")


# Tiendas de EE. UU. con precio publicado en dólares. Lista explícita: aceptar
# cualquier ".com" metería sitios de otros países y rompería la moneda.
_DOMINIOS_US = {
    "amazon.com", "walmart.com", "bestbuy.com", "target.com", "homedepot.com",
    "lowes.com", "costco.com", "ebay.com", "macys.com", "nike.com",
    "apple.com", "newegg.com", "bhphotovideo.com", "adorama.com", "rei.com",
    "kohls.com", "wayfair.com", "chewy.com", "sephora.com", "walgreens.com",
    "cvs.com", "staples.com", "officedepot.com", "ikea.com", "overstock.com",
    "zappos.com", "nordstrom.com", "samsclub.com", "microcenter.com",
    "petco.com", "petsmart.com", "dickssportinggoods.com", "academy.com",
}


def _aceptar(url: str, mercado: str) -> bool:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if mercado == "US":
        return host in _DOMINIOS_US or host.endswith(".us")
    return host.endswith(".pe")


def _con_contexto_peru(consulta: str, mercado: str = "PE") -> str:
    """
    Brave rechaza `country=pe`, así que el contexto se pide en el texto.
    Sin esto la búsqueda devuelve tiendas de Chile, Argentina o España.
    """
    limpio = " ".join(consulta.split())
    if mercado == "US":
        return limpio
    bajo = limpio.lower()
    if "peru" in bajo or "perú" in bajo:
        return limpio
    return f"{limpio} Perú"


def consultar_referencia(
    consulta: str,
    *,
    cantidad: int,
    limite_mensual: int,
    mercado: str = "PE",
) -> dict:
    """
    Busca precios de referencia. Devuelve un resumen listo para la API.

    Lanza ReferenceUnavailable / ReferenceLimitReached para que el router
    decida el código HTTP.
    """
    consulta = (consulta or "").strip()
    if len(consulta) < 3:
        raise ReferenceUnavailable("La búsqueda es demasiado corta.")

    _consumir_cupo(limite_mensual)
    consulta_enviada = _con_contexto_peru(consulta, mercado)
    try:
        resultados = _buscar_con_transitorio(consulta_enviada, cantidad)
    except ReferenceLimitReached as exc:
        _anotar_error(str(exc))
        raise
    except Exception as exc:  # noqa: BLE001
        _anotar_error(str(exc))
        raise

    aceptados = [r for r in resultados if _aceptar(r.url, mercado)]
    ignorados = len(resultados) - len(aceptados)

    dominios: list[str] = []
    for r in aceptados:
        host = urllib.parse.urlparse(r.url).hostname or ""
        if host and host not in dominios:
            dominios.append(host)

    if mercado == "US":
        # En EE. UU. solo interesa el precio en dólares.
        _, dolares = extraer_precios(aceptados)
        resumen_soles = None
        resumen_dolares = resumir(dolares)
        etiqueta = "tiendas de Estados Unidos"
    else:
        soles, dolares = extraer_precios(aceptados)
        resumen_soles = resumir(soles)
        resumen_dolares = resumir(dolares)
        etiqueta = "tiendas peruanas"

    if not aceptados:
        _anotar_error(None)
        return {
            "ok": True,
            "referencial": True,
            "mercado": mercado,
            "consulta": consulta,
            "consulta_enviada": consulta_enviada,
            "rango": None,
            "fuentes": [],
            "resultados_ignorados": ignorados,
            "aviso": (
                f"No se encontraron resultados de {etiqueta} para esta "
                "búsqueda. Prueba con marca, modelo o capacidad."
            ),
        }

    if resumen_soles is None and resumen_dolares is None:
        _anotar_error(None)
        return {
            "ok": True,
            "referencial": True,
            "mercado": mercado,
            "consulta": consulta,
            "consulta_enviada": consulta_enviada,
            "rango": None,
            "fuentes": dominios[:5],
            "resultados_ignorados": ignorados,
            "aviso": (
                f"No se encontraron precios utilizables en {etiqueta}. "
                "Prueba con una descripción más específica (marca, modelo, "
                "capacidad)."
            ),
        }

    _anotar_error(None)
    primario = resumen_soles or resumen_dolares
    if mercado == "US":
        aviso = (
            "Precio referencial de tiendas de Estados Unidos (USD). Sirve "
            "para juzgar si el valor declarado es razonable; no es valor en "
            "aduana ni precio de reventa en Perú."
        )
    else:
        aviso = (
            "Precio referencial obtenido de tiendas peruanas. No es valor en "
            "aduana, no constituye prueba documental y no reemplaza la "
            "determinación de SUNAT."
        )

    return {
        "ok": True,
        "referencial": True,
        "mercado": mercado,
        "consulta": consulta,
        "consulta_enviada": consulta_enviada,
        # Rango principal: se prefiere soles (moneda local), pero si la tienda
        # publica en dólares se devuelve igual y se etiqueta la moneda.
        "rango": primario,
        "moneda": "PEN" if resumen_soles else "USD",
        "rangos": {"PEN": resumen_soles, "USD": resumen_dolares},
        "fuentes": dominios[:5],
        "resultados_ignorados": ignorados,
        "aviso": aviso,
    }
