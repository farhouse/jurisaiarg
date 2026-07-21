# Handoff técnico y funcional · JurisAI

Fecha de corte: 21 de julio de 2026.

## 1. Objetivo actual

JurisAI es un prototipo de archivo jurídico argentino construido sobre InfoLEG.
El alcance actual cubre la captura y estructuración de normas, la extracción de
relaciones modificatorias, la revisión humana de esas relaciones y una primera
interfaz de consulta de leyes.

El sistema no brinda asesoramiento jurídico ni determina por sí solo la vigencia
de una norma. Las relaciones producidas por el parser son propuestas que deben
ser confirmadas por una persona con criterio jurídico.

## 2. Componentes implementados

### Crawler de InfoLEG

- Descarga ficha, texto original, texto actualizado y páginas de vínculos.
- Conserva los bytes originales y una representación decodificada y sanitizada.
- Estructura normas, fuentes, versiones y artículos en PostgreSQL.
- Registra lotes, resultados individuales, errores y estados de indexación.
- Tolera páginas vacías, documentos parciales y caracteres NUL.
- Mantiene hashes para permitir reprocesamiento e idempotencia.

Código principal: `tools/infoleg_index/crawl_infoleg_pg.py` y
`tools/infoleg_index/parser.py`.

### Parser jurídico

- Detecta notas editoriales explícitas de modificación.
- Clasifica `sustituye`, `incorpora`, `deroga` y `modifica`.
- Identifica norma modificadora, artículo modificador, norma y artículo afectados.
- Conserva evidencia, fechas, confianza y versión del parser.
- Resuelve referencias contra las normas almacenadas.
- La extracción es idempotente mediante una huella única.
- Incluye auditoría de cobertura sintáctica.

Código principal: `legal_parser.py`, `extract_legal_changes.py` y
`audit_legal_parser.py`.

### Panel de revisión

- Requiere autenticación de Django.
- Presenta cada relación como una propuesta automática.
- Permite dejarla pendiente, confirmarla o marcarla como incorrecta.
- Confirmar no exige observaciones; marcar como incorrecta sí exige fundamento.
- Registra usuario y fecha de revisión.
- Muestra evidencia, normas involucradas y enlaces a InfoLEG.
- Cuando está disponible, muestra el texto completo del artículo modificador.
- Diferencia el artículo modificador del artículo afectado y su nota editorial.

### Explorador jurídico

- Busca por número, título, tema, resumen y contenido.
- Usa Full-Text Search de PostgreSQL con configuración en español.
- Interpreta consultas estructuradas como `derogación artículo 48`.
- Reconoce variantes frecuentes de derogar, sustituir, incorporar y modificar.
- Muestra ficha de norma, articulado disponible y enlaces a InfoLEG.
- Muestra relaciones confirmadas de entrada y salida.

### Preparación semántica

- PostgreSQL utiliza `pgvector/pgvector:pg16`.
- La extensión `vector` está habilitada.
- `search_chunks` conserva hash, embedding, modelo, versión y fecha de generación.
- No se eligió modelo ni se generaron embeddings todavía.
- La búsqueda actual es jurídica y léxica; aún no es semántica.

## 3. Arquitectura

```text
InfoLEG
  ↓
Crawler + parser HTML
  ↓
PostgreSQL 16 + pgvector
  ├── normas, fuentes, versiones y artículos
  ├── relaciones generales
  ├── cambios jurídicos extraídos
  └── fragmentos de búsqueda
       ↓
Parser jurídico determinístico
       ↓
Django
  ├── explorador de normas
  └── control humano del parser
```

La aplicación Django no administra el esquema jurídico: sus modelos principales
usan `managed = False`. Django solo administra autenticación, permisos y sesiones.

## 4. Modelo de datos relevante

- `norms`: identidad y estado de cada norma.
- `norm_dates`: sanción, promulgación, publicación, vigencia y derogación.
- `norm_sources`: páginas y documentos crudos recuperados.
- `norm_versions`: versiones original, actualizada o histórica.
- `articles`: identidad lógica de los artículos.
- `article_versions`: contenido y vigencia de cada versión.
- `norm_relations`: vínculos generales reportados por la fuente.
- `legal_changes`: modificaciones precisas extraídas por el parser.
- `search_chunks`: unidades de búsqueda textual y futura búsqueda vectorial.
- `crawl_runs`, `crawl_items`, `legal_parse_runs`: trazabilidad de procesos.

