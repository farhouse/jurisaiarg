# Responsabilidades y resultados del procesamiento híbrido de JurisAI

Estado: especificación y guía del piloto aislado. Fecha: 9 de noviembre de 2026.

JurisAI debe convertir fuentes oficiales en normas consultables por identidad,
palabras y significado, con evidencia de cómo obtuvo cada dato. El resultado
central es una ficha por norma y versión: copia comprobable, resumen, materias,
conceptos y relaciones. La revisión humana debe resolver observaciones concretas.

El reparto propuesto conserva el parser, Laya y los embeddings locales. Agrega una
LLM para el análisis que necesita contexto o generación de contenido. La API se integra únicamente en el piloto: sus propuestas y evaluaciones permanecen
separadas de los datos oficiales y del validador habitual.

## Reparto de responsabilidades

| Responsable | Tarea principal | Resultado esperado | Límite |
| --- | --- | --- | --- |
| Crawler | Descargar ficha, textos y anexos disponibles; registrar origen y errores. | Fuentes conservadas, fecha de descarga y estado por recurso. | Una descarga exitosa no prueba que el contenido esté completo. |
| Parser y reglas | Extraer identidad y estructura; comparar la copia; detectar referencias y efectos explícitos. | Datos trazables, diferencias localizadas y candidatos de relaciones. | No redactan interpretaciones ni completan datos ausentes por suposición. |
| Laya | Clasificar pasajes acotados entre opciones cerradas. En el piloto, contrastar el tipo de efecto de una relación candidata. | Etiqueta, puntuación interna y constancia del texto procesado. | No extrae por sí solo origen, destino y alcance de una modificación. Su puntuación no equivale a exactitud jurídica. |
| LLM convencional | Analizar el contexto de la norma; redactar resumen; proponer materias y conceptos; resolver relaciones ambiguas. | Propuesta estructurada con evidencia y dudas específicas. | No certifica la copia, asigna IDs de base ni determina vigencia actual sin antecedentes suficientes. |
| Modelo de embeddings | Vectorizar texto y consultas para recuperar contenido por significado. | Vectores vinculados al fragmento y versión que los originaron. | La similitud no prueba validez, vigencia ni una relación modificatoria. |
| Validador | Confirmar, corregir o dejar sin resolver una observación con su evidencia. | Decisión, corrección, usuario, fecha y alcance de lo revisado. | Una aprobación no se extiende a otros campos o versiones que no se revisaron. |
| Administrador | Supervisar lotes, errores, calidad y carga de revisión; habilitar políticas de automatización evaluadas. | Estado del trabajo y métricas comprensibles por norma y tarea. | No debe necesitar intervenir en cada descarga o artículo. |

## Qué hace el parser

1. Extrae tipo y número de norma, organismo, título y fechas cuando la fuente los
   informa. Mantiene separados sanción, promulgación, publicación y vigencia.
2. Separa texto original y actualizado, artículos, anexos y notas editoriales;
   conserva la relación con la fuente utilizada.
3. Compara la copia con una lectura independiente del HTML guardado de la misma
   versión. Comprueba texto, orden y contenido del articulado.
4. Normaliza únicamente diferencias técnicas definidas. No borra números,
   negaciones, títulos jurídicos o párrafos para conseguir una coincidencia.
5. Detecta referencias y fórmulas explícitas de sustitución, incorporación,
   derogación o modificación. Conserva el pasaje que las fundamenta.
6. Vincula referencias contra toda la base cuando la identidad es inequívoca;
   reintenta las pendientes cuando ingresan nuevas normas.

Una comparación satisfactoria comprueba la copia del recurso disponible, no que
InfoLEG represente toda la historia normativa. Si hay un PDF, imagen o anexo sin
texto comprobable, registra esa cobertura faltante. Un modelo no puede suplirla
inventando el contenido.

El título técnico de la página de InfoLEG se excluye solamente bajo la regla
específica ya implementada y respaldada por el HTML. El documento y su historial
permanecen conservados.

