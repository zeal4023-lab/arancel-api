# Valoración — Fase F2 (métodos 2 a 6)

**Estado:** método **4 (valor deductivo)** implementado en la rama
`feature/valoracion-f2`. Los métodos 2, 3, 5 y 6 siguen sin implementar, y aquí
se explica por qué.

---

## 1. Qué se puede implementar y qué no

| # | Método | Estado | Motivo |
|---|---|---|---|
| 1 | Valor de transacción | ✅ F1 | Precio de la factura |
| 2 | Mercancías idénticas | ❌ | Requiere base de importaciones previas valoradas de Aduanas. No es pública en volumen |
| 3 | Mercancías similares | ❌ | Igual que el 2, más criterios de equivalencia funcional |
| 4 | **Valor deductivo** | ✅ **F2** | Aritmética determinista desde el precio de venta local |
| 5 | Valor reconstruido | ❌ | Requiere costos de producción en origen, no verificables |
| 6 | Último recurso | ❌ | Criterio discrecional de SUNAT; no se puede automatizar con rigor |

**No se inventan datos.** Los métodos 2 y 3 necesitan una base de valores de
Aduanas que no tenemos; simularla produciría cifras con apariencia de
autoridad y sin respaldo. El 5 requiere auditar la contabilidad del proveedor.

---

## 2. Endpoint

```
POST /v1/valuation/deductive
```

### Entrada

```json
{
  "api_version": "1.1",
  "codigo": "6109.10.00.50",
  "origen": "CN",
  "cantidad": 100,
  "precio_venta_unitario": 59.90,
  "moneda": "PEN",
  "incluye_igv": true,
  "margen_pct": 35,
  "gastos_internos": 0,
  "igv_acreditable": true,
  "precio_declarado_unitario": 9.00,
  "fecha_calculo": "2026-11-20"
}
```

| Campo | Significado |
|---|---|
| `precio_venta_unitario` | Precio al que se vende (o se vendería) la unidad en Perú |
| `incluye_igv` | Si el precio ya lleva el IGV trasladado al cliente |
| `margen_pct` | Margen del importador **+ gastos generales**, sobre el costo |
| `gastos_internos` | Flete interno, almacén… **total de la operación** |
| `igv_acreditable` | `true` si el importador tiene crédito fiscal (régimen general) |
| `precio_declarado_unitario` | Opcional: comparar contra lo que se piensa declarar |

### Salida

Además del valor deducido, devuelve la **cadena del cálculo** paso a paso, el
desglose completo de tributos (`equivalente`) y, si se envía un precio
declarado, la comparación con una alerta cuando está muy por debajo.

---

## 3. Fórmula

Sea:
- `R` = precio de venta unitario
- `t` = tasa IGV + IPM vigente (18 % en 2026)
- `a` = ad valorem del código
- `m` = margen + gastos generales
- `E` = gastos internos
- `TC` = tipo de cambio PEN/USD

```
neto   = R / (1 + t)                  quita el IGV trasladado al cliente
costo  = neto / (1 + m)               quita margen y gastos generales
CIF    = (costo − E/TC) / (1 + a) / TC
```

Si `igv_acreditable = false`, el divisor pasa a `(1 + a)(1 + t)`, porque el
IGV de importación se vuelve costo.

### Verificación de ida y vuelta

```
CIF × TC × (1 + a) × (1 + m) × (1 + t) = R × cantidad
```

Es la prueba que corre `test_deductivo_reconstruye_el_precio_de_venta`.

---

## 4. Ejemplo verificado

Camisetas `6109.10.00.50` (av 11 %), 100 unidades, venta a **S/ 59.90** con
IGV, margen 35 %, TC 3.50, tasas 2026 (15.5 % + 2.5 %):

| Paso | Concepto | Valor |
|---|---|---|
| 1 | Precio de venta total | S/ 5 990.00 |
| 2 | − IGV + IPM (18 %) | S/ 5 076.27 |
| 3 | − Margen y gastos (35 %) | S/ 3 760.20 |
| 4 | − Gastos internos | S/ 3 760.20 |
| 5 | − Ad valorem (11 %) | **US$ 967.88** |

Desglose resultante (100 unid.):

| Concepto | USD |
|---|---|
| CIF (valor en aduana) | 967.88 |
| Ad valorem 11 % | 106.47 |
| IGV 15.5 % | 166.52 |
| IPM 2.5 % | 26.86 |
| **Total tributos** | **299.85** |

Valor unitario implícito: **US$ 9.68**. Si se declarara US$ 5.00, el comparador
avisa: `ratio 51.66 % → muy por debajo`, con la advertencia de que SUNAT podría
observarlo.

---

## 5. Supuestos (van en la respuesta)

- El precio de venta corresponde a compradores **no vinculados** en Perú.
- El margen incluye utilidad y gastos generales.
- El método 4 **solo procede cuando el método 1 no es admisible**.
- No se incluyen ISC, antidumping, salvaguardias ni percepción.

## 6. Cuándo NO usar el método 4

- Cuando existe factura y el precio es defendible → método 1.
- Cuando la venta es entre partes vinculadas sin precio de libre concurrencia.
- Cuando no hay venta en Perú del producto importado.

El orden de los métodos es **obligatorio**: solo se baja al siguiente cuando el
anterior no puede aplicarse (Acuerdo de Valoración OMC, Art. 1 a 7).

---

## 7. Pruebas

```powershell
python eval\test_valoracion.py     # 28 pruebas (8 nuevas del método 4)
python eval\validar_dam.py         # 1/1 DAM real, sin regresiones
python eval\verificar_prod.py      # contra producción
```

