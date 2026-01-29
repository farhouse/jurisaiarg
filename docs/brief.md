# Brief — JurisAI (AR · Federal)

## 0) Propósito
Construir un sistema que **analice sentencias** y produzca:
- **Estructura** (metadata + outcome + fundamentos)
- **Métricas por juez** (y por fuero/cámara/tribunal)
- **Patrones/inclinaciones** interpretables (ej. pro-demandante vs pro-demandado; concede costas “altas”; plazos largos; estilo de fundamentación; frecuencia/estilo de citas normativas)

> Nota: en Argentina los jueces deben **citar normas** y exponer interpretación; eso habilita features robustas (no solo “sentimiento”).

## 1) Alcance inicial
- Jurisdicción: **Argentina**
- Nivel: **Federal** (inicial)
- Artefacto de esta fase: documento de **parametrizaciones** + supuestos + plan de ingesta.

## 2) Parametrizaciones (taxonomía)
### 2.1 Dimensiones “legales”
- **Jurisdicción**: nacional | provincial | CABA
- **Nivel**: federal | provincial
- **Fuero** (ejemplos, a completar):
  - Civil y Comercial Federal
  - Contencioso Administrativo Federal
  - Penal Económico
  - Criminal y Correccional Federal
  - Laboral
  - Seguridad Social
  - Electoral
  - Tributario (según estructura)
- **Instancia**: 1ra instancia | cámara | CSJN (si aplica)
- **Órgano**: juzgado | tribunal | cámara (sala)
- **Sede/Competencia territorial**: (a definir)

### 2.2 Entidades
- **Juez/Jueza**: nombre normalizado + posibles variantes
- **Secretaría** (opcional)
- **Cámara / Sala** (si aplica)

### 2.3 Tipo de proceso / pieza
- Tipo de proceso (amparo, cautelar, ejecución, etc.) — *pendiente*
- Tipo de documento: sentencia definitiva | interlocutoria | cautelar | resolución

## 3) Qué medimos (features)
### 3.1 Resultado (outcome)
- **Decisión principal**: concede | rechaza | parcialmente
- **Medidas**: cautelar sí/no; tipo; alcance
- **Costas**: a cargo de quién; criterios; (cuantificación si aparece)
- **Plazos**: plazos ordenados (días/hábiles) y vencimientos relativos

### 3.2 Fundamentación
- **Normas citadas** (ley/decreto/constitución/tratados; artículos)
- **Jurisprudencia citada** (CSJN/cámaras; identificadores)
- **Estilo de cita**: densidad; longitud; estructura
- **Interpretación**: heurísticas (p.ej. textualista vs finalista) — *posible fase 2*

### 3.3 “Inclinación” / patrones
No empezar con una etiqueta ideológica dura. Empezar con:
- **Ejes comparables**:
  - pro-demandante vs pro-demandado (definir por tipo de proceso)
  - “costas altas” vs “costas estándar” (necesita baseline por fuero)
  - plazos largos vs cortos
  - cautelar-friendly vs cautelar-restrictivo
  - densidad de citas normativas / jurisprudenciales
- **Descubrimiento**:
  - clustering por vectores de features
  - outliers por juez vs distribución del fuero

## 4) Datos (fuentes) — hipótesis
### 4.1 Infoleg
Infoleg (leyes/decretos) sirve para:
- resolver referencias a normas
- enriquecer metadata de normas

Pero **no es** la fuente típica de sentencias.

### 4.2 Sentencias/jurisprudencia (pendiente definir)
Objetivo: encontrar fuentes con filtros por **fuero / tribunal / juez / fecha**.
Posibles líneas (a confirmar):
- CIJ / PJN / sitios de cámaras
- SAIJ (jurisprudencia) u otros repositorios
- Boletines oficiales con fallos (según caso)
- Scraping por organismo

## 5) Estrategia de ingesta (futuro)
Pipeline propuesto:
1) Descarga (HTML/PDF) + hash
2) Extracción texto + limpieza (OCR si hace falta)
3) Segmentación: VISTOS / CONSIDERANDO / RESUELVO (heurísticas por regex)
4) Extracción estructurada (LLM + reglas)
5) Persistencia (JSON + DB)

## 6) Esquema mínimo de datos (sentencia)
Campos base sugeridos:
- `id` (hash)
- `source` (url + provider)
- `jurisdiction`, `level`, `fuero`, `instancia`
- `tribunal` (nombre), `sala` (opcional)
- `juez` (string normalizado)
- `fecha`
- `caratula` / `expediente` (si existe)
- `texto`
- `segments`: { vistos, considerando, resuelvo }
- `extraction`:
  - `outcome`
  - `costas`
  - `plazos`[]
  - `normas_citadas`[]
  - `jurisprudencia_citada`[]

## 7) Evaluación (cómo sabemos que anda)
- extracción: precisión/recall sobre un set etiquetado
- consistencia: mismo documento → mismo resultado
- utilidad: reporte por juez que un abogado considere razonable

## 8) Próximos pasos inmediatos
1) Completar lista de **fueros federales** y estructura (tribunales/cámaras) para AR.
2) Identificar **fuente de sentencias** viable (scrape/API/manual export).
3) Definir 20–50 sentencias “semilla” para bootstrap de pipeline.

---

Checklist de preguntas para completar este brief:
- ¿Querés incluir CABA o solo federal? 
- ¿Fueros prioritarios para arrancar? 
- ¿Preferís almacenar en Postgres o empezar con JSON/Parquet? 