## Qué hace Laya

La primera tarea candidata es contrastar un efecto detectado por el parser sobre
un pasaje breve y completo. Las opciones son `sustituye`, `incorpora`, `deroga`,
`modifica`, `ninguno` y `ambiguo`. Las dos últimas significan, respectivamente,
ausencia de modificación explícita y efecto no determinable como una sola etiqueta.

Laya clasifica el efecto del pasaje; el parser debe haber identificado la referencia
y su evidencia. Una etiqueta correcta no demuestra que el destino o el alcance
sean correctos. Si el pasaje contiene varios efectos, se divide únicamente cuando
puede conservarse el contexto; en caso contrario pasa al análisis contextual.

La clasificación de materias con Laya queda como comparación experimental en el
piloto. La implementación actual ya la intenta, pero observamos propuestas demasiado
amplias. No la convertimos en responsable principal de materias hasta medir su
calidad por categoría. Tampoco lo usamos como filtro único: que Laya no detecte un
efecto no descarta una referencia detectada por el parser.

Una entrada truncada o cuya cobertura no puede comprobarse produce un resultado
incompleto. No habilita aceptación automática. La viabilidad de cada tarea de Laya
se decide por resultados observados, no por mantenerlo ocupado.

## Qué hace la LLM

### Ficha semántica de la norma

Recibe la identidad extraída, la versión y el contenido completo disponible, con
identificadores estables de artículos y anexos. Produce en un mismo análisis:

- Un resumen breve de qué dispone la norma, con referencias que respalden sus
  afirmaciones. No agrega consecuencias jurídicas que el texto no establece.
- Materia principal y materias secundarias cuando sean sustantivas, con evidencia
  y una justificación breve. Puede abstenerse si el catálogo no alcanza.
- Conceptos normalizados para búsqueda, vinculados a las expresiones que los
  fundamentan. Distingue lo que la norma regula de una mención incidental.
- Función de los artículos y conceptos específicos cuando aporten información
  adicional. El contexto de la norma se conserva como contexto, sin presentar cada
  materia heredada como regulación específica de todos sus artículos.
- Dudas localizadas: campo afectado, motivo y evidencia necesaria para resolverlo.

La materia es el tema jurídico. El fuero o competencia judicial se guarda aparte,
solo si existe fundamento explícito o una fuente apropiada. Puede quedar sin
determinar sin generar una tarea humana innecesaria. Tampoco se confunde con la
jurisdicción de la norma. Mencionar un tribunal no basta para completar estos campos.

El catálogo inicial reutiliza las materias existentes: Civil y comercial, Laboral,
Familia, Penal, Administrativo, Tributario, Constitucional, Seguridad social,
Ambiental y Consumidor. Si ninguna corresponde, se registra la insuficiencia y una
sugerencia para revisar el catálogo; no se crea una categoría distinta en cada lote.

### Relaciones que necesitan contexto

Recibe el pasaje candidato, la identidad de la norma que lo contiene, las
referencias detectadas y el contexto necesario. Propone:

- Tipo de relación y si expresa una cita o un efecto normativo.
- Norma y disposición de origen y de destino, conservando la referencia literal.
- Alcance total o parcial, con el inciso, párrafo, anexo o expresión cuando el
  texto permita identificarlo.
- Evidencia literal y explicación breve de la dirección de la relación.

Una nota editorial y una disposición operativa pueden describir el mismo cambio
desde perspectivas diferentes. El sistema identifica quién modifica a quién y
evita duplicar el efecto. Si no puede resolverlo, devuelve una duda concreta.

Los identificadores locales y los enlaces a normas los resuelve el sistema. Si el
destino todavía no está descargado, conserva la relación como pendiente de enlace;
eso no significa que el análisis haya fallado ni que alguien deba buscarlo a mano.

## Resultado común y evidencia

