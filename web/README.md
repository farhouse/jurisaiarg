# JurisAI Review

Web interna para validar o rechazar los cambios jurídicos extraídos desde
InfoLEG.

## Inicio local

Desde `tools/infoleg_index/`:

```bash
docker compose up -d --build web
docker compose exec web python manage.py createsuperuser
```

Abrir `http://localhost:8000/` e ingresar con el usuario creado.

## Arquitectura

- Django administra usuarios, permisos y sesiones.
- Los modelos jurídicos usan `managed = False`: Django no crea ni modifica las
  tablas del crawler/parser.
- Las revisiones actualizan `review_status`, `reviewed_at`, `reviewed_by` y
  `review_notes` en `legal_changes`.

## Pantallas

- `/`: resumen del estado de revisión.
- `/changes/`: cola con filtros, búsqueda y paginación.
- `/changes/<id>/`: evidencia, contexto y formulario de revisión.
- `/admin/`: administración y acciones masivas.
