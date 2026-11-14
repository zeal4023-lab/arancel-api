# Clasificador Arancelario Perú 2022

App móvil (iOS/Android) que fotografía un producto, lo describe con visión
artificial y lo clasifica en el arancel de aduanas del Perú (2022).

**Pipeline:** foto recortada → visión → descripción editable → fusión con
contexto → 40 candidatos BM25 → LLM que elige de una lista cerrada →
validación contra el catálogo → confianza calibrada.

> Documento de arquitectura completo: [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md)

## Qué hay aquí

```
backend/   FastAPI + preprocesamiento + BM25 + prompts + validación
mobile/    React Native (Expo SDK 54) + expo-camera + recorte + EAS Build
eval/      evaluación del pre-filtrado y smoke test end-to-end
docs/      arquitectura, decisiones, prompts y métricas
```

> ¿Quieres el APK ya? Sigue [`mobile/BUILD_APK.md`](mobile/BUILD_APK.md):
> tres comandos, sin instalar Android Studio ni el SDK.
>
> ¿Quieres que funcione **con el PC apagado y en cualquier red**?
> [`docs/DESPLIEGUE_NUBE.md`](docs/DESPLIEGUE_NUBE.md) + el zip
> `deploy/arancel-cloud-deploy.zip`: cuentas gratuitas de GitHub y Render,
> backend con HTTPS y rebuild del APK.

Métricas medidas sobre `arancel_aduanas_2022.json` (10.377 nodos, 7.806 hojas):

| Métrica | Valor |
|---|---|
| recall@40 (400 consultas por keywords) | 0,87 |
| recall@100 | 0,90 |
| hit@40 (20 consultas reales) | 0,95 |
| Latencia p50 / p95 | 1,2 ms / 4,4 ms |

## Inicio rápido

### 1. Backend

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt

# Genera catalog.json y arancel.db (una vez por versión del arancel)
python -m scripts.build_index --input ..\..\Downloads\arancel_aduanas_2022.json

# Sin OPENAI_API_KEY usa MockLLM (desarrollo); también admite DeepSeek,
# Groq (gratis), Gemini y cualquier endpoint OpenAI-compatible:
# ver backend/PROVEEDORES.md
set OPENAI_API_KEY=sk-...
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Endpoints:

```bash
curl http://localhost:8000/healthz

curl -X POST http://localhost:8000/v1/classify ^
  -H "Content-Type: application/json" ^
  -d "{\"descripcion_ia\":\"Caballo reproductor de raza pura vivo\",\"contexto\":{\"uso_previsto\":\"cria\"}}"

curl -F "image=@foto.jpg" http://localhost:8000/v1/describe

curl "http://localhost:8000/v1/search?q=laptop%20portatil&top_k=5"
```

### 2. App móvil

Prueba rápida en el teléfono, sin compilar (usa **Expo Go**):

```bash
cd mobile
npm install
npx expo start          # escanea el QR con Expo Go (Android/iOS)
```

`expo-camera` sí funciona en Expo Go, así que puedes validar cámara, marco,
recorte, reintento y pantallas completas antes de generar el APK.

APK instalable con EAS Build (nube, sin toolchain local):

```powershell
cd mobile
npx.cmd eas-cli@latest login
npx.cmd eas-cli@latest init
npx.cmd eas-cli@latest build --platform android --profile preview
```

Detalle paso a paso, firewall, keystore y solución de problemas:
[`mobile/BUILD_APK.md`](mobile/BUILD_APK.md).

- `EXPO_PUBLIC_API_URL` debe ser la IP LAN de tu PC (no `localhost` desde un
  teléfono físico). Se incrusta al compilar: cámbiala también en
  `mobile/eas.json` (`build.preview.env`).
- El backend debe escuchar en `0.0.0.0` para ser accesible desde el teléfono.
- `mobile/src/lib/db.ts` contiene el pre-filtrado offline opcional con SQLite;
  para activarlo copia `arancel.db` a `mobile/assets/` e impórtalo. No está
  cableado en la UI actual.

