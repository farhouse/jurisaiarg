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