Casos cubiertos: cálculo concreto, invariante de ida y vuelta, comparación con
precio declarado, sin crédito fiscal, ad valorem 0 %, validaciones de entrada,
flag apagado y catálogo de métodos.

---

## 8. Pendiente

| Paso | Qué | Bloqueo |
|---|---|---|
| 2.1 | Sugerir el precio de venta desde la web (Brave) | ✅ implementado, falta la clave |
| 2.2 | Métodos 2/3 con comparables | Falta base de datos de Aduanas |
| 2.3 | UI en la APK para el método 4 | Requiere build |

La búsqueda web alimentaría el **precio de venta** del método 4, no un valor de
aduana directo: un precio de retail no es prueba aduanera.

---

## 9. Precios de referencia desde búsqueda web (implementado)

`POST /v1/valuation/reference`

Busca el producto en la web, extrae precios y devuelve un rango. Si se envía
el código, además estima el valor en aduana con el método 4.

### Activación (dos flags)

```
VALUATION_ENABLED       = true
REFERENCE_SEARCH_ENABLED = true
BRAVE_API_KEY           = <secreto, solo en el panel>
REFERENCE_MONTHLY_LIMIT = 990
```

### Qué devuelve

```json
{
  "referencial": true,
  "consulta": "termo electrico 1.5l",
  "rango": { "min": 79.90, "mediana": 82.50, "max": 85.00, "muestras": 3 },
  "moneda": "PEN",
  "rangos": {
    "PEN": { "min": 79.90, "mediana": 82.50, "max": 85.00, "muestras": 3 },
    "USD": { "min": 22.00, "mediana": 23.00, "max": 24.00, "muestras": 2 }
  },
  "fuentes": ["falabella.com.pe", "ripley.com.pe"],
  "resultados_ignorados": 4,
  "cupo": { "usadas": 1, "limite": 990, "restantes": 989 },
  "estimacion_aduanera": {
    "metodo": 4,
    "precio_venta_usado": 82.50,
    "moneda_precio_venta": "PEN",
    "valor_deducido_unitario_usd": 7.42,
    "tributos": { "...": "..." }
  }
}
```

### Dos reglas de oro del precio

1. **Solo tiendas peruanas** (dominio `.pe`). Sin este filtro, Brave devuelve
   MercadoLibre Chile o Argentina y un termo aparece a "S/ 67 500", que en
   realidad son pesos. Los resultados de otros países se descartan y se
   informan en `resultados_ignorados`.
2. **Soles o dólares**, según publique la tienda. Se devuelven ambos rangos por
   separado (`rangos.PEN` y `rangos.USD`), nunca mezclados ni convertidos. El
   rango principal prefiere soles; si la tienda solo publica en USD, se usa USD
   y la estimación se calcula con esa moneda.

### Mercados: `PE` y `US`

| `mercado` | Busca en | Moneda | ¿Alimenta el método 4? |
|---|---|---|---|
| `PE` (por defecto) | Tiendas `.pe` | PEN o USD | **Sí** (precio de reventa en Perú) |
| `US` | Amazon, Walmart, Best Buy, Target… | USD | **No** |

**Por qué `US` no usa el método 4:** el valor deductivo exige el precio de
reventa **en el país de importación** (Perú). Un precio de Amazon no es eso.

Para qué sirve entonces `US`: para juzgar si el FOB declarado es creíble. Si el
producto se vende en Amazon a US$ 30 y declaras FOB US$ 3, es plausible
(margen, flete, tributos). Si declaras US$ 45, algo no cuadra.

El cliente puede enviar `precio_declarado_unitario` y recibir `comparacion`:

```json
"comparacion": {
  "precio_declarado_usd": 3.00,
  "precio_mercado_us_usd": 29.99,
  "ratio_pct": 10.00,
  "nota": "Comparación contra precio de mercado en EE. UU. ..."
}
```



### Reglas de cumplimiento (términos de Brave)

| Cláusula | Cómo se cumple |
|---|---|
| 3(b)(i) no almacenar resultados | Solo memoria transitoria de **120 s** para evitar el doble toque. Nada en disco ni en base de datos |
| 3(b)(xi) no redistribuir | Se devuelve el **dominio** de origen, nunca la lista de resultados |
| 3(b)(xiii) no entrenar modelos | La extracción es con expresiones regulares; no hay LLM ni dataset |
| 4(d) atribución | Voluntaria; no se usan marcas de Brave |

### Coste y límites

- $5/1 000 consultas; $5 de crédito gratuito al mes (≈1 000 consultas).
- Tope propio en **990** (`REFERENCE_MONTHLY_LIMIT`), por debajo del crédito.
- En el panel de Brave: prepago **$0**, recarga automática **apagada** y
  **límite de uso mensual en $4.99**. Así el coste máximo es $0.
- El contador vive en memoria: se reinicia al redesplegar. La garantía real de
  no pagar es el prepago en $0.

### Si Brave falla

| Situación | Respuesta |
|---|---|
| Flag apagado | `503 REFERENCE_DISABLED` |
| Sin `BRAVE_API_KEY` | `503 REFERENCE_UNAVAILABLE` |
| Cupo agotado | `429 REFERENCE_LIMIT_REACHED` |
| Error de red / clave inválida | `503 REFERENCE_UNAVAILABLE` |
| Sin precios utilizables | `200` con `rango: null` y un aviso |

En todos los casos la clasificación y los métodos 1 y 4 siguen funcionando.

### Pruebas

`eval/test_valoracion.py` cubre: extracción de formatos (`S/ 59,90`,
`S/. 1.299,00`, `$ 12.99`), descarte de valores atípicos, mínimo de muestras,
rango real, caché transitoria, límite mensual, flag apagado, falta de clave y
estimación con método 4. Total: **37 pruebas**.

