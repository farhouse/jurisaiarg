# Modelo de datos normativos — JurisAI

## 1. Objetivo

Definir la información que JurisAI debe almacenar para construir un archivo
normativo argentino buscable, trazable y capaz de representar modificaciones y
vigencia temporal.

El modelo separa:

1. la **norma** como identidad jurídica;
2. sus **versiones documentales**;
3. sus **artículos** y versiones de artículos;
4. las **relaciones** con otras normas;
5. las **fuentes oficiales** desde las que se obtuvo la información.

No debe asumirse que una norma tiene un único texto ni que todos sus artículos
comparten el mismo estado de vigencia.

## 2. Convenciones generales

- Identificadores internos: UUID o `bigint`; nunca usar el ID de InfoLEG como PK
  universal.
- Fechas desconocidas: `NULL`; no estimarlas.
- Valores extraídos automáticamente: guardar evidencia, método y confianza.
- Texto original: conservarlo sin alteraciones y generar por separado una
  versión normalizada para búsqueda.
- Toda relación debe poder apuntar a una norma completa o a un artículo concreto.
- La procedencia de cada dato debe ser auditable.

## 3. Entidades

### 3.1 `norms` — identidad de la norma

Una fila representa una norma jurídica, independientemente de las distintas
versiones de su texto.

| Campo | Tipo | Req. | Descripción |
|---|---|---:|---|
| `id` | UUID | sí | Identificador interno |
| `jurisdiction` | enum | sí | `nacional`, `provincial`, `caba`, `municipal` |
| `province_code` | texto | no | Código de provincia cuando corresponda |
| `norm_type` | enum | sí | Ley, decreto, DNU, resolución, disposición, etc. |
| `number` | texto | no | Número oficial conservando su formato |
| `year` | entero | no | Año identificatorio, si integra la referencia |
| `canonical_title` | texto | no | Denominación normalizada |
| `popular_name` | texto | no | Ej. “Ley de Contrato de Trabajo” |
| `issuing_body` | texto/ID | no | Organismo emisor normalizado |
| `scope` | texto | no | Ámbito material o territorial |
| `general_status` | enum | no | `vigente`, `derogada`, `vigencia_parcial`, `desconocida` |
| `infoleg_id` | entero | no | Identificador externo de InfoLEG |
| `created_at` | timestamp | sí | Alta interna |
| `updated_at` | timestamp | sí | Última actualización interna |

Restricción candidata: unicidad por jurisdicción, tipo, número, año y organismo.
Debe permitirse una excepción cuando la fuente no brinde datos suficientes.

### 3.2 `norm_dates` — hitos temporales

Una norma puede tener varias fechas jurídicamente relevantes.

| Campo | Tipo | Req. | Descripción |
|---|---|---:|---|
| `id` | UUID | sí | Identificador interno |
| `norm_id` | FK | sí | Norma |
| `date_type` | enum | sí | `sancion`, `promulgacion`, `publicacion`, `entrada_vigencia`, `derogacion` |
| `date_value` | fecha | sí | Fecha |
| `precision` | enum | sí | `exacta`, `mes`, `anio`, `inferida` |
| `evidence_id` | FK | no | Evidencia que respalda el dato |

### 3.3 `norm_sources` — fuente y documento recuperado

Registra cada página o documento oficial consultado. La misma norma puede tener
más de una fuente o varias capturas de una fuente.

| Campo | Tipo | Req. | Descripción |
|---|---|---:|---|
| `id` | UUID | sí | Identificador interno |
| `norm_id` | FK | sí | Norma asociada |
| `provider` | enum | sí | Inicialmente `infoleg` |
| `external_id` | texto | no | ID asignado por el proveedor |
| `url` | texto | sí | Link consultado |
| `document_kind` | enum | sí | `ficha`, `texto_original`, `texto_actualizado`, `pdf`, `html` |
| `retrieved_at` | timestamp | sí | Momento de descarga |
| `http_status` | entero | no | Estado HTTP |
| `content_hash` | texto | no | SHA-256 del contenido |
| `raw_content_path` | texto | no | Ubicación del HTML/PDF original |
| `parser_version` | texto | no | Parser utilizado |
| `parse_status` | enum | sí | `pendiente`, `ok`, `parcial`, `error` |
| `error_detail` | texto | no | Error de recuperación o parseo |

No debe sobrescribirse una captura anterior si cambia el contenido de la fuente.

### 3.4 `norm_versions` — versiones del texto

| Campo | Tipo | Req. | Descripción |
|---|---|---:|---|
| `id` | UUID | sí | Identificador interno |
| `norm_id` | FK | sí | Norma |
| `source_id` | FK | sí | Fuente de la versión |
| `version_type` | enum | sí | `original`, `actualizada`, `historica`, `desconocida` |
| `valid_from` | fecha | no | Inicio de vigencia conocido |
| `valid_to` | fecha | no | Fin de vigencia conocido |
| `publication_date` | fecha | no | Fecha de publicación de la versión |
| `full_text_original` | texto | no | Texto preservado |
| `full_text_normalized` | texto | no | Texto limpio para búsqueda |
| `text_hash` | texto | sí | Hash del texto normalizado |
| `is_current` | booleano | sí | Marca operativa, no prueba jurídica por sí sola |

### 3.5 `articles` — identidad lógica de artículos

| Campo | Tipo | Req. | Descripción |
|---|---|---:|---|
| `id` | UUID | sí | Identificador interno |
| `norm_id` | FK | sí | Norma |
| `article_label` | texto | sí | Etiqueta publicada: `1`, `1 bis`, `s/n`, etc. |
| `article_key` | texto | sí | Clave normalizada para vinculación |
| `ordinal` | entero | no | Orden de aparición |
| `heading` | texto | no | Título o epígrafe |

