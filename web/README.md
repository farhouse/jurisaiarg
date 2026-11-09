# JurisAI Review

Aplicación con portada y guía públicas, y una mesa de revisión con acceso
autenticado para validar o rechazar los cambios jurídicos extraídos de InfoLEG.

## Inicio local

Desde `tools/infoleg_index/`:

```bash
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Abrir `http://localhost:8000/` para leer el manifiesto. Usar «Ingresar» con el
usuario creado para acceder al trabajo de revisión.

Para probar desde otro dispositivo de la misma red, abrir
`http://192.168.1.152:8000/`. La aplicación mantiene el ingreso con usuario y
contraseña y desactiva el modo debug. PostgreSQL solo publica su puerto en
localhost. Si cambia la IP de la Mac, actualizar `DJANGO_ALLOWED_HOSTS` y recrear
el servicio web con `docker compose up -d web` (un reinicio no actualiza variables
de entorno).

## Arquitectura

- Django administra usuarios, permisos y sesiones.
- Los modelos jurídicos usan `managed = False`: Django no crea ni modifica las
  tablas del crawler/parser.
- Los controles de norma completa se guardan en `norm_reviews`, con una fila
  por tarea de índice y ID estable de usuario. El lote identifica la descarga
  revisada. Cambiar el nombre de usuario no crea un voto adicional.
- Los pasajes conservan su decisión vigente por caso y actualizan la relación
  correspondiente en `legal_changes`, si existe.

## Pantallas

- `/`: portada pública con el manifiesto del proyecto y acceso a la guía.
- `/trabajo/`: destino después del ingreso; envía a gestión si es administrador,
  o a la cola si es validador.
- `/validar/`: mesa de trabajo, filtrable por lote, tipo, estado e ID/norma.
- `/validar/guia/`: guía pública del validador con criterios, ejemplos y pasos de revisión.
- `/validar/<id>/`: evidencia, fuente, motivos, decisión e historial del caso.
- `/gestion/`: tabla por norma con votos correctos, incorrectos y pendientes,
  búsqueda y filtros de desacuerdos o sin revisiones. Cobertura de descarga y
  resultados de pasajes en un panel desplegable. Requiere `is_staff`.
- `/gestion/leyes/<id de tarea>/`: ficha de una norma con resultados, personas,
  fechas, observaciones e historial. Requiere `is_staff`.
- `/changes/`: cola con filtros, búsqueda y paginación.
- `/changes/<id>/`: evidencia, contexto y formulario de revisión.
- `/admin/`: gestión de usuarios y consulta de relaciones históricas.

## Roles y decisiones

Los validadores deben pertenecer al grupo Django `Validadores`. Los
administradores usan `is_staff`; para gestionar usuarios necesitan los permisos
de autenticación correspondientes o ser superusuarios. No hay registro público.
La cuenta existente `farhouse` conserva su acceso y su contraseña.

Para la prueba local está creada `validador_local`, únicamente en el grupo
`Validadores`. Su contraseña aleatoria está en `web/.local-access.json`, fuera
de Git y con permisos de archivo `0600`. Usar cuentas individuales para atribuir
el trabajo de cada persona.

Desde **Gestión → Administrar validadores** (`/gestion/validadores/`) se crean cuentas individuales con el grupo `Validadores` asignado automáticamente. Permite buscar cuentas, editar nombre y correo, activar/desactivar el acceso y consultar decisiones por norma y lote, más los últimos 20 eventos de pasajes. Las revisiones se conservan al desactivar una cuenta.

Solo administradores (`is_staff`) acceden a este panel. No permite conceder roles administrativos, modificar administradores, renombrar ni eliminar cuentas. Las contraseñas nuevas usan la validación de Django; el administrador comparte las credenciales por un canal privado, sin envío automático de correo. Django Admin queda disponible en `/admin/` como herramienta técnica, fuera de la navegación habitual.