El esquema completo está en `tools/infoleg_index/schema.sql`. Para actualizar un
volumen anterior con soporte vectorial se puede aplicar
`tools/infoleg_index/upgrade_hybrid_search.sql`.

## 5. Puesta en marcha

Desde `tools/infoleg_index/`:

```bash
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Servicios:

- Web: `http://localhost:8000/`
- Explorador: `http://localhost:8000/explorar/`
- Revisión: `http://localhost:8000/changes/`
- Administración: `http://localhost:8000/admin/`
- PostgreSQL local: `localhost:55432`

No hay un usuario predeterminado en el repositorio. Debe crearse con
`createsuperuser`.

## 6. Operación de datos

Crear un entorno para las herramientas:

```bash
cd tools/infoleg_index
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Rastrear IDs explícitos:

```bash
.venv/bin/python crawl_infoleg_pg.py \
  --dsn 'postgresql://juris_ai:juris_ai_local@localhost:55432/juris_ai' \
  --ids 25552 229909
```

Extraer y auditar cambios jurídicos:

```bash
.venv/bin/python extract_legal_changes.py \
  --dsn 'postgresql://juris_ai:juris_ai_local@localhost:55432/juris_ai'

.venv/bin/python audit_legal_parser.py \
  --dsn 'postgresql://juris_ai:juris_ai_local@localhost:55432/juris_ai'
```

## 7. Pruebas

Herramientas de crawler y parser:

```bash
cd tools/infoleg_index
.venv/bin/python -m unittest discover -v -p 'test_*.py'
```

Aplicación web:

```bash
docker compose exec web python manage.py check
docker compose exec web python manage.py test
```

## 8. Estado conocido de los datos locales

El volumen Docker usado durante el desarrollo contiene el rastreo inicial de IDs
1 a 150 y las normas relacionadas descubiertas. La base local no forma parte del
repositorio; las cifras deben considerarse un estado de desarrollo reproducible,
no un dataset publicado.

Último estado observado:

- 1.708 normas, incluyendo normas descubiertas por relaciones.
- 1.863 fragmentos de búsqueda con hash.
- 89 relaciones jurídicas únicas en la corrida idempotente original; el estado
  local posterior contiene 94 registros por iteraciones adicionales del parser.
- 0 embeddings generados.
- 10 relaciones cuentan hoy con texto local del artículo modificador.

## 9. Limitaciones y riesgos conocidos

- InfoLEG no ofrece un endpoint estable de texto plano; se procesa HTML.
- Muchas normas relacionadas tienen ficha pero no articulado descargado.
- El estado de vigencia no debe inferirse solamente de una nota editorial.
- La búsqueda estructurada reconoce un vocabulario jurídico acotado.
- El explorador muestra únicamente relaciones confirmadas; el buscador puede usar
  propuestas pendientes, pero nunca relaciones marcadas como incorrectas.
- No existe todavía comparación visual entre versiones de un artículo.
- No existe pipeline de embeddings, evaluación de relevancia ni índice HNSW.
- El prototipo usa credenciales locales visibles en Compose; deben sustituirse en
  cualquier despliegue compartido o productivo.

## 10. Próximos pasos recomendados

1. Rastrear en profundidad las normas modificadoras referenciadas y completar sus
   artículos para mejorar la revisión humana.
2. Pedir a un abogado que revise una muestra representativa y documentar criterios
   de aceptación y errores frecuentes.
3. Crear un conjunto de consultas y resultados esperados para medir relevancia.
4. Elegir un modelo de embeddings adecuado para español jurídico y construir un
   generador incremental basado en `content_hash`.
5. Combinar ranking textual y vectorial con Reciprocal Rank Fusion.
6. Incorporar cronología, vigencia y comparación de versiones en el explorador.
7. Separar configuración de desarrollo y producción, agregar backups y CI.

## 11. Decisiones importantes

- PostgreSQL sigue siendo la fuente de verdad y el motor de búsqueda inicial.
- No se incorpora OpenSearch hasta que volumen o tráfico lo justifiquen.
- Las relaciones automáticas requieren trazabilidad y revisión humana.
- El texto crudo se conserva para poder corregir parsers sin volver a descargar.
- Los embeddings se versionarán y nunca reemplazarán las relaciones jurídicas
  estructuradas.
