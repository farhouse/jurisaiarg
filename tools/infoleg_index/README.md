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

## Prueba local con Laya

`try_laya.py` compara Laya multilingüe con las reglas existentes. Es un
experimento de clasificación: no descarga normas, no extrae nuevos números o
fechas y no escribe ni valida relaciones en PostgreSQL. No requiere API paga.

Crear un entorno separado con Python 3.11 (el entorno anterior de herramientas
puede usar Python 3.9, incompatible con Laya; PyTorch 2.2 en Mac Intel tampoco
admite Python 3.12 para este modelo):

```bash
python3.11 -m venv .venv-laya
.venv-laya/bin/python -m pip install -r requirements-laya.txt
.venv-laya/bin/python try_laya.py --output laya-results/smoke.jsonl
```

La primera ejecución descarga los pesos públicos desde Hugging Face a
`.laya-cache/`; las inferencias se ejecutan localmente en CPU. El modelo y el
entorno están excluidos de Git. Para repetir con los pesos descargados y sin
acceso a la red:

```bash
HF_HUB_OFFLINE=1 .venv-laya/bin/python try_laya.py \
  --output laya-results/smoke-offline.jsonl
```

Los 12 casos incluidos en `laya_smoke_cases.jsonl` son ejemplos sintéticos para
comprobar funcionamiento, incluyendo negaciones, propuestas y efectos múltiples.
Sus referencias legales son material de prueba, no afirmaciones sobre las normas.
No constituyen una evaluación representativa de precisión jurídica.

Para probar textos reales, pasar un JSONL con `text` y, opcionalmente, `id`,
metadatos de origen y `expected` (solo si existe una etiqueta independiente):

```bash
.venv-laya/bin/python try_laya.py --input muestra.jsonl \
  --output laya-results/muestra-clasificada.jsonl
```

Etiquetas: `sustituye`, `incorpora`, `deroga`, `modifica`, `ninguno`, `ambiguo`.
Conviene pasar una nota o pasaje por caso; el modelo recibe un máximo de 2.048
tokens. El resultado conserva entrada, hash, versiones de dependencias, preguntas,
respuesta completa, probabilidad de la etiqueta y comparación con el parser.
Los textos truncados (o sin telemetría de truncamiento) no cuentan como aciertos
ni acuerdos. El archivo comienza con metadatos y termina con un resumen; si falla
una ejecución, conserva lo ya procesado y no tiene resumen final.

El acuerdo con el parser no es precisión medida y la probabilidad del modelo no
es una validación jurídica. Todas las predicciones quedan como experimentales.
Los archivos de salida nunca se sobrescriben: usar un nombre nuevo para cada
corrida. Las pruebas de integración del script no descargan el modelo y están
incluidas en `python -m unittest discover -v -p 'test_*.py'`.