Cada decisión registra usuario, fecha, estado previo y observación. Marcar
incorrecto exige un motivo. En los índices, cada persona guarda su propia
decisión, aunque otra ya revisó la misma norma. Cambiarla o reabrirla actualiza
solo su fila; los cambios siguen en `review_events`. Los formularios usan una
revisión numérica por persona para rechazar formularios propios desactualizados
(HTTP 409), sin bloquear el voto independiente de otro usuario.

Los contadores del administrador son por norma y lote: cada persona cuenta
una vez, según su última decisión. Pendiente significa que guardó explícitamente
ese estado; no haber revisado no suma un pendiente. Los pasajes, los eventos
históricos y los resultados automáticos no inflan estos votos. Un desacuerdo
significa al menos un correcto y un incorrecto; no hay aprobación por mayoría.
Los pasajes conservan por ahora la revisión compartida anterior.

Antes de activar este cambio se verificó que la base local no tenía decisiones
humanas ni eventos. No se convirtieron eventos históricos en votos independientes.
En una base con revisiones previas, definir una migración explícita antes de usar
el nuevo panel; no asumir que el historial de reemplazos son votos independientes.

La aceptación automática se mantiene separada de `validada`: se puede auditar,
pero nunca se cuenta como revisión humana. El panel Django de relaciones es de
solo lectura; las decisiones se guardan desde la mesa de revisión.

## Ficha y revisión por excepciones

`/validar/<id>/` abre una ficha por norma. Muestra el texto completo de la versión,
la comprobación automática, una propuesta temática con contexto y las relaciones
entrantes/salientes de toda la base. Los casos internos no se convierten en aprobaciones
obligatorias por cada artículo. La cola agrupa las dudas por norma.

`document-integrity-v2` compara texto visible de la fuente guardada con la extracción,
tolerando espacios; comprueba orden y contenido del articulado. Una aprobación humana
usa una clave con versión y hashes de fuente, extracción y artículos. Puede reutilizarse
para el mismo contenido en otro lote, pero nunca para texto cambiado. Una comprobación
automática no se almacena como voto humano. Las aprobaciones antiguas de identidad o
partes se conservan con su alcance original.

`review_drafts` guarda avance privado por usuario y raíz, con protección por revisión,
recuperación en la pestaña y confirmación transaccional en «Resumen y finalizar».
Los resultados automáticos no trabajados no se exigen en el resumen. Las correcciones
se guardan separadas del modelo y no reemplazan el texto ni entrenan al clasificador.

`norm_references` conserva la dirección, evidencia, versión, artículo y resolución de
una cita o efecto explícito. Las referencias sin destino esperan nuevas descargas.
Los vínculos posteriores no modifican la aprobación del texto anterior. Los enlaces
generales de InfoLEG no se presentan como modificaciones de artículo comprobadas.

Al actualizar, ejecutar **init con la imagen nueva** antes de recrear web/crawler.
El SQL es idempotente. El inicio consolida la cola antigua sin borrar votos, eventos
ni borradores; archiva el progreso que tenía alcance por artículo o identidad.
Para aplicar la extracción y clasificación nuevas al lote ya descargado, elegir
**Procesar un lote descargado** en Gestión. No vuelve a acceder a InfoLEG; conserva
versiones anteriores y el historial documental cuando cambia la extracción.

Las materias automáticas necesitan control de calidad; una propuesta por vocabulario
se identifica como tal y carece de probabilidad. Un puntaje de Laya no garantiza acierto.
Las fuentes incompletas o PDF no procesados requieren completar el procesamiento.

## Verificación

```bash
docker compose exec web python manage.py test --noinput
```

Las pruebas usan una base separada: roles, votos independientes y desacuerdos,
motivos obligatorios, historial, formularios desactualizados, reapertura,
cambio de nombre de usuario, aislamiento entre lotes/pasajes, escape HTML y CSRF.

## Lote piloto 2 · IDs 151–200

