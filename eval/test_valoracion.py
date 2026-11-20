"""
Pruebas de la valoración F1 (método 1).

No levantan el catálogo completo: usan una app mínima con un servicio
simulado. Así son rápidas y aíslan el módulo de valoración del clasificador.

Ejecutar:
    python eval\\test_valoracion.py
    pytest eval\\test_valoracion.py -q
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.normalize import normalize_code  # noqa: E402
import app.valuation.router as vr  # noqa: E402
from app.valuation import fx as fxmod  # noqa: E402
from app.valuation.rates import tasas_vigentes  # noqa: E402
from app.valuation.router import router  # noqa: E402

BANK = json.loads((ROOT / "eval" / "valoracion_casos.json").read_text(encoding="utf-8"))


class _Nodo:
    def __init__(self, codigo: str, descripcion: str, av: str):
        self.codigo = codigo
        self.descripcion = descripcion
        self.av = av
        self.is_leaf = True


class _Servicio:
    def __init__(self) -> None:
        self._nodos = {
            normalize_code("6109.10.00.50"): _Nodo(
                "6109.10.00.50", "Camisetas de algodón de punto", "11"
            ),
            normalize_code("8471.60.20.00"): _Nodo(
                "8471.60.20.00", "Teclados, dispositivos por coordenadas x-y", "0"
            ),
            normalize_code("8517.13.00.00"): _Nodo(
                "8517.13.00.00", "Teléfonos inteligentes", "0"
            ),
        }

    def get_code(self, codigo: str):
        return self._nodos.get(normalize_code(codigo))


@pytest.fixture(scope="module", autouse=True)
def _habilitar():
    original = vr.settings
    vr.settings = replace(original, valuation_enabled=True)
    yield
    vr.settings = original


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(router)
    app.state.service = _Servicio()
    with TestClient(app) as c:
        yield c


def _get(data: dict, path: str):
    actual = data
    for parte in path.split("."):
        actual = actual[parte]
    return actual


@pytest.mark.parametrize("caso", BANK["casos"], ids=[c["nombre"] for c in BANK["casos"]])
def test_casos_deterministas(client, caso):
    r = client.post("/v1/valuation/calculate", json=caso["request"])
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["metodo"]["numero"] == 1
    for clave, esperado in caso["esperado"].items():
        obtenido = _get(data, clave)
        assert abs(obtenido - esperado) <= 0.011, f"{clave}: {obtenido} != {esperado}"


def test_codigo_inexistente(client):
    r = client.post("/v1/valuation/calculate", json={
        "codigo": "9999.99.99.99",
        "precio_unitario": 10,
        "cantidad": 1,
    })
    assert r.status_code == 404
    assert r.json()["error"]["codigo"] == "CODIGO_NO_ENCONTRADO"


def test_precio_obligatorio(client):
    r = client.post("/v1/valuation/calculate", json={"codigo": "6109.10.00.50"})
    assert r.status_code == 422


def test_api_version_no_soportada(client):
    r = client.post("/v1/valuation/calculate", json={
        "api_version": "2.0",
        "codigo": "6109.10.00.50",
        "precio_unitario": 10,
        "cantidad": 1,
    })
    assert r.status_code == 409
    assert r.json()["error"]["codigo"] == "API_VERSION_NO_SOPORTADA"


def test_apagado_responde_503(client):
    original = vr.settings
    try:
        vr.settings = replace(original, valuation_enabled=False)
        r = client.post("/v1/valuation/calculate", json={
            "codigo": "6109.10.00.50",
            "precio_unitario": 10,
            "cantidad": 1,
        })
        assert r.status_code == 503
        assert r.json()["error"]["codigo"] == "VALUATION_DISABLED"
    finally:
        vr.settings = original


def test_methods_disponible_apagado(client):
    original = vr.settings
    try:
        vr.settings = replace(original, valuation_enabled=False)
        r = client.get("/v1/valuation/methods")
        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is False
        assert len(data["metodos"]) == 6
        assert [m["numero"] for m in data["metodos"] if m["implementado"]] == [1]
    finally:
        vr.settings = original


def test_fx_fallback_si_falla_la_red(monkeypatch):
    def _falla(*args, **kwargs):
        raise OSError("sin red")

    fxmod.clear_cache()
    monkeypatch.setattr(fxmod.urllib.request, "urlopen", _falla)
    fx = fxmod.get_usd_pen(None, timeout=0.1)
    assert fx.usd_pen > 0
    assert fx.stale is True
    assert "respaldo" in (fx.aviso or "").lower()


def test_fx_manual_tiene_prioridad():
    fx = fxmod.get_usd_pen(Decimal("3.5"))
    assert fx.usd_pen == Decimal("3.5")
    assert fx.fuente == "manual"
    assert fx.stale is False


def test_tabla_de_tasas_por_vigencia():
    assert tasas_vigentes(date(2026, 6, 1))[0].igv == Decimal("0.155")
    assert tasas_vigentes(date(2026, 6, 1))[0].ipm == Decimal("0.025")
    assert tasas_vigentes(date(2027, 6, 1))[0].igv == Decimal("0.15")
    assert tasas_vigentes(date(2028, 6, 1))[0].ipm == Decimal("0.035")
    assert tasas_vigentes(date(2029, 6, 1))[0].igv == Decimal("0.14")
    # El total se mantiene en 18 % en todos los tramos de la Ley 32387.
    for anio in (2026, 2027, 2028, 2029):
        tasa, exacto = tasas_vigentes(date(anio, 6, 1))
        assert exacto is True
        assert tasa.total == Decimal("0.18")


def test_main_monta_el_router_sin_tocar_los_existentes():
    from app.main import app as main_app

    rutas = set(main_app.openapi()["paths"])
    assert "/v1/valuation/calculate" in rutas
    assert "/v1/describe" in rutas
    assert "/v1/classify" in rutas


# --- Origen y preferencia arancelaria -------------------------------------

def test_normalizar_origen():
    from app.valuation.acuerdos import normalizar_origen

    assert normalizar_origen("China") == "CN"
    assert normalizar_origen("  cn ") == "CN"
    assert normalizar_origen("Estados Unidos") == "US"
    assert normalizar_origen("Japón") == "JP"
    assert normalizar_origen("Taiwán") is None
    assert normalizar_origen(None) is None
    assert normalizar_origen("") is None


def test_origen_con_acuerdo_avisa_sin_cambiar_el_calculo(client):
    base = {
        "codigo": "6109.10.00.50",
        "cantidad": 100,
        "precio_unitario": 10,
        "moneda": "USD",
        "condicion": "FOB",
        "ajustes": {"flete": 100, "seguro": 10},
        "fecha_calculo": "2026-11-20",
    }
    sin = client.post("/v1/valuation/calculate", json=base).json()
    con = client.post("/v1/valuation/calculate", json={**base, "origen": "China"}).json()

    # El cálculo no cambia: la preferencia no se aplica sola.
    assert con["tributos"]["ad_valorem"] == sin["tributos"]["ad_valorem"]
    assert con["tributos"]["total"] == sin["tributos"]["total"]

    pref = con["preferencia_arancelaria"]
    assert pref["origen"] == "CN"
    assert pref["puede_aplicar"] is True
    assert "China" in pref["acuerdo"]
    assert pref["ad_valorem_general"] == 11.0
    assert any("tasa GENERAL" in a for a in con["avisos"])
    # Sin origen no se agrega el aviso de preferencia.
    assert not any("tasa GENERAL" in a for a in sin["avisos"])


def test_origen_sin_acuerdo_avisa_igual(client):
    r = client.post("/v1/valuation/calculate", json={
        "codigo": "6109.10.00.50",
        "cantidad": 100,
        "precio_unitario": 10,
        "origen": "TW",
        "fecha_calculo": "2026-11-20",
    })
    assert r.status_code == 200
    pref = r.json()["preferencia_arancelaria"]
    assert pref["origen"] == "TW"
    assert pref["acuerdo"] is None
    assert pref["puede_aplicar"] is False
    assert any("tasa GENERAL" in a for a in r.json()["avisos"])


def test_origen_av_cero_no_genera_aviso_de_preferencia(client):
    r = client.post("/v1/valuation/calculate", json={
        "codigo": "8471.60.20.00",
        "cantidad": 10,
        "precio_unitario": 100,
        "origen": "CN",
        "fecha_calculo": "2026-11-20",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["preferencia_arancelaria"]["puede_aplicar"] is False
    assert not any("tasa GENERAL" in a for a in data["avisos"])


def test_acuerdos_conocidos():
    from app.valuation.acuerdos import acuerdo_de

    assert acuerdo_de("CN") is not None
    assert acuerdo_de("US") is not None
    assert acuerdo_de("DE") is not None  # Unión Europea
    assert acuerdo_de("TW") is None
    assert acuerdo_de(None) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