Fuentes de instalación: [Laya](https://github.com/NandhaKishorM/laya) y su
[configuración para macOS Intel](https://github.com/NandhaKishorM/laya/blob/main/LOCAL_SETUP.md).

### Primera ejecución local — 22/10/2026

Laya 0.4.13, checkpoint `convaiinnovations/laya` / `multilingual`, revisión
`7b928d828b7b0e022f929d9bd2e44165aa270148`, Python 3.11.17 y CPU Intel.
Las dos corridas completas se ejecutaron con `HF_HUB_OFFLINE=1`, sin ajustar
las preguntas entre ellas.

| Muestra | Resultado |
| --- | --- |
| 12 casos sintéticos etiquetados | 9 aciertos; 3 errores |
| 18 notas reales ya detectadas por las reglas | 18 acuerdos de tipo de cambio |
| 12 artículos sin cambio almacenado | 11 clasificados como `ninguno`; 1 como `sustituye` |
| Truncamiento informado | Ninguno en los 42 casos |

La muestra real incluye las primeras seis notas por ID de cada tipo disponible
(`deroga`, `incorpora`, `sustituye`), de hasta 1.500 caracteres. Los otros doce
casos son artículos actualizados de 80–900 caracteres sin `legal_changes`,
ordenados por `md5(article_version_id::text)`. No tiene etiquetas independientes
y no permite estimar precisión ni cobertura del corpus.

Errores sintéticos: trató una propuesta aún no aprobada como derogación
(probabilidad 0,9919); eligió una sola derogación ante dos efectos distintos;
y clasificó una negación explícita como sustitución. En el caso real
`article-version-208` detectó una sustitución que las reglas no extraían, pero
el pasaje también contiene derogaciones: su etiqueta única es incompleta.

Mediana por caso después de la primera inferencia: 0,338 s en los ejemplos y
0,402 s en la muestra real. La primera inferencia, que incluye carga del modelo,
tardó 13,594 s y 9,114 s respectivamente. Son medidas locales de muestras pequeñas.

Artefactos locales excluidos de Git: `laya-results/smoke-offline.jsonl`,
`laya-results/infoleg-sample.jsonl` y `laya-results/infoleg-classified.jsonl`.
Conclusión de esta prueba: útil como detector experimental de candidatos;
los resultados no justifican validar automáticamente por probabilidad.

### Decisión combinada del piloto

`try_laya.py` ahora agrega `decision`, `requires_review` y `review_reasons`.
El umbral predeterminado es `--min-probability 0.75`, inclusivo: 0,75 supera el
umbral; 0,749 no. Se usa la probabilidad de la etiqueta elegida por Laya,
sin promediarla con la confianza heurística del parser.

| Condición | Decisión |
| --- | --- |
| Un cambio explícito de artículo extraído por el parser, coincidencia con Laya, texto completo y probabilidad ≥ 0,75 | `aceptacion_automatica` |
| Parser sin extracción y Laya indica `ninguno` con probabilidad ≥ 0,75 y texto completo | `sin_cambio_detectado` |
| Probabilidad menor, desacuerdo, ambigüedad, varios cambios, alcance parcial sin identidad exacta, texto truncado o cambio detectado solo por Laya | `revision` |

La aceptación se refiere a la clasificación del fragmento en este piloto.
No comprueba la resolución de referencias en PostgreSQL ni la vigencia, y no
equivale a revisión humana. La cola web y `legal_changes.review_status` siguen
sin cambios. `sin_cambio_detectado` tampoco certifica ausencia de modificaciones.

Al reaplicar esta política a las predicciones guardadas (sin nueva inferencia):

- 30 textos reales: 11 propuestas de aceptación automática, 14 para revisión,
  5 sin cambio detectado. Siete casos de revisión tienen alcance de inciso o
  párrafo: el parser aún no extrae su identidad exacta.
- 12 ejemplos sintéticos: 4 propuestas de aceptación y 8 para revisión.
  Los tres errores conocidos de Laya quedan entre los ocho marcados.

Los resultados con los motivos están en `laya-results/infoleg-triage-075.jsonl`
y `laya-results/smoke-triage-075.jsonl`. El umbral es una política inicial, no
una probabilidad de acierto calibrada sobre legislación argentina; esta muestra
no establece la tasa de error de los casos aceptados.

## Trabajador Docker y rangos desde el administrador

```bash
docker compose up -d --build
```

En Gestión → **Descargar índices**, solicitar un rango inclusivo de hasta 1.000
IDs InfoLEG. Se ejecuta en segundo plano con progreso por índice, parser y
preparación de controles de índice. La cola `crawl_jobs` y los lotes se conservan
en PostgreSQL. Solo usuarios `is_staff` pueden crear o consultar solicitudes.
`worker.py` toma un bloqueo de sesión PostgreSQL para ejecutar un solo trabajador.
Un reinicio marca los trabajos interrumpidos como fallidos, sin borrar evidencia;
los pendientes siguen en cola. No se reintenta automáticamente un lote parcial.

La imagen ligera no instala Laya ni sus modelos. Para un lote descargado desde
el administrador, ejecutar después `prepare_review.py --dsn ... --run-id N` en
el entorno Laya existente. `--indices-only` permite preparar únicamente los
controles de índice sin importar ni cargar Laya. Las huellas evitan duplicarlos.

Las imágenes contienen los scripts y el SQL. `init` aplica el esquema antes de
web/crawler; no depende de que el volumen sea nuevo. Al cambiar código o SQL,
reconstruir con `docker compose up -d --build`. Los valores de conexión del
Compose corresponden al entorno local actual.

### Prueba de integración aislada

Además de las pruebas Django (`docker compose exec web python manage.py test`),
el trabajador tiene un caso completo con PostgreSQL real y red simulada:

```bash
docker compose exec db createdb -U juris_ai test_crawler_worker
docker compose run --rm --no-deps -e POSTGRES_DB=test_crawler_worker crawler python -c 'import os, unittest, worker; os.environ["CRAWLER_TEST_DSN"]=worker.database_dsn(); r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName("test_worker")); raise SystemExit(not r.wasSuccessful())'
docker compose exec db dropdb -U juris_ai test_crawler_worker
```

Usar una base nueva y descartable con prefijo `test_`. Comprueba inicialización
repetida, resultados parciales, articulado, controles de índice, conservación de
votos al repetir un rango, fallos de etapa y recuperación tras interrupciones.

### Salud del trabajador

La imagen incorpora `python worker.py --healthcheck`. Retorna 0 si PostgreSQL
responde y la sesión de este contenedor mantiene el bloqueo de la cola; retorna
1 si falta la sesión, el bloqueo pertenece a otro contenedor o falla la conexión.
No crea trabajos ni adquiere bloqueos. El servicio `init` lo desactiva porque es
un proceso de una sola ejecución. La prueba `test_worker` cubre sesión propia,
sesión ajena, ausencia y liberación del bloqueo y cierre de conexión.


## Pipeline por etapas

Ver la sección «Procesamiento local por etapas» del [README principal](../../README.md).
El build del crawler ahora usa la raíz del repositorio como contexto, porque comparte
`web/review/embedding.py` con la aplicación. Desde esta carpeta: `docker compose build init`.
Las dependencias de inferencia son `requirements-ml.txt` y Torch CPU 2.6.0.
La instalación mínima sigue sirviendo para descargar sin modelos.

Pruebas de integración: crear una base descartable cuyo nombre empiece por `test_`,
configurar `CRAWLER_TEST_DSN` con su DSN y ejecutar `python -m unittest discover -p 'test_*.py'`.
No usar la base del archivo jurídico. `test_worker.py` y `test_enrich.py` prueban
reintentos, conservación de votos, idempotencia, actualización de contenido y
escritura atómica; simulan red e inferencia. La prueba real con modelos del README
se ejecutó aparte sobre dos artículos copiados a otra base temporal.
