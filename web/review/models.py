from django.db import models
from django.contrib.postgres.search import SearchVectorField


class Norm(models.Model):
    id = models.BigAutoField(primary_key=True)
    infoleg_id = models.IntegerField(null=True)
    norm_type = models.TextField(null=True)
    number = models.TextField(null=True)
    canonical_title = models.TextField(null=True)
    subject = models.TextField(null=True)
    summary = models.TextField(null=True)
    year = models.IntegerField(null=True)
    popular_name = models.TextField(null=True)
    issuing_body = models.TextField(null=True)
    general_status = models.TextField()
    index_status = models.TextField()
    article_count = models.IntegerField()

    class Meta:
        managed = False
        db_table = "norms"
        ordering = ["infoleg_id"]

    @property
    def display_title(self):
        return self.canonical_title or self.subject or self.summary or "Sin título"

    @property
    def display_reference(self):
        return " ".join(filter(None, (self.norm_type, self.number))) or f"InfoLEG {self.infoleg_id}"

    @property
    def infoleg_url(self):
        return f"https://servicios.infoleg.gob.ar/infolegInternet/verNorma.do?id={self.infoleg_id}"

    def __str__(self):
        return f"{self.display_reference} — {self.display_title}"


class Article(models.Model):
    id = models.BigAutoField(primary_key=True)
    norm = models.ForeignKey(Norm, models.DO_NOTHING, db_column="norm_id", related_name="articles")
    article_label = models.TextField()
    article_key = models.TextField()
    ordinal = models.IntegerField(null=True)
    heading = models.TextField(null=True)

    class Meta:
        managed = False
        db_table = "articles"

    def __str__(self):
        return f"Art. {self.article_label}"


class ArticleVersion(models.Model):
    id = models.BigAutoField(primary_key=True)
    article = models.ForeignKey(Article, models.DO_NOTHING, db_column="article_id")
    text_original = models.TextField()
    text_normalized = models.TextField()
    status = models.TextField()
    valid_from = models.DateField(null=True)
    valid_to = models.DateField(null=True)
    norm_version = models.ForeignKey(
        "NormVersion", models.DO_NOTHING, db_column="norm_version_id",
        related_name="article_versions",
    )

    class Meta:
        managed = False
        db_table = "article_versions"


class NormDate(models.Model):
    norm = models.ForeignKey(Norm, models.DO_NOTHING, db_column="norm_id", related_name="dates")
    date_type = models.TextField()
    date_value = models.DateField()
    precision = models.TextField()

    class Meta:
        managed = False
        db_table = "norm_dates"


class NormVersion(models.Model):
    norm = models.ForeignKey(Norm, models.DO_NOTHING, db_column="norm_id", related_name="versions")
    version_type = models.TextField()
    valid_from = models.DateField(null=True)
    valid_to = models.DateField(null=True)
    is_current = models.BooleanField()
    source = models.ForeignKey('NormSource', models.DO_NOTHING, null=True)
    full_text_original = models.TextField(null=True)

    class Meta:
        managed = False
        db_table = "norm_versions"


class SearchChunk(models.Model):
    norm = models.ForeignKey(Norm, models.DO_NOTHING, db_column="norm_id", related_name="search_chunks")
    content = models.TextField()
    chunk_type = models.TextField()
    search_vector = SearchVectorField()
    article_version = models.ForeignKey(
        ArticleVersion, models.DO_NOTHING, db_column="article_version_id", null=True,
    )
    content_hash = models.TextField(null=True)
    embedding_model = models.TextField(null=True)
    embedding_version = models.TextField(null=True)
    embedded_at = models.DateTimeField(null=True)

    class Meta:
        managed = False
        db_table = "search_chunks"


class LegalChange(models.Model):
    REVIEW_CHOICES = [
        ("pendiente", "Pendiente de revisión"),
        ("validada", "Relación confirmada"),
        ("rechazada", "Relación incorrecta"),
    ]

    id = models.BigAutoField(primary_key=True)
    amending_norm = models.ForeignKey(
        Norm, models.DO_NOTHING, db_column="amending_norm_id", null=True,
        related_name="changes_made",
    )
    affected_norm = models.ForeignKey(
        Norm, models.DO_NOTHING, db_column="affected_norm_id",
        related_name="changes_received",
    )
    affected_article = models.ForeignKey(
        Article, models.DO_NOTHING, db_column="affected_article_id",
        related_name="legal_changes",
    )
    source_article_version = models.ForeignKey(
        ArticleVersion, models.DO_NOTHING, db_column="source_article_version_id",
    )
    amending_reference_raw = models.TextField()
    amending_article_reference = models.TextField(null=True)
    change_type = models.TextField()
    affected_scope = models.TextField()
    publication_date = models.DateField(null=True)
    effect_date = models.DateField(null=True)
    evidence_text = models.TextField()
    parser_version = models.TextField()
    confidence = models.DecimalField(max_digits=4, decimal_places=3)
    review_status = models.TextField(choices=REVIEW_CHOICES)
    reviewed_at = models.DateTimeField(null=True)
    reviewed_by = models.TextField(null=True)
    review_notes = models.TextField(null=True)
    fingerprint = models.TextField()
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "legal_changes"
        ordering = ["-confidence", "id"]

    def __str__(self):
        return f"{self.change_type}: {self.affected_norm.display_reference} art. {self.affected_article.article_label}"


