"""
Tipo de cambio USD/PEN con respaldo en cascada.

Orden:
  1. Valor manual enviado por el usuario (nunca falla si es válido).
  2. API pública open.er-api.com (sin clave, timeout corto).
  3. Último valor de respaldo en `fx_fallback.json` (marcado como stale).

Ningún fallo de red debe impedir el cálculo. Si no hay ninguna fuente y la
operación está en PEN, el router responde con un error controlado.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

log = logging.getLogger(__name__)

_FALLBACK_FILE = Path(__file__).with_name("fx_fallback.json")
_CACHE_KEY = "usd_pen"
_cache: dict[str, tuple[float, "FxRate"]] = {}


class FxUnavailable(RuntimeError):
    """No hay tipo de cambio utilizable."""


@dataclass(frozen=True)
class FxRate:
    usd_pen: Decimal
    fecha: str
    fuente: str
    stale: bool = False
    aviso: str | None = None


def _leer_respaldo() -> FxRate | None:
    """Respaldo local: variable de entorno o archivo JSON."""
    env = os.getenv("VALUATION_FX_FALLBACK", "").strip()
    if env:
        try:
            valor = Decimal(env)
            if valor > 0:
                return FxRate(valor, date.today().isoformat(), "variable de entorno")
        except InvalidOperation:
            log.warning("VALUATION_FX_FALLBACK no es un número válido: %r", env)

    try:
        data = json.loads(_FALLBACK_FILE.read_text(encoding="utf-8"))
        valor = Decimal(str(data["usd_pen"]))
        if valor > 0:
            return FxRate(
                usd_pen=valor,
                fecha=str(data.get("fecha") or "sin fecha"),
                fuente=str(data.get("fuente") or "respaldo local"),
            )
    except (OSError, KeyError, ValueError, InvalidOperation) as exc:
        log.warning("No se pudo leer el respaldo de tipo de cambio: %s", exc)
    return None


def get_usd_pen(
    manual: Decimal | None = None,
    *,
    timeout: float = 4.0,
    ttl_h: float = 12.0,
) -> FxRate:
    """
    Devuelve PEN por 1 USD.

    `manual` tiene prioridad absoluta. El resultado de la API se cachea en
    memoria durante `ttl_h` horas; si la API falla se usa el respaldo.
    """
    if manual is not None:
        valor = Decimal(manual)
        if valor <= 0:
            raise FxUnavailable("El tipo de cambio manual debe ser mayor que cero.")
        return FxRate(valor, date.today().isoformat(), "manual")

    ahora = time.time()
    cacheado = _cache.get(_CACHE_KEY)
    if cacheado and ahora - cacheado[0] < max(0.0, ttl_h) * 3600:
        return cacheado[1]

    try:
        req = urllib.request.Request(
            "https://open.er-api.com/v6/latest/USD",
            headers={"User-Agent": "arancel-api-valuation/1.0"},
        )
        with urllib.request.urlopen(req, timeout=max(0.5, timeout)) as resp:
            data = json.load(resp)
        valor = Decimal(str(data["rates"]["PEN"]))
        if valor <= 0:
            raise ValueError("tasa PEN inválida")
        fecha = str(data.get("time_last_update_utc") or date.today().isoformat())
        fx = FxRate(valor, fecha, "open.er-api.com")
        _cache[_CACHE_KEY] = (ahora, fx)
        return fx
    except Exception as exc:  # red, JSON, clave ausente, tasa inválida
        log.warning("Tipo de cambio en línea no disponible: %s", exc)
        respaldo = _leer_respaldo()
        if respaldo:
            return replace(
                respaldo,
                stale=True,
                aviso=(
                    "No se pudo consultar el tipo de cambio en línea; se usó "
                    f"el respaldo del {respaldo.fecha}."
                ),
            )
        raise FxUnavailable(
            "No se pudo obtener el tipo de cambio y no hay respaldo configurado."
        ) from exc


def clear_cache() -> None:
    """Solo para pruebas."""
    _cache.clear()
