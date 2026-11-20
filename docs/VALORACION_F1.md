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

Herramienta lista: `eval/validar_dam.py` + plantilla `eval/dam_casos.json`.

**Qué copiar de la DAM (DUA):**

| Campo en `dam_casos.json` | De dónde sale |
|---|---|
| `codigo` | Subpartida nacional declarada |
| `cantidad`, `precio_unitario_fob` | Factura comercial |
| `flete_usd`, `seguro_usd` | Cuadro de liquidación (solo tramo internacional) |
| `fecha_despacho` | Fecha de numeración de la DAM (define las tasas IGV/IPM) |
| `oficial_dam.cif_dam` | Casilla 11.15 "Valor en Aduana" |
| `oficial_dam.av_pct_dam` | Ad valorem declarado, en % |
| `oficial_dam.ad_valorem_dam` / `igv_dam` / `ipm_dam` | Columnas de la liquidación |
| `oficial_dam.total_tributos_dam` | Total de tributos (sin percepción) |
| `oficial_dam.percepcion_dam` | Columna de percepción, si existe (informativa) |

**Cómo ejecutarlo:**

```powershell
python eval\validar_dam.py
python eval\validar_dam.py --tolerancia 0.50
```

Solo evalúa los casos con `"estado": "validado"`. Los `pendiente` se ignoran,
así que la plantilla puede quedarse en el repo.

Qué revisa:

- `cif`, `ad_valorem`, `igv`, `ipm` y `total` contra la DAM (± 1.00 USD por defecto).
- Que el `av` del catálogo coincida con el de la DAM. Si no coincide, es señal
  de clasificación equivocada o de arancel desactualizado, no de redondeo.
- FOB, flete y seguro de entrada, porque explican casi todas las diferencias.

Si algo falla, el orden de sospecha es:

1. `av` del código (clasificación o vigencia del arancel).
2. Flete/seguro: ¿incluye tramo interno o solo el internacional?
3. Art. 8: ¿hay comisiones, regalías, envases o asistencia no declarados?
4. Fecha: ¿las tasas IGV/IPM corresponden al año del despacho?
5. Redondeo (lo último; el motor usa `ROUND_HALF_UP` a 2 decimales).

**Ningún caso se considera validado hasta compararlo con una DAM real.**

### Resultado: F1 validada con DAM real

**DAM 118-2024-10-458884-01-0-00** (Marítima del Callao, 28/12/2024),
botellas de vidrio 30 ml con gotero, origen China, partida 7010.90.30.00.

| Concepto | Motor | DAM | Δ |
|---|---|---|---|
| FOB | 3 765.00 | 3 765.00 | 0.00 |
| Flete | 260.00 | 260.00 | 0.00 |
| Seguro | 37.65 | 37.65 | 0.00 |
| **Valor en aduana** | **4 062.65** | **4 062.65** | **0.00** |
| Ad valorem | 0.00 | 0.00 | 0.00 |
| IGV | 650.02 | 650.02 | 0.00 |
| IPM | 81.25 | 81.25 | 0.00 |
| **Total tributos** | **731.27** | **731.27** | **0.00** |
| Percepción IGV 3.5 % | 167.79 | 167.79 | 0.00 |

Nueve cifras, cero diferencia. El motor también eligió solo las tasas
**16 % / 2 %** por la fecha de 2024.

**Hallazgo:** el catálogo dice `av = 6 %` (tasa general/MFN) pero la aduana
aplicó **0 %** por el acuerdo comercial **Perú–China**. Es decir, el ad valorem
real depende del origen y del acuerdo vigente, no solo del código. Ver F1.1.

**Percepción confirmada:** `3.5 % × (CIF + IGV + IPM)`. Queda documentada para F4,
aunque la F1 no la calcula.

---

## 8. Rollback

- Código: la rama `feature/valoracion-f1` no está en `main`; `main` sigue igual.
- Producción: si se activa y falla, poner `VALUATION_ENABLED=false` y hacer
  *Manual sync*. El endpoint responde 503 y el clasificador no se toca.
- Commit estable de referencia: anotar el hash de `main` antes del merge.

---

## 9. Pruebas

```powershell
python eval\test_valoracion.py       # 15 pruebas
python eval\smoke_test.py
python eval\test_equivalencias.py
python eval\test_segunda_busqueda.py
python eval\test_llm_compat.py
python eval\evaluate_retrieval.py    # 12/12
```

---

## 10. Pendiente para F1.1

Ideas evaluadas y aceptadas, todavía **sin implementar**:

| Mejora | Por qué | Riesgo |
|---|---|---|
| **Origen + acuerdo comercial** | El `av` del catálogo es la tasa general. Con TLC (China, EE. UU., UE…) el derecho real puede ser 0 %. La DAM 2024 lo demostró: catálogo 6 %, aduana 0 % | Alto impacto. Mínimo: pedir origen y **avisar** que puede aplicar preferencia; no inventar la tasa |
| `items[]` multilínea con prorrateo por valor | Una DAM real casi siempre trae varias series | Cambia el contrato: hacerlo aditivo (mantener el modo de una línea) |
| Incoterms completos (FCA, FAS, CFR, CPT, CIP, DAP, DPU, DDP) | Saber qué falta según el incoterm evita CIF subvaluados | Ampliar el `Literal` es compatible |
| Deducciones del Art. 8.2 | Los costos posteriores a la llegada son deducibles (DAP/DPU/DDP) | Debe validarse con SUNAT |
| Resumen en PEN | La APK puede mostrar tributos en soles | Solo presentación |
| ISC, antidumping, salvaguardias, percepción | Hoy se excluyen y se avisan | F4; requiere validación legal |

**Rechazado explícitamente:**

- `ad_valorem_pct` enviado por el cliente. El `av` se lee del catálogo, sin
  excepción. Permitir override abre un hueco de subdeclaración y rompe la
  trazabilidad.
- Estimar el seguro (p. ej. 1 % del valor) para "cuadrar" el CIF. Se avisa que
  falta y se calcula sin él.
- Tasas IGV/IPM fijas 16 %/2 %. Obsoletas desde el 01/01/2026 (Ley 32387).

