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
        assert [m["numero"] for m in data["metodos"] if m["implementado"]] == [1, 4]
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


# --- Método 4: valor deductivo --------------------------------------------

DEDUCTIVO = {
    "api_version": "1.1",
    "codigo": "6109.10.00.50",
    "cantidad": 100,
    "precio_venta_unitario": 59.90,
    "moneda": "PEN",
    "incluye_igv": True,
    "margen_pct": 35,
    "gastos_internos": 0,
    "igv_acreditable": True,
    "tipo_cambio": 3.5,
    "fecha_calculo": "2026-11-20",
}


def test_deductivo_caso_concreto(client):
    r = client.post("/v1/valuation/deductive", json=DEDUCTIVO)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["metodo"]["numero"] == 4
    assert d["metodo"]["nombre"] == "Valor deductivo"
    assert d["valor_deducido"]["total_usd"] == pytest.approx(967.88, abs=0.011)
    assert d["valor_deducido"]["unitario_usd"] == pytest.approx(9.68, abs=0.011)
    t = d["equivalente"]["tributos"]
    assert t["ad_valorem"] == pytest.approx(106.47, abs=0.011)
    assert t["igv"] == pytest.approx(166.52, abs=0.011)
    assert t["ipm"] == pytest.approx(26.86, abs=0.011)
    assert t["total"] == pytest.approx(299.85, abs=0.011)


def test_deductivo_reconstruye_el_precio_de_venta(client):
    """Invariante: CIF × TC × (1+av) × (1+margen) × (1+IGV/IPM) = precio venta."""
    d = client.post("/v1/valuation/deductive", json=DEDUCTIVO).json()
    cif = d["valor_deducido"]["total_usd"]
    reconstruido = cif * 3.5 * (1 + 0.11) * (1 + 0.35) * (1 + 0.18)
    assert reconstruido == pytest.approx(5990.0, abs=0.60)


def test_deductivo_compara_con_el_precio_declarado(client):
    d = client.post(
        "/v1/valuation/deductive",
        json={**DEDUCTIVO, "precio_declarado_unitario": 5.0},
    ).json()
    c = d["comparacion"]
    assert c["muy_por_debajo"] is True
    assert c["muy_por_encima"] is False
    assert c["ratio_pct"] == pytest.approx(51.66, abs=0.5)
    assert any("por debajo del valor deductivo" in a for a in d["avisos"])


def test_deductivo_sin_credito_fiscal_da_menor_valor(client):
    con = client.post("/v1/valuation/deductive", json=DEDUCTIVO).json()
    sin = client.post(
        "/v1/valuation/deductive",
        json={**DEDUCTIVO, "igv_acreditable": False},
    ).json()
    assert sin["valor_deducido"]["total_usd"] < con["valor_deducido"]["total_usd"]
    assert any("NO es acreditable" in a for a in sin["avisos"])


def test_deductivo_ad_valorem_cero(client):
    d = client.post(
        "/v1/valuation/deductive",
        json={**DEDUCTIVO, "codigo": "8471.60.20.00"},
    ).json()
    assert d["equivalente"]["tributos"]["ad_valorem"] == 0.0
    # Sin ad valorem el valor en aduana implícito sube.
    assert d["valor_deducido"]["total_usd"] > 1000


def test_deductivo_validaciones(client):
    r = client.post("/v1/valuation/deductive", json={**DEDUCTIVO, "precio_venta_unitario": 0})
    assert r.status_code == 422
    r = client.post("/v1/valuation/deductive", json={**DEDUCTIVO, "codigo": "9999.99.99.99"})
    assert r.status_code == 404
    assert r.json()["error"]["codigo"] == "CODIGO_NO_ENCONTRADO"


def test_deductivo_apagado(client):
    original = vr.settings
    try:
        vr.settings = replace(original, valuation_enabled=False)
        r = client.post("/v1/valuation/deductive", json=DEDUCTIVO)
        assert r.status_code == 503
        assert r.json()["error"]["codigo"] == "VALUATION_DISABLED"
    finally:
        vr.settings = original


