# Architecture — JurisAI (MVP · Laboral CABA)

## Scope (Etapa 1)
- Jurisdicción: **CABA / Justicia Nacional del Trabajo**
- Incluye: **1ra instancia + Cámara (CNAT / salas)**
- Dataset inicial: ~**30 sentencias completas** (manual download asistido)
- Entregable: pipeline end-to-end + storage + reporte básico por juez/sala.

## Core idea
Separar el sistema en 3 capas para poder re-procesar sin perder el original:
1) **RAW**: PDF/HTML descargado (intacto)
2) **TEXT**: texto extraído + limpieza
3) **STRUCTURED**: JSON estructurado (outcome/costas/etc.) + evidencia

## Storage choice
### Recommended: Postgres + artifacts on disk
- **Postgres**: metadata, normalización de entidades (jueces/tribunales), extracciones versionadas, métricas y queries.
- **Filesystem** (y futuro S3): PDFs, texto extraído, segmentos, outputs grandes.

Rationale:
- Queries “por juez/sala” y comparativas requieren joins/constraints.
- Permite auditoría (qué extractor/prompt generó qué salida).

### Alternative (exploración): JSONL/Parquet + DuckDB
Útil para prototipos rápidos, pero para normalización y auditoría Postgres ordena mejor.

## Data layout (repo)
Propuesta dentro de `juris-ai/`:

```
juris-ai/
  data/
    raw/                # PDFs/HTML originales
    text/               # texto extraído (txt)
    segments/           # segmentos: vistos/considerando/resuelvo
    extractions/         # JSON estructurado por versión de extractor
    manifest.jsonl       # 1 línea por documento
  docs/
    brief.md
    parametrizaciones.md
    architecture.md
  tools/
```

### `manifest.jsonl` (borrador)
Una sentencia por línea:

```json
{"id":"<sha256>","source_type":"pdf","source_ref":"<url|manual>","downloaded_at":"<iso>","court":"<string>","instance":"1ra|camara","sala":"<opt>","date":"<opt>","case_number":"<opt>","caratula":"<opt>","raw_path":"data/raw/<id>.pdf"}
```

## Postgres schema (MVP)
### `documents`
- `id` (text, pk) — hash estable (sha256 del raw)
- `source_type` (text)
- `source_ref` (text)
- `downloaded_at` (timestamptz)
- `court` (text)
- `instance` (text) — `1ra` / `camara`
- `sala` (text, nullable)
- `date` (date, nullable)
- `case_number` (text, nullable)
- `caratula` (text, nullable)
- `raw_path` (text)
- `text_path` (text, nullable)

### `extractions`
- `id` (uuid, pk)
- `document_id` (fk → documents.id)
- `extractor_version` (text)
- `created_at` (timestamptz)
- `json` (jsonb)
- `evidence` (jsonb)

### `judges` + `judge_aliases`
- `judges(id uuid pk, canonical_name text unique)`
- `judge_aliases(alias text pk, judge_id uuid fk)`

(igual para tribunales si hace falta: `courts`, `court_aliases`).

## Pipeline (scripts)
MVP de scripts (se puede hacer en Python):
1) `extract_text.py` — pdf/html → txt
2) `segment.py` — txt → {vistos, considerando, resuelvo}
3) `extract_structured.py` — LLM + reglas → json + evidence
4) `ingest_manifest.py` — registra manifest + paths en Postgres
5) `report.py` — queries por juez/sala + métricas básicas

## Metrics (MVP)
Arrancar con features robustas (no ideológicas):
- `outcome`: concede / rechaza / parcial
- `costas`: actor/demandado/por su orden
- `cautelar`: sí/no (si aplica)
- densidad de citas normativas (conteo, luego normalizar por tipo)
- longitud de secciones (tokens/páginas aprox.)

## Next actions
- Definir formato del dataset semilla (ideal: PDFs).
- Decidir dónde corre Postgres (local Docker vs instancia existente).
- Preparar template de `manifest.jsonl` y proceso de carga asistida.