El contrato es independiente del proveedor. El esquema ejecutable compartido está
en `web/review/pilot_contract.py`; los bloques siguientes describen la ficha y
los metadatos que agrega el sistema.

| Bloque | Contenido mínimo |
| --- | --- |
| `fuente` | ID InfoLEG, identidad local, versión original o actualizada, recurso guardado y hash del contenido. Estos datos los agrega el sistema. |
| `identidad` | Tipo, número, organismo, título y fechas extraídas, cada una con su procedencia. |
| `integridad` | Coincide, hay diferencias o no comprobable; recursos cubiertos y diferencias localizadas. |
| `resumen` | Texto breve y referencias a los fragmentos que respaldan sus afirmaciones. |
| `materias` | Categoría principal y secundarias, evidencia, justificación y contexto de aplicación. |
| `conceptos` | Etiqueta normalizada, expresiones de respaldo y ubicaciones; distinguir conceptos centrales de menciones. |
| `articulos` | Identificador, función, contexto heredado y aportes específicos, si existen. |
| `relaciones` | Origen, destino, tipo, alcance, referencia literal, evidencia y estado del enlace. |
| `dudas` | Código, campo afectado, motivo comprensible y acción necesaria. |
| `procedencia` | Herramienta, modelo y revisión disponibles, proveedor efectivo, versiones de instrucciones, esquema y catálogo, fecha y cobertura de entrada. |
| `revision` | Estado automático o humano por campo, decisiones y correcciones con su historial. |

Cada evidencia apunta a una versión y a un artículo, anexo o tramo del documento,
con una cita literal verificable. El sistema comprueba que existe en el contenido
procesado y que los identificadores devueltos pertenecen a la entrada. Una cita
existente es necesaria, pero no demuestra por sí sola que la conclusión sea correcta.

`null` o una lista vacía deben acompañarse de un estado que distinga no aplica,
no identificado, pendiente de procesamiento y error. Una respuesta vacía por fallo
de API nunca se presenta como ausencia de relaciones o conceptos.

Las propuestas automáticas se guardan separadas de los datos oficiales y las
correcciones humanas. Cada campo conserva su procedencia; revisar una materia no
valida por extensión el resumen, las relaciones o el texto de la norma.

## Cuándo interviene cada herramienta

1. Se descargan los recursos y se ejecutan extracción y controles de integridad.
   Si falta contenido necesario, se señala qué análisis queda afectado.
2. El parser genera referencias candidatas. Laya contrasta solo los pasajes
   compatibles con su tarea y límite de entrada, sin cargarlo para normas sin
   candidatos pertinentes.
3. La LLM genera la ficha semántica por versión y contenido, reuniendo resumen,
   materias y conceptos en una sola tarea. No repite una llamada por cada etiqueta.
4. La LLM analiza además las relaciones no resueltas por las reglas: desacuerdos,
   efectos múltiples, destinos ambiguos o alcances parciales no identificados.
   Las relaciones claras no requieren otra llamada cuando la política evaluada
   permita conservarlas como propuestas automáticas.
5. El sistema valida estructura, cobertura y evidencia; registra resultados y
   prepara observaciones. El modelo no decide por sí solo la aprobación final.
6. E5 indexa el texto fuente por fragmentos con su versión y procedencia. Puede
   hacerlo sin esperar una aprobación temática; las etiquetas son filtros y ayudas
   adicionales. Los resúmenes generados no reemplazan al texto fuente en el índice.

El piloto envía el contenido disponible completo. Si supera el presupuesto
conservador del contexto, informa que requiere análisis por partes, sin truncarlo.
La división con contexto y síntesis global se implementará cuando los casos
evaluados la justifiquen. La unidad que ve el validador sigue siendo la norma.

## Qué valida una persona

Durante el piloto se revisan todas las propuestas semánticas y de relaciones.
Después, la cola habitual contiene excepciones y una muestra de control de los
resultados automáticos. El acuerdo entre parser, Laya y LLM no reemplaza esa medición.

