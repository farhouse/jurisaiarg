# JurisAI

Proyecto: archivo normativo inteligente de legislación argentina.

- Objetivo inicial: almacenar normas, artículos, versiones, vigencia y relaciones
  modificatorias desde fuentes oficiales, comenzando por InfoLEG.
- Producto inicial: búsqueda exacta, textual y semántica con trazabilidad hacia
  la fuente oficial.
- Evolución futura: usar esta base normativa para enriquecer el análisis de
  sentencias judiciales.
- Estado: prototipo PostgreSQL + crawler estructurado de InfoLEG.

Ver `docs/brief.md`.

## Base del proyecto

El primer producto será un archivo normativo argentino basado inicialmente en
InfoLEG: normas, artículos, versiones, vigencia, relaciones modificatorias y
búsqueda semántica.

- Modelo de datos propuesto: `docs/modelo-datos-normativos.md`
- Idea y estado del índice InfoLEG: `docs/infoleg-index.md`
- Responsabilidades de parser, Laya, LLM y validadores:
  [procesamiento híbrido y guía del piloto](docs/procesamiento-hibrido.md).

El piloto aislado se abre desde **Gestión → Pilotos**. Trabaja con un lote ya
descargado, congela una muestra y su referencia revisada, y compara las propuestas
sin modificar el validador habitual. Para habilitar OpenRouter, configurar
`OPENROUTER_API_KEY` y `JURISAI_PILOT_MODEL` según [.env.example](.env.example),
usar el Compose actualizado y recrear `init`, `crawler` y `web`.
La clave se pasa solamente al crawler. Ver la [guía del piloto](docs/procesamiento-hibrido.md#uso-en-dockhand).

## Docker y administración

La aplicación incluye portada pública, validación humana, gestión por norma y
un formulario para ejecutar el crawler por rango de IDs InfoLEG.

```bash
# Desde la raíz del repositorio: construir y arrancar
docker compose -f tools/infoleg_index/compose.yaml up -d --build
```

Abrir `http://localhost:8000`. En una instalación nueva, crear el administrador:

```bash
docker compose -f tools/infoleg_index/compose.yaml exec web python manage.py createsuperuser
```

Desde **Gestión → Administrar validadores** se crean cuentas, se administra su
acceso y se consulta su actividad sin salir de la plataforma.

Desde **Gestión → Descargar índices** se ejecuta descarga, parser y preparación
de controles de índice en segundo plano. Laya se ejecuta por separado; ver
[la documentación del crawler](tools/infoleg_index/README.md) y
[la documentación de la aplicación](web/README.md).

## Imágenes en GitHub Container Registry

### Cargar en Dockhand

Usar el archivo independiente [compose.dockhand.yaml](compose.dockhand.yaml).
En **Stacks → Create stack**, elegir un nombre (por ejemplo `jurisaiarg`),
pegar el YAML y configurar estas variables en el panel del stack:

| Variable | Valor |
| --- | --- |
| `POSTGRES_PASSWORD` | Contraseña propia de PostgreSQL; marcar como secreto. |
| `DJANGO_SECRET_KEY` | Clave aleatoria propia de al menos 50 caracteres; marcar como secreto. |
| `DJANGO_ALLOWED_HOSTS` | IP del servidor o dominios separados por comas, sin protocolo ni puerto. |
| `JURISAI_PORT` | Opcional: puerto HTTP, por defecto `8000`. |

Para generar cada secreto por separado se puede usar `openssl rand -hex 32`.
El archivo fija la versión publicada `54ffe68`; `JURISAI_IMAGE_TAG` permite
seleccionar otro SHA publicado o `latest`. Las imágenes son Linux/amd64.
No hay que clonar código, compilar, montar carpetas ni configurar acceso a GHCR.
El servicio `init` termina con código 0 al preparar el esquema: es normal.

Al estar `web` saludable, abrir `http://IP_DEL_SERVIDOR:8000`. En una instalación
nueva, abrir la terminal del contenedor **web** desde Dockhand y ejecutar:

```bash
python manage.py createsuperuser
```

La base comienza vacía: este despliegue no copia los datos ni las cuentas de
la Mac. PostgreSQL queda accesible solo dentro del stack y los datos persisten
en su volumen. Mantener las claves y el nombre del stack al redesplegar;
cambiar `POSTGRES_PASSWORD` en las variables no cambia la contraseña de una
base ya inicializada. No eliminar el volumen al actualizar.

El acceso HTTP directo por IP/red local sigue disponible por defecto.
Para HTTPS mediante Cloudflare Tunnel, configurar en las variables del stack:

```text
DJANGO_ALLOWED_HOSTS=jurisai.farhouse.org
DJANGO_CSRF_TRUSTED_ORIGINS=https://jurisai.farhouse.org
DJANGO_TRUST_PROXY_HEADERS=true
DJANGO_SECURE_COOKIES=true
```

`DJANGO_ALLOWED_HOSTS` acepta hosts separados por comas, sin protocolo ni puerto.
`DJANGO_CSRF_TRUSTED_ORIGINS` acepta orígenes separados por comas, con `https://`
y sin barra final. El Compose agrega localhost para el healthcheck.
Las tres opciones nuevas deben estar dentro de `web.environment` del Compose;
agregarlas al panel de Dockhand con un YAML antiguo no las pasa al contenedor.

Confiar en `X-Forwarded-Proto` solo cuando el origen recibe tráfico mediante
un proxy confiable que sobrescribe ese header, como Cloudflare. No exponer el
puerto de origen directamente a Internet. Si cloudflared corre en el mismo host,
se puede usar `JURISAI_BIND_IP=127.0.0.1`; si corre en otro contenedor, debe poder
alcanzar `web:8000` por su red Docker. Las cookies seguras requieren entrar por
HTTPS para iniciar sesión; conservar `false` para una instalación solo HTTP.

Para actualizar: usar este Compose, quitar una etiqueta antigua de
`JURISAI_IMAGE_TAG` si está configurada, hacer pull y recrear `init`, `crawler` y `web`, conservando
`DJANGO_SECRET_KEY`, la contraseña de PostgreSQL y el volumen existente.
Volver a abrir el formulario de login antes de enviarlo. No es necesario crear
nuevamente el superusuario. La protección CSRF permanece activa.
El procesamiento local por etapas se describe en la sección siguiente; requiere actualizar las imágenes y recrear `init`, `crawler` y `web` juntos.
Referencia de la interfaz: [manual de Dockhand](https://dockhand.pro/manual/#creating-stacks).

### Publicación y uso con Docker Compose

El workflow `Publish Docker images` publica en cada push a `main` y también
puede ejecutarse manualmente desde Actions:

- `ghcr.io/farhouse/jurisaiarg-web:latest`
- `ghcr.io/farhouse/jurisaiarg-crawler:latest`

Cada publicación tiene además una etiqueta con el SHA completo del commit.
Las imágenes son Linux/amd64. PostgreSQL usa la imagen oficial de pgvector;
los datos se guardan en un volumen, no dentro de las imágenes publicadas.

Para ejecutar las imágenes publicadas sin compilar (desde `tools/infoleg_index`):

```bash
docker compose -f compose.yaml -f compose.ghcr.yaml pull
docker compose -f compose.yaml -f compose.ghcr.yaml up -d --no-build
```

Si los paquetes son privados, primero autenticarse en `ghcr.io` con un token
que tenga `read:packages`, suministrándolo a `docker login --password-stdin`.
La visibilidad de los paquetes se administra por separado de la del repositorio.
Para fijar una versión, definir `JURISAI_IMAGE_TAG` con el SHA publicado antes
de ambos comandos. No usar `down -v` al actualizar: elimina el volumen de datos.
La configuración incluida es para pruebas locales; antes de exponerla a Internet,
configurar credenciales propias, `DJANGO_SECRET_KEY`, hosts permitidos y HTTPS.

### Healthchecks de las imágenes

La imagen web consulta su login HTTP cada 15 segundos. Mantener `127.0.0.1`
en `DJANGO_ALLOWED_HOSTS` al usarla fuera de los Compose incluidos. Este chequeo
comprueba que Django atiende solicitudes; no verifica todo el acceso a datos.

La imagen del crawler comprueba cada 15 segundos que PostgreSQL responde y que
la sesión identificada con ese contenedor conserva el bloqueo exclusivo de la
cola. Funciona también con la cola vacía; no mide el avance de cada descarga ni
la disponibilidad de InfoLEG. El chequeo es de solo lectura y no toma el bloqueo.
El servicio puntual `init` desactiva el healthcheck heredado y termina con código 0.

Dockhand muestra el estado de salud de los contenedores. Un resultado `unhealthy`
por sí solo no reinicia un contenedor con Docker Compose; `restart: unless-stopped`
lo reinicia cuando su proceso termina. Para aplicar una imagen nueva hay que
hacer pull y recrear los contenedores, no solo reiniciarlos.


### Procesamiento local por etapas

En **Gestión → Descargar y procesar** se puede elegir:

1. **Descargar y procesar**: descarga el rango completo, ejecuta el parser,
   clasifica con Laya, genera embeddings y prepara los controles.
2. **Solo descargar**: guarda fuentes, extrae el articulado y relaciones, y permite
   revisar la fidelidad de la copia. No carga modelos.
3. **Procesar un lote descargado**: trabaja con el contenido local de sus normas,
   sin acceder a InfoLEG. Reutiliza resultados de ese lote por contenido/versión.

Un solo trabajador procesa los trabajos en orden. Laya y E5 se ejecutan en
subprocesos sucesivos para liberar la RAM entre etapas. Los resultados se guardan
por fragmento y los embeddings por documento en una transacción. Ante una
interrupción queda el trabajo fallido; un administrador puede volver a encolar
el lote terminado. Si se interrumpió la descarga, hay que solicitar los IDs que
faltan. No se reemplazan decisiones humanas al reintentar.

**Modelos y datos:** Laya 0.4.13 y su revisión fijada; embeddings con
[intfloat/multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small),
revisión `614241f622f53c4eeff9890bdc4f31cfecc418b3`, 384 dimensiones.
E5 usa prefijos `query:` y `passage:`, normalización y ventanas de 400 tokens
con 50 de solapamiento. No se descarta silenciosamente el final de un artículo.
Los pesos se descargan la primera vez desde Hugging Face y quedan en el volumen
`juris_ai_models`; no se envían textos jurídicos ni consultas a una API de inferencia.

La web carga E5 cuando llega la primera búsqueda por significado. Esto es trabajo
adicional a la indexación de documentos: cada consulta distinta necesita su
vector. Usa un proceso web con dos threads, serializa la inferencia y conserva
hasta 128 consultas en memoria. La búsqueda por palabras sigue disponible sin
cargar el modelo. La búsqueda semántica ofrece hasta 40 normas por similitud con
el fragmento que motivó cada resultado; no garantiza relevancia jurídica o vigencia.
Solo usa embeddings del contenido y modelo vigentes en el índice local. La versión
preferida es la última actualizada disponible, o la última original si falta aquella.

**Validación por norma:** una ficha reúne la copia completa, la propuesta temática
con contexto y las relaciones de toda la base. La copia se compara automáticamente
con el HTML guardado mediante un lector independiente y controles del articulado;
solo las diferencias concretas necesitan cotejo humano. Una aprobación humana cubre
esa versión completa, incluidos artículos y anexos, y se identifica por su contenido.
No se vuelve a aprobar por cada artículo ni por la llegada de una relación nueva.

Las materias y conceptos son propuestas, con procedencia explícita. Una predicción
ambigua, de baja confianza, truncada o excesivamente amplia produce una duda temática
por norma. Las sugerencias por vocabulario no reciben una probabilidad inventada.
Las materias no asignan fuero competente. Los conceptos provienen de vocabulario y
los embeddings se calculan sobre el texto; las etiquetas no reemplazan la búsqueda
por significado. Laya necesita evaluación con ejemplos revisados: su puntaje no
es una probabilidad calibrada de acierto jurídico.

`norm_references` registra citas y efectos explícitos, evidencia, versión y dirección.
La falta de una norma destino queda esperando vinculación automática, sin tarea humana
por esa sola causa. Cada lote vuelve a resolver referencias pendientes en toda la base.
Las relaciones entrantes posteriores aparecen en la ficha anterior sin modificar votos.
Los enlaces generales de InfoLEG se muestran aparte del efecto específico de un artículo.

**Actualizar un volumen existente:** descargar las imágenes nuevas, ejecutar `init` y
recrear `web` y `crawler`. En Gestión → Descargar y procesar, elegir **Procesar un lote
descargado**. Relee el HTML guardado con el parser nuevo, clasifica con contexto y
reutiliza los embeddings compatibles; no vuelve a descargar InfoLEG. El inicio migra
la cola antigua conservando tareas, votos, eventos y borradores. Una aprobación antigua
de identidad o artículo no se convierte en aprobación del documento completo.

**Límites:** no se garantiza vigencia, competencia judicial ni exhaustividad del catálogo.
No hay resumen generativo ni entrenamiento automático con los votos. Una fuente ausente
o PDF sin extracción queda pendiente de procesamiento. Las propuestas rechazadas y sus
correcciones se conservan separadas de la evidencia original.

**Verificación de la ficha:** 23 pruebas Django y 19 del crawler sobre bases descartables; comparador, borrador, confirmación y escritorio/móvil comprobados en Chrome. La inferencia real de Laya sobre la Ley 23.600 produjo materias demasiado amplias: se retienen para auditoría y la ficha propone Administrativo por vocabulario, con revisión temática única.

**Comprobación anterior:** 13 pruebas Django y 18 del crawler. En una prueba con
los artículos 1 de InfoLEG 1 y 2, en CPU dentro de Docker, el proceso completo tardó
84,69 s (incluyendo la primera descarga/carga de E5) y el máximo de los subprocesos
fue 1.817,8 MiB. Una consulta semántica en frío tardó 10,57 s con 728 MiB de pico,
y ordenó primero el artículo de expropiación para esa consulta. Es una prueba de
funcionamiento, no una medición de precisión ni de rendimiento para 50 normas.

Para compilar localmente desde la raíz:

```bash
docker compose -f tools/infoleg_index/compose.yaml build web init
```

`init` crea/actualiza las tablas, incluyendo `semantic_passages`. Al desplegar una
esta versión, recrear `init`, `crawler` y `web`, manteniendo los dos volúmenes.
El Compose de Dockhand fija las imágenes de `54ffe686eeca3f434c9daca9aee5ca5f787a370e`.
Si `JURISAI_IMAGE_TAG` tiene otro valor en el stack, actualizarlo a ese SHA o quitarlo
para usar el valor del archivo. Se necesita el Compose actualizado para montar
`juris_ai_models` en web y crawler y conservar los modelos entre actualizaciones.
El detalle de cada solicitud muestra descarga/carga de modelos, documento en curso,
última actividad y señal del trabajador; se actualiza cada 10 segundos.
