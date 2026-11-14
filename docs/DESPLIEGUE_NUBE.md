# Despliegue en la nube (PC apagada ✅, cualquier red ✅, Groq gratis ✅)

Guía para mover el backend de tu PC a un servidor público con HTTPS. Al final el
APK apuntará a esa URL y **ya no necesitarás tu PC encendida**.

---

## 1. Dónde crear las cuentas

| Cuenta | URL | Para qué | Estado |
|---|---|---|---|
| **GitHub** | <https://github.com/signup> | Alojar el código que Render va a construir | ❌ **Crear** |
| **Render** | <https://render.com> (regístrate **con GitHub**) | Ejecutar el backend 24/7 con HTTPS público | ❌ **Crear** |
| Groq | <https://console.groq.com/keys> | La IA gratis | ✅ Ya la tienes, pero **rota la clave** |
| Expo | <https://expo.dev> | Solo recompilar el APK | ✅ Ya la tienes |

> Registrarte en Render con GitHub cuenta como un solo inicio de sesión: son
> **2 cuentas nuevas** en la práctica (GitHub + Render).

### ¿Por qué Render y no otro?

| Plataforma | Tarjeta | Duerme | Veredicto |
|---|---|---|---|
| **Render Free** | **No** | Sí, tras 15 min inactivo (~30–60 s para despertar) | ✅ **Recomendado**: gratis, HTTPS, Docker, sin tarjeta |
| Hugging Face Spaces | No | Sí | ❌ Desde 2026 los Spaces **Docker requieren plan PRO** (de pago) |
| Koyeb | **Sí** (retención de US$ 29) | Sí | ❌ Pide tarjeta |
| Railway | Sí | No | ❌ Crédito de US$ 5, luego tarjeta |
| Fly.io | Sí | No | ❌ Sin capa gratuita para cuentas nuevas |
| Google Cloud Run | Sí (billing) | Sí | ⚠️ Gratis dentro de cuota, pero exige tarjeta |

El único "peaje" de Render gratis es el **arranque en frío**: la primera foto
tras un rato de inactividad puede tardar ~1 min o fallar por timeout. Si te
molesta, el plan de pago son US$ 7/mes y elimina el problema.

### ⚠️ Haz esto ANTES de empezar

La clave de Groq que pegaste en el chat quedó expuesta. Ve a
<https://console.groq.com/keys>, **bórrala** y crea una nueva. Cópiala solo en
el panel de Render; **no la pongas en el repositorio ni me la pegues**.

---

## 2. Subir el código a GitHub (sin instalar git)

En esta máquina no hay `git`, así que usamos la **web de GitHub**:

1. Descomprime `deploy/arancel-cloud-deploy.zip` en una carpeta.
2. En GitHub: **New repository** → nombre `arancel-api` → *Public* o *Private*
   (Render acepta ambos) → **no** marques "Add a README" → **Create repository**.
3. En la página del repo: **uploading an existing file** → arrastra **el
   contenido** de la carpeta descomprimida (no la carpeta que lo envuelve).
4. **Commit changes**.

Debe quedar así:

```
arancel-api/
├── Dockerfile
├── .dockerignore
├── .gitignore
├── render.yaml
├── backend/
│   ├── requirements.txt
│   ├── app/*.py
│   ├── scripts/build_index.py
│   └── data/catalog.json   ← 16 MB, el arancel
└── docs/DESPLIEGUE_NUBE.md
```

> No subas `mobile/`, `node_modules/`, ni ningún `.env`. El zip ya los excluye.
> El `catalog.json` **sí** debe subirse: el contenedor lo lee al arrancar y no
> reconstruye el índice. Son 16 MB; el límite del cargador web de GitHub es
> **25 MiB por archivo** y 100 archivos por subida, así que entra sin problema.

---

## 3. Crear el servicio en Render

**Opción A — Blueprint (más rápida):**

1. <https://dashboard.render.com> → **New** → **Blueprint** → conecta GitHub →
   elige `arancel-api`.
2. Render lee `render.yaml` y rellena todo solo.
3. Te pedirá el valor de `OPENAI_API_KEY` (`sync: false`): pega la **clave nueva
   de Groq**.
4. Plan **Free** → **Apply** / **Create**.

**Opción B — Manual:** New → **Web Service** → elige el repo →

| Campo | Valor |
|---|---|
| Runtime | **Docker** |
| Region | Oregon (o la más cercana) |
| Instance type | **Free** |
| Health Check Path | `/healthz` |

Y en **Environment** añade:

| Variable | Valor |
|---|---|
| `OPENAI_API_KEY` | tu clave Groq nueva (**Secret**) |
| `OPENAI_BASE_URL` | `https://api.groq.com/openai/v1` |
| `VISION_MODEL` | `qwen/qwen3.8-27b` |
| `TEXT_MODEL` | `qwen/qwen3.8-27b` |
| `VERIFY_MODEL` | `qwen/qwen3.8-27b` |
| `TOP_K` | `25` |
| `VERIFY_ENABLED` | `false` |
| `LLM_MAX_TOKENS` | `700` |
| `LLM_TIMEOUT_S` | `90` |
| `CORS_ORIGINS` | `*` |
| `ALLOW_MOCK` | `false` |

El build tarda 3–6 min. Al terminar tendrás una URL tipo:

```
https://arancel-api.onrender.com
```

### Verificación (desde el navegador del teléfono, con datos móviles)

```
https://arancel-api.onrender.com/healthz
```

Debe devolver `"status":"ok"` y **`"modo_simulado": false`**. Si dice `true`,
la clave no se aplicó. Si tarda ~1 min en responder, es el arranque en frío:
normal en el plan Free.

---

## 4. Rebuild del APK (obligatorio)

La URL del backend está **incrustada** en el APK; no se cambia en caliente.

Edita `mobile/eas.json`, línea del perfil `preview`:

```json
"env": {
  "EXPO_PUBLIC_API_URL": "https://arancel-api.onrender.com"
}
```

(HTTPS, sin barra final). Luego:

```powershell
cd C:\Users\ASUS\arancel-clasificador\mobile
npx.cmd eas-cli@latest build -p android --profile preview
```

Instala el APK nuevo desde el enlace/QR que te da EAS. Ya puedes **apagar el PC
y usar datos móviles**.

---

## 5. Limitaciones que debes conocer

| Límite | Efecto | Mitigación |
|---|---|---|
| Render Free duerme a los 15 min | 1ª petición tras inactividad: 30–60 s; a veces timeout en la app | Reintenta tras ~1 min, o plan de US$ 7/mes |
| 750 h/mes de instancia gratis | Suficiente para 1 servicio; si se agota, se suspende hasta el mes siguiente | Uso personal: sobra |
| Sistema de archivos efímero | Se borra al redesplegar | No importa: `catalog.json` va **dentro de la imagen**, no se escribe en runtime |
| Groq free: OTPM 1000 | 429 → 503 si no se limita la salida | `LLM_MAX_TOKENS=700` (ya configurado) |
| Groq free: ~1 búsqueda/min | 429 si haces varias seguidas | Espera ~60 s entre búsquedas |
| Privacidad | La foto sale del teléfono → Render → Groq | Los planes gratis pueden usar datos para entrenar; no envíes documentos personales |

**Lo que NO cambia:** la precisión (mismo catálogo, mismos prompts, mismo Groq),
la UI, el marco, el reintento de 2 fotos ni la validación de lista cerrada.

---

## 6. Problemas frecuentes

| Síntoma | Causa | Solución |
|---|---|---|
| Build de Render falla | Falta `catalog.json` o `Dockerfile` en el repo | Revisa la estructura del paso 2 |
| `502 Bad Gateway` | El contenedor no escucha en `$PORT` | El `Dockerfile` ya usa `${PORT:-10000}`; mira los logs |
| `modo_simulado: true` | Falta `OPENAI_API_KEY` | Añádela en Environment y redesplega |
| `503` al clasificar | Rate limit de Groq | Espera 60 s; comprueba `LLM_MAX_TOKENS=700` |
| App: "Network request failed" | URL mal puesta o **no recompilaste** el APK | Edita `eas.json` y vuelve a `eas build` |
| La 1ª foto siempre falla | Arranque en frío de Render | Abre `/healthz` en el navegador para "despertar" y reintenta |
| Se agota la memoria (512 MB) | Catálogo + BM25 | Reduce `TOP_K` a 15 o sube de plan |

---

## 7. Resumen de cuentas y costes

| Servicio | Cuenta | Coste |
|---|---|---|
| GitHub | nueva | US$ 0 |
| Render (Free) | nueva | US$ 0 |
| Groq (Free) | existente, clave rotada | US$ 0 |
| EAS Build (Free) | existente | US$ 0 |
| **Total** | | **US$ 0/mes** |

Recordatorio legal: la clasificación es **orientativa**; la determina
SUNAT/aduana, y el arancel es la versión 2022.