def test_methods_marca_el_metodo_4(client):
    d = client.get("/v1/valuation/methods").json()
    impl = [m["numero"] for m in d["metodos"] if m["implementado"]]
    assert impl == [1, 4]


# --- Precios de referencia desde búsqueda web -----------------------------

class _ProveedorFalso:
    def __init__(self, resultados):
        self._resultados = resultados
        self.llamadas = 0

    def buscar(self, consulta, cantidad):
        self.llamadas += 1
        return self._resultados


def _resultados(*textos):
    from app.valuation.reference import ResultadoBusqueda

    return [
        ResultadoBusqueda(titulo=t, descripcion="", url="https://tienda.pe/x")
        for t in textos
    ]


def test_extraer_precios_formatos():
    from app.valuation.reference import extraer_precios, ResultadoBusqueda

    res = [ResultadoBusqueda(
        titulo="Termo S/ 59,90 · otro S/. 1.299,00 · oferta S/ 89.90",
        descripcion="desde PEN 45,50 y US$ 12.99",
        url="https://x.pe",
    )]
    soles, dolares = extraer_precios(res)
    assert 59.90 in soles or any(abs(float(s) - 59.9) < 0.01 for s in soles)
    assert any(abs(float(s) - 1299.0) < 0.01 for s in soles)
    assert any(abs(float(s) - 89.90) < 0.01 for s in soles)
    assert any(abs(float(d) - 12.99) < 0.01 for d in dolares)


def test_resumir_descarta_valores_atipicos():
    from decimal import Decimal as D
    from app.valuation.reference import resumir

    # Un accesorio de S/ 5 entre termos de ~S/ 80 no debe arrastrar la mediana.
    r = resumir([D("75"), D("80"), D("85"), D("90"), D("5")])
    assert r is not None
    assert r["muestras"] == 4
    assert r["descartados"] == 1
    assert 75 <= r["mediana"] <= 90


def test_resumir_exige_muestras():
    from decimal import Decimal as D
    from app.valuation.reference import resumir

    assert resumir([D("80")]) is None
    assert resumir([]) is None


def _habilitar_referencia(monkeypatch=None):
    from dataclasses import replace as _replace
    import app.valuation.router as _vr

    return _replace(
        _vr.settings,
        valuation_enabled=True,
        reference_search_enabled=True,
        brave_api_key="clave-de-prueba",
        reference_monthly_limit=990,
    )


