# Plan de implementación — parser jurídico de modificaciones

## Objetivo

Transformar las notas editoriales presentes en los textos actualizados de
InfoLEG en cambios jurídicos estructurados, trazables y revisables.

Ejemplo:

> Artículo sustituido por art. 272 del Decreto N° 70/2023 B.O. 21/12/2023.

Debe producir:

- norma modificadora: Decreto 70/2023;
- artículo modificador: 272;
- norma afectada: la norma que contiene la nota;
- artículo afectado: el artículo que contiene la nota;
- efecto: `sustituye`;
- alcance: `articulo`;
- publicación: 2023-12-21;
- evidencia: nota editorial completa;
- método, versión, confianza y estado de revisión.

## Principios

- Una relación general de InfoLEG no prueba por sí sola el tipo de cambio.
- Cada cambio preciso debe conservar evidencia textual.
- La fecha del Boletín Oficial no se confunde con la fecha de vigencia.
- No se inventan identificadores: una referencia no resuelta se conserva como
  texto.
- El parser automático crea resultados `pendiente`; una revisión humana puede
  validarlos o rechazarlos.
- Las reglas y resultados son versionados e idempotentes.

## Taxonomía inicial

### Tipo de cambio

- `sustituye`
- `incorpora`
- `deroga`
- `modifica`

### Alcance afectado

- `articulo`
- `inciso`
- `parrafo`
- `capitulo`
- `titulo`
- `expresion`
- `desconocido`

## Fases

### Fase 1 — reglas explícitas

Reconocer fórmulas del tipo:

- “Artículo sustituido por art. … de la Ley …”
- “Artículo incorporado por art. … del Decreto …”
- “Artículo derogado por art. … de la Ley …”
- equivalentes para inciso, párrafo, capítulo, título y expresión.

Resolver la norma modificadora por tipo y número contra `norms`. Guardar la
referencia literal si no existe una coincidencia única.

### Fase 2 — vigencia y alcance fino

- Interpretar vigencia explícita.
- Diferenciar derogación total de artículo y derogación parcial.
- Reconocer sustituciones de rangos, anexos y enumeraciones.
- Vincular el artículo modificador cuando su texto también esté indexado.

### Fase 3 — evaluación

- Construir un conjunto manual de al menos 100 notas.
- Medir precisión y recall por tipo de cambio.
- Revisar falsos positivos por textos anexos o antecedentes normativos.
- Asignar umbrales para validación asistida.

### Fase 4 — asistencia semántica

Usar un modelo sólo para casos no cubiertos por reglas. Nunca publicar un cambio
inferido sin evidencia, confianza y estado de revisión.

## Criterio de aceptación de Fase 1

- extracción idempotente;
- evidencia reproducible;
- norma afectada y artículo siempre identificados;
- referencia modificadora siempre conservada;
- resolución automática cuando tipo y número son unívocos;
- cero abortos por notas no reconocidas;
- reporte de resueltas, no resueltas y distribución por tipo.
