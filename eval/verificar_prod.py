"""
Verificacion de produccion del backend arancel-api.

Comprueba que la API este viva, que la clasificacion siga intacta y, si la
valoracion esta encendida, que devuelva exactamente los numeros de la DAM
real ya validada (118-2024-10-458884).

Uso:
    python eval\\verificar_prod.py
    python eval\\verificar_prod.py --sin-clasificar   (no consume Groq false)
    python eval\\verificar_prod.py --url https://...

Codigo de salida 0 = todo bien, 1 = algo falla.
"""
from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
import json

# Caso validado contra DAM real (tolerancia 0.01).
DAM = {
    "codigo": "7010.90.30.00",
    "origen": "CN",
    "cantidad": 30000,
    "precio_unitario": 0.1255,
    "moneda": "USD",
    "condicion": "FOB",
    "ajustes": {"flete": 260.0, "seguro": 37.65},
    "fecha_calculo": "2024-12-28",
    "av_pct_forzar": 0,
}

OFICIAL = {
    "cif": 4062.65,
    "ad_valorem": 0.00,
    "igv": 650.02,
    "ipm": 81.25,
    "total": 731.27,
}

# Produccion NO aplica preferencias arancelarias: usa la tasa general del
# catalogo y avisa. Para este codigo (China, av 6 %) lo correcto es cobrar
# el escenario general, no el de la DAM (que tenia 0 % por el TLC).
ESPERADO_GENERAL = {
    "cif": 4062.65,
    "ad_valorem": 243.76,
    "igv": 689.03,
    "ipm": 86.13,
    "total": 1018.92,
}