Procesado el 22/10/2026: 39 índices con texto original, 7 con texto actualizado,
1 con solo ficha y 3 sin ficha reconocible (155, 156 y 193). No hubo errores de
descarga ni de extracción reportados por el crawler; esto no confirma calidad.

Sobre 537 artículos de las versiones seleccionadas se generaron 652 pasajes:
585 requieren revisión, 7 tienen aceptación automática y 60 no presentan cambio
detectado según el filtro. Se agregan los 50 controles de índice: **635 tareas
pendientes de revisión humana**. Al preparar el lote no se registró ninguna
validación humana.

496 pasajes quedaron por debajo del umbral 0,75 y 138 tienen texto dividido;
los motivos se superponen. El piloto todavía deriva gran parte del trabajo a
personas: el puntaje del modelo no es una tasa de acierto calibrada. Los 67
resultados automáticos también están disponibles para auditar.

Se verificó una segunda ejecución sin duplicados (652 pasajes ya existentes).

## Descargas desde Gestión

Abrir `/gestion/descargas/` con un administrador, indicar el ID inicial y final
(inclusivos, de 1 a 1.000 índices por solicitud) y pulsar **Encolar descarga**.
El detalle muestra etapa, índices procesados y errores; se actualiza cada 10
segundos mientras el trabajo está activo. El historial conserva quién lo pidió.
Un doble envío del mismo formulario devuelve el mismo trabajo.

Los servicios `web`, `crawler` y `db` corren separados. `init` aplica el esquema
idempotente antes del arranque, tanto en instalaciones nuevas como existentes.
La cola vive en PostgreSQL y el trabajador procesa un lote por vez. Cerrar el
navegador no lo detiene. Si el contenedor se interrumpe, el siguiente arranque
marca el trabajo inconcluso como fallido; las solicitudes en cola permanecen.
Para reintentar, enviar nuevamente el rango. Las revisiones anteriores no se borran.

El trabajador ejecuta descarga → parser determinista → controles de índice.
**Laya no está incluido en esta imagen**: la clasificación de pasajes conserva
el comando independiente documentado en `tools/infoleg_index/README.md`.
`review_prepared_at` solo se completa después de ese procesamiento completo.

```bash
# Desde tools/infoleg_index: construir/actualizar y arrancar todo
docker compose up -d --build
# Ver el trabajador y los servicios
docker compose logs --tail=50 crawler
docker compose ps
# Pausar el trabajador (puede interrumpir un lote activo)
docker compose stop crawler
docker compose start crawler
```

Las imágenes `juris-ai-web:local` y `juris-ai-crawler:local` incluyen el código:
no necesitan carpetas del host montadas. Al editar código hay que reconstruir.
Los datos permanecen en el volumen `juris_ai_pgdata`; no usar `down -v` para
actualizar. El archivo privado de acceso local y las vistas previas quedan fuera
de las imágenes. La configuración actual está destinada a pruebas en la red local.

## HTTPS detrás de Cloudflare Tunnel

La imagen permite configurar `DJANGO_CSRF_TRUSTED_ORIGINS` (orígenes con esquema,
separados por comas), `DJANGO_TRUST_PROXY_HEADERS=true` (confía en
`X-Forwarded-Proto: https`) y `DJANGO_SECURE_COOKIES=true` (cookies de sesión y
CSRF solo por HTTPS). Por defecto las tres opciones están desactivadas/vacías
para conservar el acceso HTTP local. El origen debe aceptar el tráfico del proxy
confiable, sin exposición directa a Internet al confiar en su header.

Ejemplo: host `jurisai.farhouse.org`, origen CSRF `https://jurisai.farhouse.org`.
El Compose debe pasar estas variables a `web.environment`. Hacer pull de la
imagen corregida y recrear web; conservar claves, usuarios y volumen. Recargar
el formulario de login. No se desactiva CSRF ni se usa un comodín de orígenes.
Las pruebas reproducen el 403 anterior y verifican login web/admin, rechazo de
origen ajeno o token ausente, cookies seguras y compatibilidad HTTP local.