No convertir `article_label` a entero: existen artículos bis, ter, incorporados y
sin número.

### 3.6 `article_versions` — texto y vigencia por artículo

| Campo | Tipo | Req. | Descripción |
|---|---|---:|---|
| `id` | UUID | sí | Identificador interno |
| `article_id` | FK | sí | Artículo lógico |
| `norm_version_id` | FK | sí | Versión documental |
| `text_original` | texto | sí | Texto publicado |
| `text_normalized` | texto | sí | Texto para búsqueda |
| `valid_from` | fecha | no | Inicio de vigencia |
| `valid_to` | fecha | no | Fin de vigencia |
| `status` | enum | no | `vigente`, `sustituido`, `derogado`, `desconocido` |
| `introduced_by_norm_id` | FK | no | Norma que lo incorporó o sustituyó |
| `repealed_by_norm_id` | FK | no | Norma que lo derogó |

### 3.7 `norm_relations` — modificaciones y vínculos jurídicos

| Campo | Tipo | Req. | Descripción |
|---|---|---:|---|
| `id` | UUID | sí | Identificador interno |
| `source_norm_id` | FK | sí | Norma que produce el efecto |
| `target_norm_id` | FK | no | Norma afectada, si fue resuelta |
| `target_reference_raw` | texto | no | Referencia literal cuando aún no se resolvió |
| `source_article_id` | FK | no | Artículo que dispone el efecto |
| `target_article_id` | FK | no | Artículo afectado |
| `relation_type` | enum | sí | Tipo de vínculo |
| `effect_date` | fecha | no | Fecha desde la que opera el efecto |
| `scope_text` | texto | no | Alcance literal del cambio |
| `evidence_text` | texto | sí | Fragmento que prueba la relación |
| `extraction_method` | enum | sí | `fuente`, `regla`, `llm`, `manual` |
| `confidence` | decimal | no | Confianza entre 0 y 1 |
| `review_status` | enum | sí | `pendiente`, `validada`, `rechazada` |

Tipos iniciales de relación:

- `modifica`
- `sustituye`
- `incorpora`
- `deroga_totalmente`
- `deroga_parcialmente`
- `reglamenta`
- `complementa`
- `prorroga`
- `suspende`
- `restablece`
- `texto_ordenado_de`
- `citada_por`

La relación inversa (`modificada_por`, `reglamentada_por`, etc.) debe calcularse
en consulta y no duplicarse como otra fila.

### 3.8 `evidence` — procedencia de datos extraídos

| Campo | Tipo | Req. | Descripción |
|---|---|---:|---|
| `id` | UUID | sí | Identificador interno |
| `source_id` | FK | sí | Documento fuente |
| `locator_type` | enum | sí | `selector_html`, `pagina_pdf`, `rango_texto`, `articulo` |
| `locator` | JSON | sí | Ubicación reproducible |
| `quoted_text` | texto | no | Fragmento de respaldo |
| `extracted_at` | timestamp | sí | Momento de extracción |

### 3.9 `search_chunks` — índice semántico

| Campo | Tipo | Req. | Descripción |
|---|---|---:|---|
| `id` | UUID | sí | Identificador interno |
| `norm_id` | FK | sí | Norma |
| `article_version_id` | FK | no | Artículo/version, si aplica |
| `chunk_type` | enum | sí | `titulo`, `sumario`, `articulo`, `relacion` |
| `content` | texto | sí | Texto indexado |
| `embedding` | vector | no | Representación semántica |
| `embedding_model` | texto | no | Modelo y versión |
| `metadata` | JSONB | sí | Filtros de jurisdicción, tipo, fecha y vigencia |

## 4. Catálogos iniciales

Los siguientes valores deben administrarse como catálogos extensibles:

- jurisdicciones y provincias;
- tipos de norma;
- organismos emisores y aliases;
- tipos de fecha;
- estados de vigencia;
- tipos de relación;
- proveedores documentales.

No conviene codificar listas jurídicas cerradas dentro del crawler.

## 5. Datos mínimos para considerar una norma indexada

Una norma puede marcarse como `indexada` cuando tenga:

- fuente y link recuperable;
- identificador externo;
- tipo y número, o una identificación oficial equivalente;
- título o sumario;
- al menos una captura original con hash;
- texto completo o estado explícito de que no fue obtenido;
- fecha de recuperación;
- estado de parseo.

Para marcarla como `estructurada` debe tener además:

- artículos segmentados;
- metadatos principales;
- relaciones mencionadas identificadas, aunque algunas estén pendientes de
  resolución;
- evidencia de cada relación.

## 6. Reglas de calidad

- Nunca declarar una norma vigente únicamente porque la página siga disponible.
- Nunca inferir una derogación total a partir de una modificación parcial.
- No eliminar textos reemplazados: cerrar su intervalo temporal.
- Mantener la referencia literal cuando no pueda resolverse la norma destino.
- Permitir correcciones manuales sin destruir el dato originalmente extraído.
- Registrar versiones de crawler, parser, reglas y modelo semántico.

## 7. Alcance recomendado para validar el modelo

Antes de una carga masiva, validar con 20–30 normas laborales que incluyan:

- una ley con muchas modificaciones;
- decretos reglamentarios;
- artículos sustituidos e incorporados;
- derogaciones totales y parciales;
- un texto ordenado;
- referencias no resueltas o ambiguas.

La validación debe comprobar que el sistema puede responder:

1. cuál es la ficha de una norma;
2. qué normas la modificaron;
3. qué artículos fueron afectados;
4. qué evidencia sustenta cada vínculo;
5. qué texto estuvo vigente en una fecha determinada.
