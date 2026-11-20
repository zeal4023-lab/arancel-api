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
| 2.1 | Sugerir el precio de venta desde la web (Brave) | Falta `BRAVE_API_KEY` |
| 2.2 | Métodos 2/3 con comparables | Falta base de datos de Aduanas |
| 2.3 | UI en la APK para el método 4 | Requiere build |

La búsqueda web alimentaría el **precio de venta** del método 4, no un valor de
aduana directo: un precio de retail no es prueba aduanera.
