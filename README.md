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
El archivo fija la versión publicada `1203f13`; `JURISAI_IMAGE_TAG` permite
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
`JURISAI_IMAGE_TAG` si está configurada, hacer pull y recrear `web`, conservando
`DJANGO_SECRET_KEY`, la contraseña de PostgreSQL y el volumen existente.
Volver a abrir el formulario de login antes de enviarlo. No es necesario crear
nuevamente el superusuario. La protección CSRF permanece activa.
El procesamiento local por etapas se describe en la sección siguiente; requiere las imágenes de esta versión, todavía sin publicar.
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


### Procesamiento local por etapas (versión en desarrollo)

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

**Validación:** la copia de la norma, las relaciones jurídicas y las propuestas
sobre materias/conceptos son casos separados. Las materias son multietiqueta y
no representan el fuero competente. El catálogo inicial está en
`tools/infoleg_index/topics.py`: materias propuestas por Laya y conceptos detectados
por vocabulario. Todas las propuestas temáticas requieren revisión durante el
piloto; las relaciones conservan la regla de 0,75 más desacuerdos y faltantes.
Un rechazo registra la corrección sugerida en observaciones; no reescribe el texto
ni entrena automáticamente el modelo. El buscador vectoriza el texto, no estas
etiquetas. Las normas muestran etiquetas con acceso a la evidencia y estado humano.

**Límites del piloto:** no hay resúmenes generativos, clasificación judicial de
competencia, ajuste automático con votos, ni garantía de que el catálogo cubra
cada tema. Si se vuelve a descargar una norma antes de procesar un lote antiguo,
se procesa el contenido local más reciente; las copias de casos ya preparados
se conservan. Los casos antiguos sin copia guardada lo indican al mostrar el texto
local actual. La comparación semántica es exacta en PostgreSQL; evaluar HNSW cuando
crezca el volumen. Calibrar las materias con ejemplos revisados antes de automatizar
su aprobación: la muestra inicial tuvo omisiones y dudas.

**Comprobación local:** 13 pruebas Django y 18 del crawler. En una prueba con
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
futura publicación, recrear también `init`, `crawler` y `web`, manteniendo los dos
volúmenes. El Compose de Dockhand conserva por ahora su etiqueta publicada anterior;
seleccionar el nuevo SHA cuando se autorice su publicación. Esta versión no hace
push ni publica imágenes automáticamente desde la máquina local.
