# Infoleg Index (idea) — Base local de normas

Objetivo: armar una BD local (cache) que permita:
- buscar rápido una norma por **id infoleg**, **número**, palabras clave, etc.
- resolver referencias normativas desde sentencias
- futuro: exponerlo como herramienta (p.ej. MCP) para que el agente consulte normas

## Fuente
- Infoleg: `https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id=<ID>`
- Observación: el `id` parece **secuencial** (al menos en rangos bajos/medios). Se puede recorrer por rango.

## MVP de la BD (campos)
- `infoleg_id` (int, PK)
- `url`
- `titulo` (string)
- `sumario` (string)
- `tipo_norma` (opcional)
- `numero_norma` (opcional)
- `fecha` (opcional)
- `organismo` (opcional)
- `texto` (opcional; probablemente pesado)
- `modifica_a` / `modificada_por` (opcional; relaciones)
- `fetched_at`
- `http_status` / `error`

## Notas de implementación
- Respetar rate limit / politeness (sleep + retry/backoff)
- Guardar HTML raw opcional para reprocesar parsers
- Definir estrategia de fin de rango (p.ej. cortar cuando hay N ids seguidos sin contenido útil)

Ver `tools/infoleg_index/`.
