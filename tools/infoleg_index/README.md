# Infoleg Index (tool)

Crawler para construir un índice local de normas de InfoLEG.

## PostgreSQL local (prototipo actual)

```bash
docker compose up -d
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python crawl_infoleg_pg.py \
  --dsn 'postgresql://juris_ai:juris_ai_local@localhost:55432/juris_ai' \
  --ids 25552
```

La prueba con `25552` recupera la ficha de la Ley 20.744, sus textos original y
actualizado y sus dos listados generales de vínculos.

### Estados de indexación

Cada norma expone un `index_status`:

- `completa`: tiene texto actualizado y artículos estructurados.
- `solo_texto_original`: tiene articulado original, pero no texto actualizado.
- `solo_ficha`: InfoLEG sólo ofrece metadatos.
- `vacia`: el ID responde, pero no contiene una ficha reconocible.
- `error_descarga`: no pudo recuperarse la página.
- `error_parseo`: se descargó contenido que el parser no pudo procesar.

También se mantienen `has_original_text`, `has_current_text`,
`has_outgoing_relations`, `has_incoming_relations` y `article_count`. La tabla
`crawl_items` registra el resultado de cada ID de un lote; un error individual
no interrumpe los IDs restantes.

Consultas rápidas:

```bash
docker compose exec db psql -U juris_ai -d juris_ai -c \
  "select infoleg_id, norm_type, number, canonical_title from norms order by id;"

docker compose exec db psql -U juris_ai -d juris_ai -c \
  "select count(*) from article_versions;"
```

Tests del parser:

```bash
.venv/bin/python -m unittest discover -v -p 'test_*.py'
```

Recalcular estados e indicadores después de una migración:

```bash
.venv/bin/python recalculate_stats.py \
  --dsn 'postgresql://juris_ai:juris_ai_local@localhost:55432/juris_ai'
```

Extraer cambios jurídicos desde los textos actualizados:

```bash
.venv/bin/python extract_legal_changes.py \
  --dsn 'postgresql://juris_ai:juris_ai_local@localhost:55432/juris_ai'
```

Se puede limitar la ejecución con `--infoleg-ids 25552 62`. Los resultados se
guardan en `legal_changes`, siempre con evidencia, confianza, versión del parser
y estado de revisión.

Auditar cobertura sintáctica y listar notas no reconocidas:

```bash
.venv/bin/python audit_legal_parser.py \
  --dsn 'postgresql://juris_ai:juris_ai_local@localhost:55432/juris_ai'
```

## SQLite (legacy / exploración rápida)

```bash
python3 crawl_infoleg.py --db ./infoleg.sqlite --start 1 --end 50000 --sleep 0.2
```

## PostgreSQL sin Docker

1) Crear esquema:

```bash
psql "$JURIS_AI_DSN" -f schema.sql
```

2) Instalar dependencia:

```bash
pip install -r requirements.txt
```

3) Ejecutar crawler estructurado:

```bash
python3 crawl_infoleg_pg.py \
  --dsn "$JURIS_AI_DSN" \
  --ids 25552 229909 \
  --sleep 0.5
```

### Variables/flags clave
- `--ids`: IDs explícitos de InfoLEG; se evita barrer rangos sin control
- `--sleep`: pausa base entre requests
- `--jitter`: variación aleatoria de pausa
- `--timeout`: timeout HTTP por página

## Archivos principales
- `crawl_infoleg.py` → crawler SQLite (legacy / rápido)
- `crawl_infoleg_pg.py` → crawler PostgreSQL estructurado
- `parser.py` → parser de ficha, artículos y relaciones
- `test_parser.py` → pruebas unitarias del parser
- `recalculate_stats.py` → recálculo de estados e indicadores derivados
- `legal_parser.py` → reglas determinísticas para notas modificatorias
- `extract_legal_changes.py` → persistencia y resolución de cambios jurídicos
- `audit_legal_parser.py` → cobertura de reglas y notas explícitas no reconocidas
- `compose.yaml` → PostgreSQL 16 local
- `schema.sql` → modelo inicial de BD (`norms`, `norm_articles`, `norm_relations`, `crawl_runs`, etc.)

## Búsqueda jurídica híbrida

El explorador en `http://localhost:8000/explorar/` usa Full-Text Search en español y reconoce filtros jurídicos como `derogación artículo 48`. `search_chunks` queda preparada para embeddings con pgvector, conservando modelo, versión, hash y fecha de generación. Los embeddings se generan después de seleccionar explícitamente el modelo.

## Notas
- Endpoint: `https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id=<ID>`
- El HTML crudo se conserva en `norm_sources` para poder reprocesarlo.
- Los bytes recibidos se conservan en `raw_content_bytes`; `raw_content` es la
  representación decodificada y sanitizada para PostgreSQL y el parser.
- Las relaciones generales de InfoLEG se guardan inicialmente como
  `modifica_o_complementa`, con confianza 0.7 y revisión pendiente. La
  clasificación fina debe extraerse de las notas de cada artículo.
