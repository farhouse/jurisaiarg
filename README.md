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
