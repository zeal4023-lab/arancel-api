"""
Módulo de valoración aduanera (F1: primer método).

Aislado del clasificador: no importa nada de `service.py` ni modifica los
contratos de `/v1/describe` o `/v1/classify`.

- Toda la aritmética es determinista (Decimal), no LLM.
- Las tasas viven en `rates.py` con fecha de vigencia.
- El tipo de cambio tiene respaldo local y nunca bloquea el cálculo.
- El router se activa solo con VALUATION_ENABLED=true.
"""
__all__ = ["rates", "fx", "calculator", "schemas", "router"]