| Observación | Qué debe ver y decidir el validador |
| --- | --- |
| Diferencia de copia | Fuente y copia de la misma versión, con el tramo distinto resaltado. Indica si falta, sobra o cambió contenido, o si es una diferencia técnica admisible. |
| Materia dudosa | Propuesta, definición de la categoría y evidencia. Confirma, cambia o declara insuficiente el catálogo. |
| Concepto dudoso | Etiqueta, contexto y pasaje de respaldo. Determina si es pertinente, incidental o incorrecto; puede corregirlo. |
| Resumen dudoso | Afirmación concreta y pasajes que deberían respaldarla. Corrige omisiones o agregados sin fundamento. |
| Relación ambigua | Origen, destino, efecto y alcance propuestos junto con el pasaje. Corrige el campo problemático o deja la duda fundamentada. |

No se pide aprobar automáticamente todos los artículos ni volver a comparar cada
uno si la copia completa de esa versión ya fue comprobada. Se muestran a demanda
para entender el contexto. Tampoco se reabre una aprobación de texto por la llegada
de una relación posterior.

La pantalla debe explicar qué campo necesita atención, por qué y cómo resolverlo.
Debe permitir corregir el dato propuesto y conservar la decisión independiente de
cada persona. Una diferencia en un campo no marca toda la norma como incorrecta.

No se usa una puntuación `.75` como regla universal. Laya aporta su puntuación
interna; una LLM puede declarar incertidumbre, pero su confianza autodeclarada no
es una probabilidad de acierto. Las políticas de automatización se calibran por
tarea con ejemplos revisados y contemplan desacuerdo, evidencia y cobertura.

## Ejemplo esperado con la Ley 23.600

Este ejemplo define el resultado deseado a partir del texto compartido en el
proyecto; no es una salida ya obtenida de la nueva LLM.

| Componente | Resultado esperado |
| --- | --- |
| Identidad y copia | Ley 23.600, cuatro artículos. El control automático informa si coinciden con la misma fuente y versión. |
| Resumen propuesto | Declara de utilidad pública y sujeto a expropiación un inmueble destinado al Poder Judicial de la Nación, faculta a la Corte Suprema a determinar sus dependencias y establece la imputación de los gastos de adquisición. |
| Materia propuesta | Administrativo como principal, sustentada por la expropiación del artículo 1. La mención de tasas no basta para asignar una materia tributaria. |
| Conceptos | Expropiación, utilidad pública e inmueble, respaldados por el artículo 1; Poder Judicial y Corte Suprema con sus ubicaciones correspondientes. |
| Artículos | El 1 dispone la expropiación; el 2 faculta a determinar las dependencias; el 3 trata la financiación; el 4 es la comunicación formal. No originan cuatro revisiones temáticas obligatorias. |
| Relación | El artículo 3 cita o remite al artículo 19 de la Ley 21.859 para imputar gastos. Esa mención no prueba una modificación de la ley citada. |
| Fuero y vigencia actual | Sin determinar con este texto. No se completan por la sola mención del Poder Judicial ni por la fecha de la ley. |

Por ejemplo, el concepto «Utilidad pública» se apoya en la cita del artículo 1
«Declárase de utilidad pública y sujeto a expropiación». La relación del artículo 3
se apoya en «Los gastos necesarios para su adquisición se imputarán a la cuenta
prevista en el artículo 19 de la Ley 21.859».

Una norma posterior puede citar, modificar o derogar esta norma. Al incorporarla,
se agrega la relación entrante y se reintenta su enlace contra toda la base. La
aprobación de la copia de la Ley 23.600 conserva su alcance original.

## Ejecución, costes y trazabilidad

La tarea de la LLM se ejecuta desde el trabajador del crawler por etapas, utilizando
la copia guardada. El proveedor y el modelo serán configurables; el contrato de
salida y la política de revisión pertenecen a JurisAI.

