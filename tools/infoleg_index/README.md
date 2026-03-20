# Infoleg Index (tool)

Crawler para construir un índice local de normas de Infoleg.

## Opción 1: SQLite (rápido para prototipo)

```bash
python3 crawl_infoleg.py --db ./infoleg.sqlite --start 1 --end 50000 --sleep 0.2
```

## Opción 2: PostgreSQL (recomendada para JurisAI)

1) Crear esquema:

```bash
psql "$JURIS_AI_DSN" -f schema.sql
```

2) Instalar dependencia:

```bash
pip install -r requirements.txt
```

3) Ejecutar crawler:

```bash
python3 crawl_infoleg_pg.py \
  --dsn "$JURIS_AI_DSN" \
  --start 1 --end 5000 \
  --sleep 0.2
```

### Variables/flags clave
- `--start/--end`: rango de ids
- `--sleep`: pausa base entre requests
- `--jitter`: variación aleatoria de pausa
- `--commit-every`: cada cuántas filas hacer commit

## Archivos principales
- `crawl_infoleg.py` → crawler SQLite (legacy / rápido)
- `crawl_infoleg_pg.py` → crawler PostgreSQL (recomendado)
- `schema.sql` → modelo inicial de BD (`norms`, `norm_articles`, `norm_relations`, `crawl_runs`, etc.)

## Notas
- Endpoint: `https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id=<ID>`
- Scraper “best effort”: hoy extrae mínimo `titulo` y `sumario` + snapshot HTML para reprocesar.
- Paso siguiente: parser de artículos y relaciones normativas sobre `raw_documents`.