class CrawlRun(models.Model):
    from django.contrib.postgres.fields import ArrayField

    requested_ids = ArrayField(models.IntegerField(), null=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True)
    status = models.TextField()
    processed_count = models.IntegerField()
    notes = models.TextField(null=True)
    review_prepared_at = models.DateTimeField(null=True)

    class Meta:
        managed = False
        db_table = "crawl_runs"
        ordering = ["-id"]

    @property
    def label(self):
        ids = self.requested_ids or []
        return f"Lote {self.pk} · IDs {min(ids)}–{max(ids)}" if ids else f"Lote {self.pk}"


class CrawlItem(models.Model):
    crawl_run = models.ForeignKey(CrawlRun, models.DO_NOTHING, related_name="items")
    infoleg_id = models.IntegerField()
    norm = models.ForeignKey(Norm, models.DO_NOTHING, null=True)
    status = models.TextField()
    error_detail = models.TextField(null=True)

    class Meta:
        managed = False
        db_table = "crawl_items"


class CrawlJob(models.Model):
    MODES = [('full', 'Descargar y procesar'), ('download', 'Solo descargar'), ('process', 'Procesar un lote descargado')]
    mode = models.TextField(default='download', choices=MODES)
    progress = models.JSONField(default=dict)
    heartbeat_at = models.DateTimeField(null=True)
    last_activity_at = models.DateTimeField(null=True)
    submission_id = models.UUIDField(unique=True)
    start_id = models.IntegerField()
    end_id = models.IntegerField()
    requested_by = models.TextField()
    status = models.TextField(default="queued", choices=[
        ("queued", "En cola"), ("running", "En curso"), ("done", "Finalizado"),
        ("partial", "Finalizado con errores de descarga"), ("failed", "Interrumpido o fallido"),
    ])
    stage = models.TextField(default="queued", choices=[
        ("queued", "Esperando al trabajador"), ("download", "Descargando índices"),
        ("parser", "Extrayendo relaciones"), ("classify", "Clasificando materias y pasajes"),
        ("embeddings", "Preparando búsqueda semántica"), ("review", "Preparando revisión"),
        ("finished", "Etapas solicitadas terminadas"),
    ])
    crawl_run = models.ForeignKey(CrawlRun, models.DO_NOTHING, null=True)
    created_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True)
    finished_at = models.DateTimeField(null=True)
    error = models.TextField(default="")

    class Meta:
        managed = False
        db_table = "crawl_jobs"
        ordering = ["-id"]

    @property
    def total(self):
        return self.end_id - self.start_id + 1

    @property
    def active(self):
        return self.status in ("queued", "running")


class NormRelation(models.Model):
    source_norm = models.ForeignKey(Norm, models.DO_NOTHING, related_name='official_links_out')
    target_norm = models.ForeignKey(Norm, models.DO_NOTHING, related_name='official_links_in')
    relation_type = models.TextField()
    evidence_text = models.TextField()
    review_status = models.TextField(default='pendiente')

    class Meta:
        managed = False
        db_table = 'norm_relations'


class NormReference(models.Model):
    source_norm = models.ForeignKey(Norm, models.DO_NOTHING, related_name='references_made')
    source_version = models.ForeignKey(NormVersion, models.DO_NOTHING)
    source_article_version = models.ForeignKey(ArticleVersion, models.DO_NOTHING, null=True)
    target_norm = models.ForeignKey(Norm, models.DO_NOTHING, null=True, related_name='references_received')
    target_type = models.TextField()
    target_number = models.TextField()
    target_article = models.TextField(default='')
    relation_type = models.TextField()
    scope = models.TextField(default='norma')
    scope_detail = models.TextField(default='')
    evidence_text = models.TextField()
    resolution = models.TextField(default='pendiente')
    candidates = models.JSONField(default=list)
    assessment = models.JSONField(default=dict)
    review_status = models.TextField(default='pendiente')
    reviewed_by = models.TextField(null=True)
    reviewed_at = models.DateTimeField(null=True)
    review_notes = models.TextField(default='')
    fingerprint = models.TextField(unique=True)
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'norm_references'

    @property
    def target_reference(self):
        return self.target_norm.display_reference if self.target_norm_id else f'{self.target_type} {self.target_number}'


