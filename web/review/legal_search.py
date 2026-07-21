import re
import unicodedata
from dataclasses import dataclass


ACTION_TERMS = {
    "deroga": ("deroga", "derogacion", "derogado", "derogada", "derogase"),
    "sustituye": ("sustituye", "sustitucion", "sustituido", "reemplaza"),
    "incorpora": ("incorpora", "incorporacion", "agrega", "anade"),
    "modifica": ("modifica", "modificacion", "reforma"),
}


def normalize(value):
    value = unicodedata.normalize("NFKD", value.lower())
    return "".join(char for char in value if not unicodedata.combining(char))


@dataclass(frozen=True)
class ParsedLegalQuery:
    original: str
    text: str
    action: str | None = None
    article: str | None = None

    @property
    def filters(self):
        result = []
        if self.action:
            result.append(("Tipo de cambio", self.action.title()))
        if self.article:
            result.append(("Artículo", self.article))
        return result


def parse_legal_query(query):
    normalized = normalize(query)
    action = None
    consumed = []
    for canonical, terms in ACTION_TERMS.items():
        matched = next((term for term in terms if re.search(rf"\b{re.escape(term)}\w*\b", normalized)), None)
        if matched:
            action = canonical
            consumed.append(rf"\b{re.escape(matched)}\w*\b")
            break

    article_match = re.search(r"\b(?:articulo|art\.?)[\sº°nro\.]*([0-9]+(?:\s*(?:bis|ter|quater))?)\b", normalized)
    article = article_match.group(1).strip() if article_match else None
    if article_match:
        consumed.append(re.escape(article_match.group(0)))

    remaining = normalized
    for pattern in consumed:
        remaining = re.sub(pattern, " ", remaining)
    remaining = re.sub(r"[^a-z0-9]+", " ", remaining)
    remaining = re.sub(r"\s+", " ", remaining).strip()
    return ParsedLegalQuery(query, remaining, action, article)
