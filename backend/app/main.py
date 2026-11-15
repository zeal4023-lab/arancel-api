"""
API FastAPI del clasificador arancelario.

Ejecutar:
    cd backend
    uvicorn app.main:app --reload --port 8000

Endpoints:
    GET  /healthz
    POST /v1/describe          (multipart: image)
    POST /v1/classify          (JSON)
    GET  /v1/codes/{codigo}
    GET  /v1/search?q=...
"""
from __future__ import annotations

import base64
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .llm import LLMError, MockLLM
from .schemas import ClassifyIn, ClassifyOut, ContextoIn, SearchOut, VisionOut
from .service import ClassificationService, ProductContext

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 8 MB, suficiente tras redimensionar a 1280px


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service = ClassificationService()
    log.info("Catálogo cargado: %s", app.state.service.meta)
    yield


app = FastAPI(
    title="Clasificador Arancelario Perú 2022",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


def svc() -> ClassificationService:
    return app.state.service


@app.get("/healthz")
def healthz() -> dict:
    service = svc()
    return {
        "status": "ok",
        "catalogo": service.meta,
        "modelos": {"vision": settings.vision_model, "texto": settings.text_model},
        "proveedor": getattr(service.llm, "provider_label", "desconocido"),
        "modo_json": settings.llm_json_mode,
        "modo_simulado": isinstance(service.llm, MockLLM),
        "verify_enabled": settings.verify_enabled,
    }


# Alias: algunos monitores/proxies sondean /health en lugar de /healthz.
@app.get("/health")
def health() -> dict:
    return healthz()


@app.post("/v1/describe", response_model=VisionOut)
async def describe(image: UploadFile = File(...)) -> VisionOut:
    if image.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(415, "Formato no soportado (usa JPEG, PNG o WebP).")
    data = await image.read()
    if not data:
        raise HTTPException(400, "Imagen vacía.")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Imagen demasiado grande (máx. 8 MB).")
    try:
        # La llamada a la IA es bloqueante: se ejecuta en un hilo aparte para
        # no congelar el servidor (y que el health check siga respondiendo).
        result = await run_in_threadpool(
            svc().describe_image, base64.b64encode(data).decode("ascii")
        )
    except LLMError as exc:
        raise HTTPException(502, f"El servicio de visión falló: {exc}") from exc
    return VisionOut(**result)


@app.post("/v1/classify", response_model=ClassifyOut)
def classify(body: ClassifyIn) -> ClassifyOut:
    contexto = ProductContext(**body.contexto.model_dump())
    try:
        result = svc().classify(
            descripcion_ia=body.descripcion_ia,
            descripcion_usuario=body.descripcion_usuario,
            contexto=contexto,
            top_k=body.top_k,
            verify=body.verificar,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(503, f"Clasificación no disponible: {exc}") from exc

    return ClassifyOut(
        resultado={
            "codigo": result.codigo,
            "descripcion": result.descripcion,
            "nivel": result.nivel,
            "av": result.av,
        },
        confianza=result.confianza,
        nivel_confianza=result.nivel_confianza,
        explicacion=result.explicacion,
        regla_interpretacion=result.regla_interpretacion,
        alternativas=result.alternativas,
        informacion_faltante=result.informacion_faltante,
        advertencia=result.advertencia,
        descripcion_fusionada=result.descripcion_fusionada,
        trazabilidad=result.trazabilidad,
    )


@app.get("/v1/codes/{codigo}")
def get_code(codigo: str) -> dict:
    node = svc().get_code(codigo)
    if not node:
        raise HTTPException(404, "Código no encontrado")
    return {
        "codigo": node.codigo,
        "descripcion": node.descripcion,
        "nivel": node.nivel,
        "av": node.av,
        "es_residual": node.is_residual,
        "ruta": node.path,
    }


@app.get("/v1/search", response_model=SearchOut)
def search(
    q: str = Query(..., min_length=3, max_length=500),
    top_k: int = Query(10, ge=1, le=50),
) -> SearchOut:
    return SearchOut(candidatos=svc().search(q, top_k=top_k))