class ReviewTask(models.Model):
    crawl_run = models.ForeignKey(CrawlRun, models.DO_NOTHING)
    norm = models.ForeignKey(Norm, models.DO_NOTHING)
    article_version = models.ForeignKey(ArticleVersion, models.DO_NOTHING, null=True)
    legal_change = models.ForeignKey(LegalChange, models.DO_NOTHING, null=True)
    reference = models.ForeignKey(NormReference, models.DO_NOTHING, null=True)
    superseded = models.BooleanField(default=False)
    superseded_reason = models.TextField(default="")
    kind = models.TextField(choices=[("indice", "Calidad del índice"), ("pasaje", "Interpretación del texto")])
    source_text = models.TextField()
    decision = models.TextField(choices=[("revision", "Requiere revisión"), ("aceptacion_automatica", "Acuerdo automático"), ("sin_cambio_detectado", "Sin cambio detectado")])
    prediction = models.TextField(null=True)
    probability = models.FloatField(null=True)
    reasons = models.JSONField(default=list)
    model_result = models.JSONField(default=dict)
    fingerprint = models.TextField(unique=True)
    review_status = models.TextField(default="pendiente", choices=[("pendiente", "Pendiente"), ("validada", "Correcto"), ("rechazada", "Incorrecto")])
    review_notes = models.TextField(default="")
    proposed_correction = models.JSONField(default=dict)
    reviewed_by = models.TextField(null=True)
    reviewed_at = models.DateTimeField(null=True)
    revision = models.IntegerField(default=0)
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "review_tasks"

    @property
    def reasons_display(self):
        labels = {
            'copia_diferente':'La copia o su articulado presentan diferencias con la fuente',
            'materias_demasiado_amplias':'El modelo propuso demasiadas materias; hace falta comprobar el tema central',
            'materia_ambigua':'La materia jurídica no quedó resuelta con suficiente confianza',
            'materia_no_identificada':'No se identificó una materia para el contenido sustantivo',
            'referencia_ambigua':'La referencia coincide con varias normas del catálogo',
            'relacion_ambigua':'El efecto o los artículos afectados no están determinados',
            'catalogo_experimental': 'Revisar materias y conceptos propuestos; el catálogo está en evaluación',
            "control_del_indice": "Comprobar la ficha y el articulado descargado",
            "probabilidad_baja": "Probabilidad menor a 0,75",
            "desacuerdo_parser_laya": "El parser y Laya no coinciden",
            "sin_extraccion_estructurada": "Laya detecta un cambio que el parser no pudo extraer",
            "alcance_parcial_sin_identificar": "Falta identificar el inciso o párrafo exacto",
            "multiples_cambios": "El texto contiene varios cambios",
            "clasificacion_ambigua": "La clasificación es ambigua",
            "texto_truncado_o_no_verificado": "El modelo no pudo leer todo el texto",
            "norma_sin_resolver": "La referencia no se resolvió a una norma local única",
            "error_modelo": "Falló la clasificación; revisar el texto manualmente",
            "fragmento_dividido": "Artículo extenso dividido: revisar también su contexto",
        }
        return [labels.get(reason, reason.replace("_", " ").capitalize()) for reason in self.reasons]


class NormReview(models.Model):
    task = models.ForeignKey(ReviewTask, models.DO_NOTHING, related_name="votes")
    user_id = models.BigIntegerField()
    username = models.TextField()
    status = models.TextField(choices=ReviewTask._meta.get_field("review_status").choices)
    notes = models.TextField(default="")
    checks = models.JSONField(default=dict)
    revision = models.IntegerField(default=1)
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "norm_reviews"
        constraints = [models.UniqueConstraint(fields=["task", "user_id"], name="norm_reviews_task_user_unique")]


class ReviewDraft(models.Model):
    task = models.ForeignKey(ReviewTask, models.DO_NOTHING)
    user_id = models.BigIntegerField()
    data = models.JSONField(default=dict)
    revision = models.IntegerField(default=0)
    updated_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = 'review_drafts'
        constraints = [models.UniqueConstraint(fields=['task', 'user_id'], name='review_drafts_task_user_unique')]


class ReviewEvent(models.Model):
    task = models.ForeignKey(ReviewTask, models.DO_NOTHING, related_name="events")
    username = models.TextField()
    previous_status = models.TextField()
    status = models.TextField()
    notes = models.TextField()
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField()

    class Meta:
        managed = False
        db_table = "review_events"


class NormSource(models.Model):
    norm = models.ForeignKey(Norm, models.DO_NOTHING)
    crawl_run = models.ForeignKey(CrawlRun, models.DO_NOTHING, null=True)
    url = models.TextField()
    document_kind = models.TextField()
    retrieved_at = models.DateTimeField()
    http_status = models.IntegerField(null=True)
    raw_content = models.TextField(null=True)

    class Meta:
        managed = False
        db_table = 'norm_sources'
