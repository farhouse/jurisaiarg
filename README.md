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

Desde **Gestión → Descargar índices** se ejecuta descarga, parser y preparación
de controles de índice en segundo plano. Laya se ejecuta por separado; ver
[la documentación del crawler](tools/infoleg_index/README.md) y
[la documentación de la aplicación](web/README.md).

## Imágenes en GitHub Container Registry

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
