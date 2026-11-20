# Valoración aduanera — Fase F1 (método 1)

**Estado:** implementado, **apagado por defecto** (`VALUATION_ENABLED=false`).
No modifica `/v1/describe` ni `/v1/classify`. No requiere APK nueva para desplegarse.

---

## 1. Alcance

| Incluido en F1 | Fuera de F1 |
|---|---|
| Método 1: valor de transacción | Métodos 2 a 6 |
| Ajustes del Art. 8 (flete, seguro, comisiones, etc.) | Búsqueda web / Brave Search |
| CIF en USD | Valor deductivo desde retail |
| Ad valorem desde el catálogo | ISC, antidumping, salvaguardias |
| IGV + IPM con vigencia (Ley 32387) | Percepción del IGV |
| Tipo de cambio con respaldo | UI en la APK |

---

## 2. Activación

```yaml
# render.yaml
- key: VALUATION_ENABLED
  value: "false"   # cambiar a "true" y hacer Manual sync
```

La APK debe consultar `GET /healthz` y mirar `valuation` (booleano). Si es
`false`, no muestra la sección de valor.

---

## 3. Endpoints

### `GET /v1/valuation/methods`
Funciona incluso apagado. Devuelve los 6 métodos y cuáles están implementados.

### `POST /v1/valuation/calculate`

```json
{
  "api_version": "1.0",
  "codigo": "6109.10.00.50",
  "descripcion": "Camisetas de algodón",
  "cantidad": 100,
  "precio_unitario": 10,
  "moneda": "USD",
  "condicion": "FOB",
  "ajustes": {
    "flete": 100,
    "seguro": 10,
    "comisiones": 0,
    "corretaje": 0,
    "envases": 0,
    "regalias": 0,
    "asistencia": 0,
    "otros": 0
  }
}
```

- `moneda`: `USD` o `PEN`.
- `condicion`: `FOB` (por defecto), `CIF` o `EXW`.
- `ajustes`: **totales de la operación**, en la misma moneda del precio.
- `tipo_cambio`: opcional (PEN por USD); si falta, se consulta en línea.
- `fecha_calculo`: opcional, solo para simular tasas de otra fecha.

Respuesta (resumida):

```json
{
  "ok": true,
  "api_version": "1.0",
  "codigo": "6109.10.00.50",
  "metodo": { "numero": 1, "nombre": "Valor de transacción", "grado": "estricto" },
  "tipo_cambio": { "usd_pen": null, "fuente": "no_requerido" },
  "unitario": { "precio": 10.0, "flete": 1.0, "seguro": 0.1, "cif": 11.1 },
  "total": { "cantidad": 100, "valor": 1000.0, "cif": 1110.0 },
  "tributos": {
    "av_pct": 11.0,
    "ad_valorem": 122.1,
    "igv": 190.98,
    "ipm": 30.8,
    "total": 343.88
  },
  "avisos": [],
  "aviso_legal": "Estimación referencial..."
}
```

Errores controlados (siempre JSON, nunca excepción):

| HTTP | Código | Cuándo |
|---|---|---|
| 404 | `CODIGO_NO_ENCONTRADO` | El código no existe |
| 400 | `CODIGO_NO_CLASIFICABLE` | No es una hoja de 10 dígitos |
| 422 | `ENTRADA_INVALIDA` | Cantidad/precio inválidos |
| 409 | `API_VERSION_NO_SOPORTADA` | Versión mayor distinta de 1 |
| 503 | `VALUATION_DISABLED` | Flag apagado |
| 503 | `FX_NO_DISPONIBLE` | PEN sin tipo de cambio ni respaldo |
| 500 | `ERROR_INTERNO` | Cualquier fallo inesperado (aislado) |

---

## 4. Fórmulas

```
valor_total   = precio_unitario × cantidad
CIF           = valor_total + flete + seguro + ajustes Art. 8   (FOB/EXW)
CIF           = valor_total + ajustes Art. 8                    (CIF)

ad_valorem    = CIF × av% / 100
base_igv      = CIF + ad_valorem
IGV           = base_igv × tasa_igv
IPM           = base_igv × tasa_ipm
total         = ad_valorem + IGV + IPM
```

Redondeo: `Decimal`, `ROUND_HALF_UP`, 2 decimales.

---

## 5. Tasas por vigencia

Fuente: **Ley N° 32387** (publicada el 16/06/2025, vigente desde 01/01/2026).

| Período | IGV | IPM | Total |
|---|---|---|---|
| Hasta 2025-12-31 | 16.0 % | 2.0 % | 18 % |
| 2026 | 15.5 % | 2.5 % | 18 % |
| 2027 | 15.0 % | 3.0 % | 18 % |
| 2028 | 14.5 % | 3.5 % | 18 % |
| Desde 2029 | 14.0 % | 4.0 % | 18 % |

La tabla vive en `backend/app/valuation/rates.py`. **No hardcodear tasas en
otro lugar.** Antes de producción, contrastar con SUNAT.

---

## 6. Tipo de cambio

1. `tipo_cambio` manual del request.
2. `open.er-api.com` (timeout 4 s, caché 12 h en memoria).
3. `backend/app/valuation/fx_fallback.json` (marcado como `stale`).
4. Si nada funciona y la operación es en PEN → `503 FX_NO_DISPONIBLE`.

Nunca bloquea una operación en USD.

---

## 7. Validación con una DAM real

Cuando tengas una DAM liquidada:

1. Toma el **valor FOB**, flete y seguro de la factura/DAM.
2. Envía el código, cantidad y precio al endpoint.
3. Compara:
   - `total.cif` contra el valor en aduana de la DAM.
   - `tributos.ad_valorem`, `igv`, `ipm` y `total` contra la liquidación.
4. Si hay diferencia, revisa:
   - tasa `av` del código,
   - inclusiones/exclusiones del Art. 8,
   - fecha de cálculo (tasas IGV/IPM),
   - redondeo.
5. Agrega el caso a `eval/valoracion_casos.json` con los valores oficiales.

**Ningún caso se considera validado hasta compararlo con una DAM real.**

---

## 8. Rollback

- Código: la rama `feature/valoracion-f1` no está en `main`; `main` sigue igual.
- Producción: si se activa y falla, poner `VALUATION_ENABLED=false` y hacer
  *Manual sync*. El endpoint responde 503 y el clasificador no se toca.
- Commit estable de referencia: anotar el hash de `main` antes del merge.

---

## 9. Pruebas

```powershell
python eval\test_valoracion.py       # 14 pruebas
python eval\smoke_test.py
python eval\test_equivalencias.py
python eval\test_segunda_busqueda.py
python eval\test_llm_compat.py
python eval\evaluate_retrieval.py    # 12/12
```