### 3. Evaluación

```bash
python eval/evaluate_retrieval.py   # recall, MRR y casos reales
python eval/smoke_test.py           # pipeline completo con MockLLM
python eval/test_llm_compat.py      # compatibilidad de proveedores (sin red)
python eval/test_groq_live.py       # prueba real describe -> classify (con backend activo)
```

## Variables de entorno (backend)

| Variable | Default | Uso |
|---|---|---|
| `OPENAI_API_KEY` | vacío | Clave de **cualquier** proveedor OpenAI-compatible; sin clave se usa `MockLLM` |
| `OPENAI_BASE_URL` | vacío | `api.deepseek.com`, `api.groq.com/openai/v1`, Gemini-compat, OpenRouter... |
| `LLM_JSON_MODE` | `auto` | `auto` / `schema` / `object`; resuelve las diferencias de `response_format` |
| `VISION_MODEL` / `TEXT_MODEL` | `gpt-4o-mini` | Modelos de visión y de texto |
| `VISION_API_KEY` / `VISION_BASE_URL` | vacío | Opcional: proveedor distinto solo para las fotos |
| `LLM_MAX_TOKENS` | vacío | Límite de salida; en Groq gratis usar `700` (evita 429 por OTPM) |
| `VERIFY_ENABLED` | `true` | Segunda pasada de auditoría |
| `TOP_K` | `40` | Candidatos enviados al LLM (10-60) |
| `CONFIDENCE_HIGH` / `MEDIUM` | `80` / `60` | Umbrales Alta/Media |
| `ALLOW_MOCK` | `true` | Permite arrancar sin API key |

> ¿Sin pagar? [`backend/PROVEEDORES.md`](backend/PROVEEDORES.md) explica cómo
> usar **Groq gratis** (visión real), DeepSeek (visión real, pago mínimo),
> Gemini, OpenAI y combinaciones híbridas. El chat web de DeepSeek es gratis
> pero **no tiene API**.

## Decisiones importantes

1. **Solo hojas clasificables.** Se indexan 7.806 códigos hoja; el texto de
   capítulos/partidas se pliega como contexto. Un nodo interno nunca es
   respuesta.
2. **Jerarquía por dígitos, con huecos.** 4.543 líneas nacionales no tienen
   subpartida de 6 dígitos en el JSON; se resuelve el ancestro existente.
3. **“Los demás” (42,8 %) se marca y se penaliza.** Se avisa al LLM para que
   no sea la opción por defecto.
4. **El LLM no puede alucinar códigos.** Se valida contra la lista candidata y
   los datos oficiales (descripción, nivel, `av`) se copian del catálogo.
5. **La confianza no es la del LLM.** Se mezcla con el rank de recuperación y
   los datos faltantes.

## Limitaciones conocidas

- El arancel 2022 puede tener modificaciones posteriores: la app debe mostrar
  versión/fecha del catálogo y un disclaimer.
- El mapeo del marco a la foto depende de la orientación del sensor. Probar en
  varios dispositivos iOS/Android antes de publicar.
- El pre-filtrado léxico no recupera códigos residuales cuya descripción
  oficial no menciona el nombre comercial (p. ej. “mesa” → “muebles de
  plástico”). Ver §3.8 del documento: enriquecimiento del índice.
- No determina tributos totales (IGV, IPM, percepciones) ni reemplaza a SUNAT.
- Sin `OPENAI_API_KEY` el backend usa `MockLLM`: el APK muestra resultados
  simulados. Sirve para validar la interfaz, no para clasificar.
- El APK apunta a un backend en red local por HTTP; en Android requiere
  `usesCleartextTraffic` (ya configurado) y ambos dispositivos en la misma red.

## Licencia / uso

Código de referencia. Verifica la normativa vigente y los términos de uso del
arancel antes de desplegarlo en producción.