Se pide JSON Schema estricto cuando el endpoint lo admite y se valida la respuesta
localmente. Groq documenta modo estricto para `openai/gpt-oss-120b`, pero aclara que
cumplir el esquema no garantiza exactitud semántica. En OpenRouter la compatibilidad
depende también del proveedor; debe exigirse soporte de los parámetros utilizados.
[Groq Structured Outputs](https://console.groq.com/docs/structured-outputs),
[OpenRouter Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs).

El piloto usa OpenRouter con el modelo fijo
`nvidia/nemotron-3.5-super-vl:free`. Antes de analizar consulta sus endpoints y
exige precio cero y soporte anunciado de JSON Schema. Cada solicitud exige los
parámetros y precio de entrada y salida cero. La respuesta se valida localmente;
se rechazan citas inexistentes, unidades ajenas y respuestas incompletas. Si no
hay clave, endpoint o cupo, el piloto queda en atención para retomarlo. No cambia
silenciosamente de modelo ni pasa a uno pago.

Los reintentos tienen límite y respetan las indicaciones de espera del servicio.
El administrador ve etapa, documento, último avance, error, solicitudes, tokens y
coste informado cuando esté disponible. Los secretos se configuran fuera del código.

Un resultado se reutiliza si coinciden contenido, contexto, tarea, modelo y versiones
de instrucciones, catálogo y esquema. Cambiar de lote no obliga a repetir un análisis
idéntico; cambiar el texto o las instrucciones sí puede requerir una nueva propuesta.
Una búsqueda nueva necesita su embedding de consulta, aunque el corpus ya esté
indexado. Las nuevas propuestas no sobrescriben revisiones humanas anteriores.

## Piloto y criterios de aceptación

Comenzamos con 30 normas ya descargadas: 20 para ajustar instrucciones y 10
reservadas para comprobar el resultado sin seguir ajustándolo sobre esos ejemplos.
Incluimos normas simples y largas, varias materias, citas sin modificación, efectos
explícitos, alcances parciales, notas editoriales, anexos y referencias sin destino
descargado. Este tamaño sirve para detectar fallos; no demuestra por sí solo que
todo el corpus pueda aprobarse automáticamente.

La referencia esperada se prepara por campo a partir de la fuente; no se toma el
resultado del modelo como respuesta correcta. Comparamos Laya con las etiquetas
cerradas esperadas y la LLM con los campos que le corresponden. Para materias,
Laya participa en modo experimental para decidir si conviene delegarle parte de
esa tarea en el futuro.

Medimos precisión y omisiones por materia y tipo de relación; exactitud de origen,
destino y alcance; conceptos útiles o espurios; afirmaciones del resumen sin respaldo;
porcentaje de casos derivados; tiempo humano, latencia, tokens y coste por norma.
También se informa cuántos resultados no pudieron producirse y por qué.

Antes de habilitar una tarea automática deben cumplirse estas condiciones:

- Todas las respuestas aceptadas cumplen el esquema y la cobertura registrada;
  los fallos se conservan como fallos, sin convertirlos en resultados vacíos.
- Las evidencias y los identificadores de entrada son verificables. Los casos sin
  respaldo se rechazan o derivan explícitamente.
- La Ley 23.600 no produce revisiones obligatorias por sus cuatro artículos ni una
  modificación inventada por su cita a la Ley 21.859.
- Una referencia todavía no descargada queda pendiente de enlace; una norma nueva
  puede resolverla sin borrar decisiones previas.
- Reintentar un lote conserva propuestas, votos y correcciones, y reutiliza lo
  que corresponde por contenido y versiones.
- Los errores de cada tarea están medidos en los ejemplos reservados. El objetivo
  de precisión y el muestreo posterior se fijan por tarea antes de automatizarla;
  si la evidencia es insuficiente, se amplía la evaluación.

## Uso en Dockhand

Cuando estén publicadas las imágenes de esta versión, actualizar el Compose y
recrear `init`, `crawler` y `web`, conservando el volumen y las credenciales de la
base. `init` agrega las tablas aisladas del piloto y su modalidad en la cola.

Configurar en el `.env` que lee el stack, o en las variables de Dockhand:

```dotenv
OPENROUTER_API_KEY=tu_clave
JURISAI_PILOT_MODEL=nvidia/nemotron-3.5-super-vl:free
```

El Compose pasa la clave solamente a `crawler`; el nombre del modelo también
llega a `web` para fijarlo al crear el piloto. Un `.env` no envía por sí solo
variables nuevas a los contenedores: hay que usar el Compose actualizado y
recrearlos. `.env` queda fuera de Git y `.env.example` no contiene secretos.
La clave nunca se guarda en el piloto, en el navegador o en los logs.

1. Entrar como administrador en **Gestión → Pilotos** y elegir un lote terminado.
2. Preparar la muestra. El trabajador conserva el HTML y su extracción dentro del
   piloto, sin descargar ni modificar la extracción jurídica existente.
3. Abrir cada norma y revisar sus cinco bloques de referencia. El borrador usa
   reglas, vocabulario y sumario oficial; la Ley 23.600 incluye el ejemplo
   fundamentado de este documento. Las demás referencias pueden necesitar
   completar el resumen o la función de los artículos. No se consideran respuestas
   correctas hasta que una persona las revise. Se puede señalar una duda jurídica
   para excluir ese bloque de la comparación automática.
4. Agregar consultas cotidianas de prueba. Los cambios en los bloques se guardan
   automáticamente; cada decisión conserva usuario, fecha y revisión. La navegación
   espera el guardado y los errores se muestran junto al bloque afectado.
5. Volver al piloto y elegir **Congelar referencia e iniciar análisis**. Todos los
   bloques deben tener una decisión; una duda jurídica cuenta como decisión, no
   como referencia correcta. La referencia deja de ser editable.
6. El trabajador comprueba OpenRouter, ejecuta Laya en un subproceso que termina,
   y luego analiza las fichas mediante API. Las entradas demasiado grandes se
   informan sin truncamiento; el análisis por partes queda para una ampliación.
7. Revisar las propuestas frente a la fuente y la referencia congelada. Corregir
   campos mantiene las evidencias conservadas y permite añadir citas para datos
   nuevos. Una norma referida ausente se muestra como pendiente de incorporación.
8. Consultar el informe y exportar fuentes, resultados e historial. Un intento
   fallido conserva las respuestas anteriores; **Retomar piloto** reutiliza las
   tareas terminadas cuando coinciden contenido y versiones.

El informe distingue los grupos de ajuste y evaluación reservada. Muestra
coincidencias y diferencias de etiquetas y relaciones; evaluaciones por persona y
tarea; contraste de efectos y materias de Laya; fallos; solicitudes, tokens,
latencia y coste cuando el servicio lo informa. El tiempo de edición es una
aproximación de la interacción con los formularios, no toda la lectura jurídica.
Las puntuaciones de Laya y el acuerdo de etiquetas no se presentan como exactitud
jurídica. Las recomendaciones son exploratorias y no habilitan aprobación automática.

## Límites de esta entrega

El piloto no publica resúmenes, etiquetas o relaciones en el buscador, no cambia
E5 y no altera votos ni borradores habituales. Las consultas de ejemplo abren
la búsqueda actual como línea base: todavía no prueban una mejora del ranking.

La primera entrega está limitada a administradores. El reparto entre colaboradores
y revisores jurídicos, el tratamiento por partes de documentos que excedan el
contexto y la incorporación de resultados al flujo habitual se decidirán a partir
de la evaluación. Las reglas determinísticas y Laya siguen siendo experimentales
para los campos donde no exista una referencia jurídica revisada.

La implementación se verifica con PostgreSQL descartable y respuestas simuladas
de API. La calidad real del endpoint se medirá al ejecutar este piloto en el
servidor con las fuentes del lote y la clave configurada por su administrador.
