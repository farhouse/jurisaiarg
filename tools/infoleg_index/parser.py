"""Parsers tolerantes para páginas de InfoLEG.

El HTML histórico no es homogéneo. Por eso se preserva siempre la fuente cruda
y este módulo sólo produce una primera representación estructurada.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from html import unescape
from urllib.parse import urljoin

from bs4 import BeautifulSoup


PARSER_VERSION = "infoleg-html-v1"
BASE_URL = "https://servicios.infoleg.gob.ar/infolegInternet/"

MONTHS = {
    "jan": 1, "ene": 1, "feb": 2, "mar": 3, "apr": 4, "abr": 4,
    "may": 5, "jun": 6, "jul": 7, "aug": 8, "ago": 8, "sep": 9,
    "oct": 10, "nov": 11, "dec": 12, "dic": 12,
}


def normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def normalize_text(value: str) -> str:
    return normalize_ws(value).replace("\u00ad", "")


def optional_text(value: str | None) -> str | None:
    normalized = normalize_text(value or "")
    return normalized or None


def text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    match = re.search(r"(\d{1,2})-([a-záéíóú]{3})-(\d{4})", value.lower())
    if not match:
        return None
    month_key = unicodedata.normalize("NFKD", match.group(2)).encode("ascii", "ignore").decode()[:3]
    month = MONTHS.get(month_key)
    if not month:
        return None
    return dt.date(int(match.group(3)), month, int(match.group(1)))


@dataclass
class NormCard:
    norm_type: str | None
    number: str | None
    issuing_body: str | None
    canonical_title: str | None
    subject: str | None
    summary: str | None
    sanction_date: dt.date | None
    publication_date: dt.date | None
    original_url: str | None
    current_url: str | None
    modifies_url: str | None
    modified_by_url: str | None


def _identity(value: str) -> tuple[str | None, str | None, str | None]:
    value = normalize_text(value)
    match = re.match(
        r"(Decreto\s+(?:DNU|Reglamentario)|Ley|Decreto|Resoluci[oó]n|Disposici[oó]n)\s+([\d./-]+)\s*(.*)",
        value,
        re.IGNORECASE,
    )
    if not match:
        return None, None, value or None
    return match.group(1).title(), match.group(2), match.group(3) or None


def parse_card(html: str, page_url: str) -> NormCard:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find(id="Textos_Completos") or soup
    strong = container.find("strong")
    norm_type, number, issuing_body = _identity(strong.get_text(" ") if strong else "")
    title_node = container.select_one("span.destacado")
    subject_node = container.find("h1")
    date_node = container.select_one("span.vr_azul11")

    all_text = container.get_text(" ", strip=True)
    summary_match = re.search(r"(?:Resumen|Sumario)\s*:\s*(.*?)(?=Texto completo|Texto actualizado|Esta norma|$)", all_text, re.I)
    publication_match = re.search(r"Publicada en el Bolet[ií]n Oficial del\s+(\d{1,2}-[a-záéíóú]{3}-\d{4})", all_text, re.I)

    links: dict[str, str] = {}
    for anchor in container.find_all("a", href=True):
        label = normalize_text(anchor.get_text(" ")).lower()
        href = urljoin(page_url, anchor["href"])
        if "texto completo" in label:
            links["original"] = href
        elif "texto actualizado" in label:
            links["current"] = href
        elif "modifica o complementa a" in label:
            links["modifies"] = href
        elif "complementada o modificada por" in label:
            links["modified_by"] = href

    return NormCard(
        norm_type=norm_type,
        number=number,
        issuing_body=issuing_body,
        canonical_title=optional_text(title_node.get_text(" ") if title_node else None),
        subject=optional_text(subject_node.get_text(" ") if subject_node else None),
        summary=optional_text(summary_match.group(1) if summary_match else None),
        sanction_date=parse_date(date_node.get_text(" ") if date_node else None),
        publication_date=parse_date(publication_match.group(1)) if publication_match else None,
        original_url=links.get("original"),
        current_url=links.get("current"),
        modifies_url=links.get("modifies"),
        modified_by_url=links.get("modified_by"),
    )


ARTICLE_MARKER = re.compile(
    r"(?m)^(?:ART[IÍ]CULO|Art[ií]culo|Art\.)\s*"
    r"([0-9]+(?:\s*\.?\s*(?:BIS|Bis|bis|TER|Ter|ter|QU[AÁ]TER|Qu[aá]ter|qu[aá]ter))?)"
    r"\s*[º°o]?\s*[-—–.]?\s*"
)


def parse_articles(html: str) -> tuple[str, list[dict[str, str | int]]]:
    # Los textos actualizados suelen anexar una sección histórica que vuelve a
    # mencionar muchos artículos. Se conserva en la fuente cruda, pero no debe
    # confundirse con el articulado vigente.
    antecedents = re.search(r">\s*Antecedentes\s+Normativos\s*<", html, re.IGNORECASE)
    if antecedents:
        html = html[:antecedents.start()]
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "header", "footer"]):
        node.decompose()
    raw_text = soup.get_text("\n")
    lines = [normalize_ws(line) for line in raw_text.splitlines()]
    full_text = "\n".join(line for line in lines if line)
    matches = list(ARTICLE_MARKER.finditer(full_text))
    articles: list[dict[str, str | int]] = []
    by_key: dict[str, dict[str, str | int]] = {}
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(full_text)
        label = normalize_text(match.group(1)).lower().replace(".", "")
        label = normalize_ws(label)
        body = normalize_text(full_text[match.start():end])
        key = re.sub(r"\s+", "_", label)
        if key in by_key:
            # Algunas páginas repiten el rótulo dentro del mismo bloque (p.ej.
            # Art. 83 / ARTICULO 83). Conservamos ambos fragmentos sin crear una
            # identidad de artículo duplicada.
            by_key[key]["text"] = normalize_text(str(by_key[key]["text"]) + " " + body)
            continue
        article = {
            "label": label,
            "key": key,
            "ordinal": len(articles) + 1,
            "text": body,
        }
        articles.append(article)
        by_key[key] = article
    return full_text, articles


def parse_relation_rows(html: str) -> list[dict[str, str | int | dt.date | None]]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for row in soup.find_all("tr"):
        anchor = row.find("a", href=re.compile(r"verNorma\.do.*[?&]id=\d+"))
        cells = row.find_all("td")
        if not anchor or len(cells) < 3:
            continue
        id_match = re.search(r"[?&]id=(\d+)", anchor.get("href", ""))
        if not id_match:
            continue
        norm_type, number, issuing_body = _identity(cells[0].get_text(" "))
        description = normalize_text(cells[2].get_text(" "))
        rows.append({
            "infoleg_id": int(id_match.group(1)),
            "norm_type": norm_type,
            "number": number,
            "issuing_body": issuing_body,
            "publication_date": parse_date(cells[1].get_text(" ")),
            "description": description,
            "evidence": normalize_text(row.get_text(" ")),
        })
    return rows