def call(base: str, method: str, path: str, body: dict | None = None,
         timeout: int = 120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Content-Type": "application/json", "User-Agent": "verificar-prod"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        crudo = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(crudo)
        except Exception:
            return e.code, {"raw": crudo[:300]}
    except Exception as e:  # noqa: BLE001
        return None, {"error_red": str(e)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://arancel-api-q3o9.onrender.com")
    ap.add_argument("--sin-clasificar", action="store_true",
                    help="Omite la prueba de clasificacion (no consume LLM).")
    ap.add_argument("--referencia", action="store_true",
                    help="Prueba la busqueda de precios (consume 1 credito de Brave).")
    args = ap.parse_args()
    base = args.url.rstrip("/")
    fallos = 0

    print(f"== {base} ==")

    # 1. Salud
    estado, h = call(base, "GET", "/healthz")
    if estado != 200:
        print(f"[1] /healthz -> {estado} {h}")
        return 1
    print(f"[1] /healthz 200 | status={h.get('status')} | "
          f"hojas={h.get('catalogo', {}).get('total_leaves')} | "
          f"mock={h.get('modo_simulado')}")
    print(f"    valuation = {h.get('valuation')}")

    # 2. Catalogo de metodos (funciona con el flag apagado)
    estado, m = call(base, "GET", "/v1/valuation/methods")
    impl = [x["numero"] for x in m.get("metodos", []) if x.get("implementado")]
    print(f"[2] /v1/valuation/methods {estado} | enabled={m.get('enabled')} | "
          f"implementados={impl}")
    if estado != 200:
        fallos += 1

    # 3. Valoracion con la DAM real
    if h.get("valuation") is True:
        body = {k: v for k, v in DAM.items() if k != "av_pct_forzar"}
        estado, v = call(base, "POST", "/v1/valuation/calculate", body)
        if estado != 200:
            print(f"[3] /v1/valuation/calculate -> {estado} {v}")
            fallos += 1
        else:
            motor = {
                "cif": v["total"]["cif"],
                "ad_valorem": v["tributos"]["ad_valorem"],
                "igv": v["tributos"]["igv"],
                "ipm": v["tributos"]["ipm"],
                "total": v["tributos"]["total"],
            }
            print("[3] /v1/valuation/calculate 200")
            for k, esperado in ESPERADO_GENERAL.items():
                obtenido = motor[k]
                delta = obtenido - esperado
                marca = "OK  " if abs(delta) <= 0.011 else "DIFF"
                if marca == "DIFF":
                    fallos += 1
                print(f"    {marca} {k:<12} {obtenido:>12,.2f}  esperado {esperado:>10,.2f}")

            # El CIF es independiente del arancel: debe coincidir con la DAM.
            if abs(motor["cif"] - OFICIAL["cif"]) > 0.011:
                print(f"    !! CIF no coincide con la DAM ({OFICIAL['cif']:,.2f})")
                fallos += 1

            pref = v.get("preferencia_arancelaria", {})
            print(f"    aviso preferencia: {pref.get('acuerdo')} "
                  f"(puede_aplicar={pref.get('puede_aplicar')})")
            if pref.get("puede_aplicar") is not True:
                print("    !! se esperaba el aviso del acuerdo Peru-China")
                fallos += 1
            print(f"    referencia: con preferencia TLC la DAM liquido "
                  f"{OFICIAL['total']:,.2f}")

            # Sin origen declarado no debe aparecer el aviso.
            sin_origen = {k: val for k, val in body.items() if k != "origen"}
            estado2, v2 = call(base, "POST", "/v1/valuation/calculate", sin_origen)
            if estado2 == 200:
                pref2 = v2.get("preferencia_arancelaria", {})
                print(f"    sin origen: puede_aplicar={pref2.get('puede_aplicar')} "
                      f"(se esperaba False)")
                if pref2.get("puede_aplicar") is not False:
                    fallos += 1
    else:
        print("[3] valoracion APAGADA: se omite el calculo")
        estado, v = call(base, "POST", "/v1/valuation/calculate", {"codigo": DAM["codigo"],
                                                                  "cantidad": 1,
                                                                  "precio_unitario": 1})
        if estado != 503:
            print(f"    !! con el flag apagado se esperaba 503 y llego {estado}")
            fallos += 1
        else:
            print(f"    503 controlado: {v.get('error', {}).get('codigo')}")

    # 4. La clasificacion sigue viva
    if not args.sin_clasificar:
        estado, c = call(base, "POST", "/v1/classify", {
            "descripcion_ia": "botellas de vidrio de 30 ml con gotero para cosmeticos",
            "descripcion_usuario": "frasco de vidrio pequeno",
            "contexto": {},
            "top_k": 25,
            "verificar": False,
        }, timeout=180)
        if estado != 200:
            print(f"[4] /v1/classify -> {estado} {c}")
            fallos += 1
        else:
            r = c.get("resultado", {})
            print(f"[4] /v1/classify 200 | {r.get('codigo')} | "
                  f"confianza={c.get('confianza')} | nivel={r.get('nivel')}")

    # 5. Referencia de precios (opcional: consume 1 credito de Brave)
    ref_info = (h.get("valuation_info") or {}).get("reference_search") or {}
    if args.referencia:
        if not ref_info.get("enabled"):
            print("[5] referencia APAGADA (REFERENCE_SEARCH_ENABLED=false)")
        elif not ref_info.get("configurada"):
            print("[5] referencia sin BRAVE_API_KEY")
            fallos += 1
        else:
            estado, ref = call(base, "POST", "/v1/valuation/reference", {
                "consulta": "termo electrico 1.5 litros",
                "codigo": "8516.10.00.00",
                "tipo_cambio": 3.5,
            }, timeout=180)
            print(f"[5] /v1/valuation/reference -> {estado}")
            if estado != 200:
                print(f"    {json.dumps(ref)[:240]}")
                fallos += 1
            else:
                rango = ref.get("rango")
                print(f"    rango: {rango}")
                print(f"    fuentes: {ref.get('fuentes')}")
                print(f"    cupo: {ref.get('cupo')}")
                est = ref.get("estimacion_aduanera")
                if est:
                    print(f"    estimacion metodo 4: "
                          f"US$ {est['valor_deducido_unitario_usd']} /unid.")
                if not rango:
                    print("    (sin precios utilizables en esta consulta)")
    else:
        print(f"[5] referencia: enabled={ref_info.get('enabled')} "
              f"configurada={ref_info.get('configurada')} "
              f"(usa --referencia para probarla)")

    print("\nRESULTADO:", "TODO OK" if fallos == 0 else f"{fallos} PROBLEMAS")
    return 0 if fallos == 0 else 1

if __name__ == "__main__":
    raise SystemExit(main())