def test_reference_devuelve_rango(client):
    import app.valuation.reference as ref
    import app.valuation.router as vr
    from dataclasses import replace

    original = vr.settings
    ref.reiniciar_cupo()
    ref.limpiar_transitorio()
    proveedor = _ProveedorFalso(_resultados(
        "Termo electrico 1.5L S/ 79,90",
        "Termo electrico acero S/ 85,00",
        "Termo electrico S/ 82.50 envio gratis",
        "Repuesto tapa S/ 12,00",
    ))
    try:
        ref.set_provider(proveedor)
        vr.settings = replace(original, valuation_enabled=True,
                              reference_search_enabled=True,
                              brave_api_key="x", reference_monthly_limit=990)
        r = client.post("/v1/valuation/reference", json={"consulta": "termo electrico 1.5l"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["referencial"] is True
        assert d["moneda"] == "PEN"
        assert d["rango"]["muestras"] >= 2
        assert 70 <= d["rango"]["mediana"] <= 90
        assert d["cupo"]["usadas"] == 1
        assert proveedor.llamadas == 1
    finally:
        vr.settings = original
        ref.set_provider(ref.NullProvider())
        ref.reiniciar_cupo()


def test_reference_usa_transitorio_para_evitar_doble_cobro(client):
    import app.valuation.reference as ref
    import app.valuation.router as vr
    from dataclasses import replace

    original = vr.settings
    ref.reiniciar_cupo()
    ref.limpiar_transitorio()
    proveedor = _ProveedorFalso(_resultados("Termo S/ 79,90", "Termo S/ 85,00"))
    try:
        ref.set_provider(proveedor)
        vr.settings = replace(original, valuation_enabled=True,
                              reference_search_enabled=True, brave_api_key="x")
        body = {"consulta": "termo electrico 1.5l"}
        client.post("/v1/valuation/reference", json=body)
        client.post("/v1/valuation/reference", json=body)
        assert proveedor.llamadas == 1  # solo una llamada real en 120 s
    finally:
        vr.settings = original
        ref.set_provider(ref.NullProvider())
        ref.reiniciar_cupo()
        ref.limpiar_transitorio()


def test_reference_apagada_503(client):
    import app.valuation.router as vr
    from dataclasses import replace

    original = vr.settings
    try:
        vr.settings = replace(original, valuation_enabled=True,
                              reference_search_enabled=False)
        r = client.post("/v1/valuation/reference", json={"consulta": "termo electrico"})
        assert r.status_code == 503
        assert r.json()["error"]["codigo"] == "REFERENCE_DISABLED"
    finally:
        vr.settings = original


def test_reference_sin_clave_503(client):
    import app.valuation.reference as ref
    import app.valuation.router as vr
    from dataclasses import replace

    original = vr.settings
    try:
        ref.set_provider(ref.NullProvider())
        vr.settings = replace(original, valuation_enabled=True,
                              reference_search_enabled=True, brave_api_key="")
        r = client.post("/v1/valuation/reference", json={"consulta": "termo electrico"})
        assert r.status_code == 503
        assert r.json()["error"]["codigo"] == "REFERENCE_UNAVAILABLE"
    finally:
        vr.settings = original


def test_reference_limite_mensual(client):
    import app.valuation.reference as ref
    import app.valuation.router as vr
    from dataclasses import replace

    original = vr.settings
    ref.reiniciar_cupo()
    ref.limpiar_transitorio()
    proveedor = _ProveedorFalso(_resultados("Termo S/ 79,90", "Termo S/ 85,00"))
    try:
        ref.set_provider(proveedor)
        vr.settings = replace(original, valuation_enabled=True,
                              reference_search_enabled=True, brave_api_key="x",
                              reference_monthly_limit=1)
        r1 = client.post("/v1/valuation/reference", json={"consulta": "termo a"})
        assert r1.status_code == 200
        r2 = client.post("/v1/valuation/reference", json={"consulta": "termo b"})
        assert r2.status_code == 429
        assert r2.json()["error"]["codigo"] == "REFERENCE_LIMIT_REACHED"
    finally:
        vr.settings = original
        ref.set_provider(ref.NullProvider())
        ref.reiniciar_cupo()
        ref.limpiar_transitorio()


def test_reference_estima_valor_en_aduana(client):
    import app.valuation.reference as ref
    import app.valuation.router as vr
    from dataclasses import replace

    original = vr.settings
    ref.reiniciar_cupo()
    ref.limpiar_transitorio()
    proveedor = _ProveedorFalso(_resultados(
        "Camiseta algodon S/ 59,90", "Camiseta algodon S/ 62,00"))
    try:
        ref.set_provider(proveedor)
        vr.settings = replace(original, valuation_enabled=True,
                              reference_search_enabled=True, brave_api_key="x")
        r = client.post("/v1/valuation/reference", json={
            "consulta": "camiseta algodon",
            "codigo": "6109.10.00.50",
            "tipo_cambio": 3.5,
            "cantidad": 100,
        })
        assert r.status_code == 200, r.text
        est = r.json()["estimacion_aduanera"]
        assert est is not None
        assert est["metodo"] == 4
        assert est["valor_deducido_unitario_usd"] > 0
    finally:
        vr.settings = original
        ref.set_provider(ref.NullProvider())
        ref.reiniciar_cupo()
        ref.limpiar_transitorio()


def test_brave_reintenta_sin_localizacion(monkeypatch):
    """Si Brave rechaza country/search_lang, baja escalón hasta que funcione."""
    from app.valuation.reference import BraveProvider, _HttpError

    BraveProvider._combo_desde = 0
    prov = BraveProvider("clave")
    llamadas = []

    def fake_get(params):
        llamadas.append(dict(params))
        if "country" in params:
            raise _HttpError(422, '{"error":{"detail":"country: invalid value"}}')
        return {"web": {"results": [
            {"title": "Termo S/ 79,90", "description": "", "url": "https://x.pe"},
        ]}}

    monkeypatch.setattr(prov, "_get", fake_get)
    res = prov.buscar("termo electrico", 10)
    assert len(res) == 1
    # Dos intentos con country fallan; el tercero (solo search_lang) funciona.
    assert len(llamadas) == 3
    assert "country" not in llamadas[-1]
    assert llamadas[-1]["q"] == "termo electrico"
    BraveProvider._combo_desde = 0


def test_brave_traduce_errores(monkeypatch):
    from app.valuation.reference import (
        BraveProvider, ReferenceLimitReached, ReferenceUnavailable, _HttpError,
    )

    prov = BraveProvider("clave")
    for codigo, esperado in (
        (401, ReferenceUnavailable),
        (402, ReferenceLimitReached),
        (429, ReferenceLimitReached),
        (500, ReferenceUnavailable),
    ):
        def fake_get(_params, c=codigo):
            raise _HttpError(c, "detalle de error")

        monkeypatch.setattr(prov, "_get", fake_get)
        try:
            prov.buscar("x", 5)
            raise AssertionError(f"{codigo} debió lanzar")
        except esperado:
            pass


def test_reference_ignora_tiendas_de_otros_paises(client):
    """Un precio de MercadoLibre Chile no debe entrar como si fueran soles."""
    import app.valuation.reference as ref
    import app.valuation.router as vr
    from dataclasses import replace
    from app.valuation.reference import ResultadoBusqueda

    original = vr.settings
    ref.reiniciar_cupo()
    ref.limpiar_transitorio()
    extranjeros = [
        ResultadoBusqueda("Termo $45.000", "", "https://listado.mercadolibre.cl/x"),
        ResultadoBusqueda("Termo $67.500", "", "https://www.idealo.es/y"),
        ResultadoBusqueda("Termo S/ 79,90", "", "https://www.falabella.com.pe/z"),
        ResultadoBusqueda("Termo S/ 85,00", "", "https://www.ripley.com.pe/w"),
    ]
    proveedor = _ProveedorFalso(extranjeros)
    try:
        ref.set_provider(proveedor)
        vr.settings = replace(original, valuation_enabled=True,
                              reference_search_enabled=True, brave_api_key="x")
        r = client.post("/v1/valuation/reference", json={"consulta": "termo electrico"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["resultados_ignorados"] == 2
        assert d["fuentes"] == ["www.falabella.com.pe", "www.ripley.com.pe"]
        assert d["rango"] is not None
        assert d["rango"]["muestras"] == 2
        assert d["rango"]["max"] < 1000  # ningún peso chileno se coló
    finally:
        vr.settings = original
        ref.set_provider(ref.NullProvider())
        ref.reiniciar_cupo()
        ref.limpiar_transitorio()


def test_reference_sin_tiendas_peruanas(client):
    import app.valuation.reference as ref
    import app.valuation.router as vr
    from dataclasses import replace
    from app.valuation.reference import ResultadoBusqueda

    original = vr.settings
    ref.reiniciar_cupo()
    ref.limpiar_transitorio()
    proveedor = _ProveedorFalso([
        ResultadoBusqueda("Termo $45.000", "", "https://listado.mercadolibre.cl/x"),
        ResultadoBusqueda("Termo $67.500", "", "https://www.idealo.es/y"),
    ])
    try:
        ref.set_provider(proveedor)
        vr.settings = replace(original, valuation_enabled=True,
                              reference_search_enabled=True, brave_api_key="x")
        d = client.post("/v1/valuation/reference",
                        json={"consulta": "termo electrico"}).json()
        assert d["rango"] is None
        assert "tiendas peruanas" in d["aviso"]
    finally:
        vr.settings = original
        ref.set_provider(ref.NullProvider())
        ref.reiniciar_cupo()
        ref.limpiar_transitorio()


def test_reference_acepta_precios_en_dolares(client):
    """Una tienda peruana puede publicar en USD: debe servir igual."""
    import app.valuation.reference as ref
    import app.valuation.router as vr
    from dataclasses import replace
    from app.valuation.reference import ResultadoBusqueda

    original = vr.settings
    ref.reiniciar_cupo()
    ref.limpiar_transitorio()
    proveedor = _ProveedorFalso([
        ResultadoBusqueda("Camiseta algodon US$ 16.90", "", "https://www.falabella.com.pe/a"),
        ResultadoBusqueda("Camiseta algodon US$ 18.00", "", "https://www.ripley.com.pe/b"),
    ])
    try:
        ref.set_provider(proveedor)
        vr.settings = replace(original, valuation_enabled=True,
                              reference_search_enabled=True, brave_api_key="x")
        r = client.post("/v1/valuation/reference", json={
            "consulta": "camiseta algodon",
            "codigo": "6109.10.00.50",
            "cantidad": 100,
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["moneda"] == "USD"
        assert d["rangos"]["PEN"] is None
        assert d["rangos"]["USD"] is not None
        est = d["estimacion_aduanera"]
        assert est is not None
        assert est["moneda_precio_venta"] == "USD"
        assert est["valor_deducido_unitario_usd"] > 0
    finally:
        vr.settings = original
        ref.set_provider(ref.NullProvider())
        ref.reiniciar_cupo()
        ref.limpiar_transitorio()


def test_reference_devuelve_ambas_monedas(client):
    import app.valuation.reference as ref
    import app.valuation.router as vr
    from dataclasses import replace
    from app.valuation.reference import ResultadoBusqueda

    original = vr.settings
    ref.reiniciar_cupo()
    ref.limpiar_transitorio()
    proveedor = _ProveedorFalso([
        ResultadoBusqueda("Termo S/ 79,90", "", "https://www.falabella.com.pe/a"),
        ResultadoBusqueda("Termo S/ 85,00", "", "https://www.ripley.com.pe/b"),
        ResultadoBusqueda("Termo US$ 22.00", "", "https://www.coolbox.pe/c"),
        ResultadoBusqueda("Termo US$ 24.00", "", "https://www.hiraoka.com.pe/d"),
    ])
    try:
        ref.set_provider(proveedor)
        vr.settings = replace(original, valuation_enabled=True,
                              reference_search_enabled=True, brave_api_key="x")
        d = client.post("/v1/valuation/reference",
                        json={"consulta": "termo electrico"}).json()
        assert d["moneda"] == "PEN"  # se prefiere la moneda local
        assert d["rangos"]["PEN"]["muestras"] == 2
        assert d["rangos"]["USD"]["muestras"] == 2
    finally:
        vr.settings = original
        ref.set_provider(ref.NullProvider())
        ref.reiniciar_cupo()
        ref.limpiar_transitorio()


def test_consulta_con_contexto_peru():
    from app.valuation.reference import _con_contexto_peru

    assert _con_contexto_peru("termo electrico") == "termo electrico Perú"
    assert _con_contexto_peru("termo electrico Perú") == "termo electrico Perú"
    assert _con_contexto_peru("termo electrico peru") == "termo electrico peru"
    assert _con_contexto_peru("  termo   electrico  ") == "termo electrico Perú"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
