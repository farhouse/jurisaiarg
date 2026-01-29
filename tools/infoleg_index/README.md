# Infoleg Index (tool)

Crawler simple para construir un índice local (SQLite) de normas de Infoleg.

## Uso

```bash
python3 crawl_infoleg.py --db ./infoleg.sqlite --start 1 --end 50000 --sleep 0.2
```

- `--start/--end`: rango de ids
- `--sleep`: pausa entre requests
- El crawler intenta extraer (mínimo): `titulo` y `sumario`.

## Notas
- Infoleg endpoint: `https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id=<ID>`
- Este scraper es “best effort” y se irá mejorando (parsing de número/tipo/fechas/modificatorias).
