"""Configuración por variables de entorno (12-factor)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    catalog_path: str = os.getenv(
        "CATALOG_PATH",
        os.path.join(os.path.dirname(__file__), "..", "data", "catalog.json"),
    )
    # --- Modelos (cámbialos por los vigentes de tu proveedor) ---------------
    vision_model: str = os.getenv("VISION_MODEL", "gpt-4o-mini")
    text_model: str = os.getenv("TEXT_MODEL", "gpt-4o-mini")
    verify_model: str = os.getenv("VERIFY_MODEL", os.getenv("TEXT_MODEL", "gpt-4o-mini"))
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str | None = os.getenv("OPENAI_BASE_URL") or None

    # --- Compatibilidad con proveedores OpenAI-like -------------------------
    # "auto" elige json_schema para OpenAI/Groq y json_object para el resto
    # (DeepSeek, Gemini-compat, OpenRouter...). Fuerza uno con "schema"/"object".
    llm_json_mode: str = os.getenv("LLM_JSON_MODE", "auto").lower()

    # Overrides opcionales: permiten usar un proveedor GRATIS para la visión
    # y otro (p. ej. DeepSeek) para el texto. Si no se definen, se usa el
    # mismo cliente para todo.
    vision_api_key: str = os.getenv("VISION_API_KEY", "")
    vision_base_url: str | None = os.getenv("VISION_BASE_URL") or None

    # --- Recuperación -------------------------------------------------------
    top_k: int = _env_int("TOP_K", 40)          # candidatos enviados al LLM
    max_per_heading: int = _env_int("MAX_PER_HEADING", 6)

    # --- Calibración de confianza ------------------------------------------
    confidence_high: int = _env_int("CONFIDENCE_HIGH", 80)
    confidence_medium: int = _env_int("CONFIDENCE_MEDIUM", 60)
    # Por debajo de este valor se repite la búsqueda usando la pista de "Revisar".
    # 0 desactiva la segunda búsqueda.
    confidence_retry: int = _env_int("CONFIDENCE_RETRY", 60)

    # --- Robustez -----------------------------------------------------------
    llm_timeout_s: float = float(os.getenv("LLM_TIMEOUT_S", "45"))
    llm_max_retries: int = _env_int("LLM_MAX_RETRIES", 2)
    # Algunos planes gratuitos (Groq on_demand) limitan los tokens de SALIDA
    # por minuto y rechazan la petición si el estimado los supera, aunque no
    # se envíe max_tokens. Fijarlo evita el 429 "Request too large".
    llm_max_output_tokens: int | None = (
        int(os.environ["LLM_MAX_TOKENS"])
        if os.getenv("LLM_MAX_TOKENS", "").isdigit()
        else None
    )
    # La fusión produce una salida corta. Limitarla aparte evita que
    # fusión + clasificación sumen más que el límite OTPM del plan gratuito
    # (Groq ~1000 tokens de salida por minuto) y provoque 429 con reintentos.
    fusion_max_tokens: int = _env_int("LLM_FUSION_MAX_TOKENS", 250)
    # Verificación condicional: con VERIFY_ENABLED=true, solo se verifica
    # cuando la confianza queda por debajo de este valor. 0 = verificar siempre.
    confidence_verify: int = _env_int("CONFIDENCE_VERIFY", 60)
    verify_enabled: bool = os.getenv("VERIFY_ENABLED", "true").lower() == "true"

    # --- Valoración aduanera (F1) -------------------------------------------
    # Apagada por defecto: el código se despliega sin exponer el endpoint.
    valuation_enabled: bool = os.getenv("VALUATION_ENABLED", "false").lower() == "true"
    valuation_api_version: str = os.getenv("VALUATION_API_VERSION", "1.1")
    # Tipo de cambio: timeout corto y caché en memoria. Nunca bloquea.
    valuation_fx_timeout_s: float = float(os.getenv("VALUATION_FX_TIMEOUT_S", "4"))
    valuation_fx_cache_h: int = _env_int("VALUATION_FX_CACHE_H", 12)

    # Si no hay API key, el backend usa un proveedor simulado (solo para dev).
    allow_mock: bool = os.getenv("ALLOW_MOCK", "true").lower() == "true"

    cors_origins: list[str] = field(
        default_factory=lambda: [o for o in os.getenv("CORS_ORIGINS", "*").split(",") if o]
    )


settings = Settings()
